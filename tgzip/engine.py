import asyncio
import json
import threading
import time
from pathlib import Path
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from PySide6.QtCore import QObject, Signal
from .security import Vault, validate_keys, import_legacy_session
from .storage import Store, atomic_write, load_settings, safe_name, zip_candidate
from .transfer import download, TransferError


def error_text(exc):
    # Do not echo RPC request objects, credentials, QR tokens or arbitrary exception text.
    if isinstance(exc, TransferError):
        return str(exc)
    if isinstance(exc, errors.PasswordHashInvalidError):
        return 'Incorrect two-step verification password. Try again.'
    if isinstance(exc, errors.ApiIdInvalidError):
        return 'Telegram rejected the API ID / API Hash. Check your credentials.'
    if isinstance(exc, errors.FloodWaitError):
        return f'Telegram requires a {exc.seconds}-second wait. Try again later.'
    if isinstance(exc, (OSError, ConnectionError, asyncio.TimeoutError)):
        return f'Network or local file operation failed ({type(exc).__name__}, code {getattr(exc, "errno", "n/a")}). Check connection, disk space and folder permissions.'
    return f'Operation failed ({type(exc).__name__}). Check your access and settings, then retry.'


class Bridge(QObject):
    event = Signal(str, object)

    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='Telegram worker', daemon=False)
        self.thread.start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = Engine(self.data_dir, self.event.emit)
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()
        self.event.emit('closed', None)

    def send(self, command, **kwargs):
        if not self.ready.is_set():
            raise RuntimeError('Worker is not ready')
        future = asyncio.run_coroutine_threadsafe(self.engine.command(command, **kwargs), self.loop)
        def report(result):
            try:
                result.result()
            except Exception as exc:
                self.event.emit('error', error_text(exc))
        future.add_done_callback(report)


class Engine:
    def __init__(self, data_dir, emit):
        self.data_dir, self.emit = data_dir, emit
        self.settings_path = data_dir / 'settings.json'
        self.vault = Vault(data_dir / 'credentials.dpapi')
        self.settings = load_settings(self.settings_path)
        self.secrets = {}
        self.store = Store(data_dir / 'queue.sqlite3')
        self.client = None
        self.account = None
        self.chat = None
        self.dialogs = {}
        self.task = None
        self.kind = None
        self.gate = asyncio.Event()
        self.gate.set()
        self.password_future = None
        self.closing = False
        self.control = asyncio.Lock()

    def save_settings(self):
        atomic_write(self.settings_path, json.dumps(self.settings, indent=2).encode('utf-8'))

    def snapshot(self):
        if self.account and self.chat:
            self.emit('queue', {'rows': self.store.jobs(self.account, self.chat, limit=1000),
                                'summary': self.store.summary(self.account, self.chat)})
        else:
            self.emit('queue', {'rows': [], 'summary': {'total': 0, 'bytes': 0, 'received': 0, 'complete': 0, 'failed': 0}})

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
        self.gate.set()

    def launch(self, kind, fn):
        if self.task and not self.task.done():
            self.emit('error', 'Another operation is still running. Stop it first.')
            return
        async def run():
            self.kind = kind
            self.emit('state', kind)
            try:
                await fn()
            except asyncio.CancelledError:
                self.emit('log', 'Operation stopped. Completed files and partial downloads were kept.')
            except Exception as exc:
                self.emit('error', error_text(exc))
            finally:
                self.kind = None
                self.emit('state', 'idle')
                self.snapshot()
        self.gate.set()
        self.task = asyncio.create_task(run())

    async def command(self, command, **args):
        if self.closing:
            return
        if command == 'password':
            if self.password_future and not self.password_future.done():
                self.password_future.set_result(args['value'])
            return
        if command == 'pause':
            if self.kind in {'downloading', 'scanning'}:
                self.gate.clear()
                self.emit('state', 'paused')
            return
        if command == 'resume':
            self.gate.set()
            if self.kind:
                self.emit('state', self.kind)
            return
        async with self.control:
            if command == 'boot':
                self.emit('settings', self.settings)
                self.secrets = self.vault.load()
                self.emit('configured', bool(self.secrets.get('api_id')))
                if self.secrets.get('api_id'):
                    self.launch('connecting', self.connect)
            elif command == 'configure':
                api_id, api_hash = validate_keys(args['api_id'], args['api_hash'])
                imported = import_legacy_session(args['legacy']) if args.get('legacy') else None
                await self.cancel()
                if self.client:
                    self.persist_session()
                    await self.client.disconnect()
                    self.client = None
                session = imported if imported is not None else self.secrets.get('session', '')
                self.secrets = {'api_id': api_id, 'api_hash': api_hash, 'session': session}
                self.vault.save(self.secrets)
                self.emit('configured', True)
                self.launch('connecting', self.connect)
            elif command == 'connect':
                self.launch('connecting', self.connect)
            elif command == 'refresh':
                if self.account:
                    self.launch('refreshing', self.refresh)
            elif command == 'select':
                # Switching chats serializes cancellation and saves the old file first.
                await self.cancel()
                chat = int(args['chat'])
                if chat not in self.dialogs:
                    raise ValueError('Chat not accessible')
                self.chat = chat
                self.settings.setdefault('selected_chats', {})[str(self.account)] = chat
                self.save_settings()
                self.emit('selected', chat)
                self.emit('current', None)
                self.snapshot()
            elif command == 'folder':
                self.settings['download_dir'] = str(Path(args['path']).resolve())
                self.save_settings()
                self.emit('settings', self.settings)
            elif command == 'scan':
                if self.chat and self.account:
                    self.launch('scanning', self.scan)
            elif command == 'start':
                if self.chat and self.account:
                    self.launch('downloading', self.download_queue)
            elif command == 'stop':
                await self.cancel()
            elif command == 'logout':
                await self.cancel()
                if self.client:
                    await asyncio.wait_for(self.client.log_out(), 20)
                self.client = None
                self.secrets.pop('session', None)
                self.vault.save(self.secrets)
                self.account, self.chat, self.dialogs = None, None, {}
                self.emit('account', None)
                self.emit('dialogs', [])
                self.emit('current', None)
                self.snapshot()
            elif command == 'close':
                self.closing = True
                await self.cancel()
                try:
                    if self.client:
                        self.persist_session()
                        await asyncio.wait_for(self.client.disconnect(), 15)
                except Exception as exc:
                    self.emit('error', error_text(exc))
                finally:
                    self.store.close()
                    asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

    def persist_session(self):
        if self.client and self.client.session.auth_key:
            self.secrets['session'] = self.client.session.save()
            self.vault.save(self.secrets)

    async def connect(self):
        if not self.secrets.get('api_id'):
            self.emit('configured', False)
            return
        if self.client:
            await self.client.disconnect()
        self.account, self.chat, self.dialogs = None, None, {}
        self.emit('account', None)
        self.emit('dialogs', [])
        self.snapshot()
        self.client = TelegramClient(StringSession(self.secrets.get('session', '')),
                                     self.secrets['api_id'], self.secrets['api_hash'],
                                     device_model='Telegram ZIP Desktop', system_version='Windows',
                                     app_version='1.0.0', request_retries=2, connection_retries=3,
                                     retry_delay=2, timeout=15, flood_sleep_threshold=0)
        try:
            await asyncio.wait_for(self.client.connect(), 45)
            if not await self.client.is_user_authorized():
                self.emit('state', 'authorizing')
                qr = await self.client.qr_login()
                while True:
                    self.emit('qr', qr.url)
                    try:
                        await qr.wait(timeout=35)
                        break
                    except asyncio.TimeoutError:
                        await qr.recreate()
                    except errors.SessionPasswordNeededError:
                        self.emit('qr', None)
                        while True:
                            self.password_future = asyncio.get_running_loop().create_future()
                            self.emit('password', None)
                            password = await self.password_future
                            if password is None:
                                raise asyncio.CancelledError
                            try:
                                await self.client.sign_in(password=password)
                                break
                            except errors.PasswordHashInvalidError as exc:
                                self.emit('error', error_text(exc))
                            finally:
                                password = None
                                self.password_future = None
                        break
            self.persist_session()
            me = await self.client.get_me()
            self.account = me.id
            self.emit('account', {'name': ' '.join(filter(None, [me.first_name, me.last_name])), 'id': me.id})
            await self.refresh()
        finally:
            self.emit('qr', None)

    async def refresh(self):
        dialogs = {}
        # folder=None includes archived and non-archived dialogs.
        async for dialog in self.client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                dialogs[dialog.id] = dialog
        self.dialogs = dialogs
        rows = [{'id': d.id, 'name': d.name, 'kind': 'Group' if d.is_group else 'Channel'} for d in dialogs.values()]
        rows.sort(key=lambda row: row['name'].casefold())
        self.emit('dialogs', rows)
        previous = self.settings.get('selected_chats', {}).get(str(self.account))
        self.chat = previous if previous in dialogs else None
        if previous and not self.chat:
            self.emit('log', 'The saved chat is not currently accessible. Choose a chat from the list.')
        self.emit('selected', self.chat)
        self.emit('current', None)
        self.snapshot()

    async def scan(self):
        account, chat = self.account, self.chat
        folder = self.settings.get('download_dir')
        if not folder:
            raise TransferError('Choose a download folder first.')
        root = Path(folder).resolve() / f'account_{account}' / f'chat_{chat}'
        cursor = self.store.cursor(account, chat)
        count, last = 0, 0
        async for message in self.client.iter_messages(self.dialogs[chat].input_entity, min_id=cursor, reverse=True):
            await self.gate.wait()
            item = None
            doc = message.document
            if doc and message.file and zip_candidate(message.file.name, doc.mime_type):
                name = safe_name(message.file.name or f'archive_{message.id}.zip')
                item = {'document': doc.id, 'name': name, 'size': doc.size,
                        'path': root / f'{message.id}_{doc.id}_{name}'}
            self.store.scanned(account, chat, message.id, item)
            count += 1
            now = time.monotonic()
            if now - last > .5:
                self.emit('scan', count)
                self.snapshot()
                last = now
        self.emit('log', f'Scan complete: checked {count:,} new messages. ZIP queue saved.')

    async def download_queue(self):
        account, chat = self.account, self.chat
        after = 0
        while (job := self.store.next_job(account, chat, after)) is not None:
            await self.gate.wait()
            after = job['message']
            self.emit('current', job)
            self.store.update(account, chat, after, state='downloading', error='')
            def progress(done, speed):
                self.store.update(account, chat, job['message'], done=done)
                self.emit('progress', {'done': done, 'size': job['size'], 'speed': speed,
                                       'message': job['message'], 'summary': self.store.summary(account, chat)})
            try:
                for attempt in range(4):
                    try:
                        await self.gate.wait()
                        # Fresh message refreshes expired file references on every retry/resume.
                        message = await self.client.get_messages(self.dialogs[chat].input_entity, ids=job['message'])
                        if not message or not message.document:
                            raise TransferError('Message was deleted or the attachment is no longer accessible.')
                        if str(message.document.id) != job['document'] or message.document.size != job['size']:
                            raise TransferError('The attachment changed since scanning. The old partial file was preserved.')
                        await download(self.client, message, job, self.gate, progress)
                        break
                    except errors.FloodWaitError as exc:
                        self.emit('log', f'Telegram rate limit: waiting {exc.seconds} seconds. Stop remains available.')
                        self.emit('speed_reset', None)
                        await asyncio.sleep(exc.seconds)
                        if attempt == 3:
                            raise
                    except (ConnectionError, asyncio.TimeoutError, errors.FileReferenceExpiredError, errors.ServerError) as exc:
                        if attempt == 3:
                            raise
                        self.emit('log', f'Connection retry {attempt + 1}/3 ({type(exc).__name__}). Partial file saved.')
                        self.emit('speed_reset', None)
                        await asyncio.sleep(2 ** (attempt + 1))
                self.store.update(account, chat, after, done=job['size'], state='done', error='')
            except asyncio.CancelledError:
                self.store.update(account, chat, after, state='queued')
                raise
            except Exception as exc:
                reason = error_text(exc)
                self.store.update(account, chat, after, state='error', error=reason)
                self.emit('log', f'File #{after}: {reason}')
                # Disk errors should not cause the rest of the queue to fail in a loop.
                if isinstance(exc, OSError) and not isinstance(exc, ConnectionError):
                    raise
            self.snapshot()
        self.emit('log', 'Queue pass complete. Failed files can be retried with Start.')
