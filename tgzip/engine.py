import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import time
from pathlib import Path
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from PySide6.QtCore import QObject, Signal
from .security import Vault, validate_keys, import_legacy_session
from .storage import parallel_downloads, Store, atomic_write, load_settings, safe_name, archive_extension
from .transfer import download, TransferError
from .archive_check import check_archive, find_winrar, quarantine_destination, move_to_quarantine
from updater import UpdateError, prepare_update, load_plan


def error_text(exc):
    # Do not echo RPC request objects, credentials, QR tokens or arbitrary exception text.
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    if isinstance(exc, (TransferError, UpdateError)):
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
        self.transfers = {}

    def save_settings(self):
        atomic_write(self.settings_path, json.dumps(self.settings, indent=2).encode('utf-8'))

    def snapshot(self):
        if self.account and self.chat:
            for job in self.store.pending_quarantines(self.account, self.chat):
                self.recover_quarantine(job)
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
            if self.kind in {'downloading', 'scanning', 'verifying'}:
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
                self.emit('checker', self.checker())
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
            elif command == 'parallel':
                if self.task and not self.task.done():
                    raise TransferError('Stop the current operation before changing parallel downloads.')
                self.settings['parallel_downloads'] = parallel_downloads(args['value'])
                self.save_settings()
                self.emit('settings', self.settings)
            elif command == 'scan':
                if self.chat and self.account:
                    self.launch('scanning', self.scan)
            elif command == 'start':
                if self.chat and self.account:
                    self.launch('downloading', self.download_queue)
            elif command == 'checker':
                if self.task and not self.task.done():
                    raise TransferError('Stop the current operation before changing WinRAR.')
                path = find_winrar(args['path'])
                if not path:
                    raise TransferError('Select the installed WinRAR.exe on Windows. Rar.exe is not supported.')
                self.settings['winrar_path'] = str(path)
                self.save_settings()
                self.emit('checker', path)
            elif command == 'verify':
                if self.chat and self.account:
                    self.launch('verifying', self.verify_queue)
            elif command == 'update':
                self.launch('updating', self.update_application)
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

    async def update_application(self):
        cancel = threading.Event()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='Application update') as executor:
            operation = asyncio.get_running_loop().run_in_executor(
                executor, prepare_update, Path(__file__).resolve().parents[1],
                lambda text: self.emit('update_progress', text), cancel)
            try:
                plan = await asyncio.shield(operation)
            except asyncio.CancelledError:
                cancel.set()
                result = await asyncio.gather(operation, return_exceptions=True)
                if isinstance(result[0], str):
                    import shutil
                    _, stage, _ = load_plan(result[0])
                    shutil.rmtree(stage, ignore_errors=True)
                raise
        if plan:
            self.emit('update_ready', plan)
        else:
            self.emit('log', 'You are up to date.')
            self.emit('update_progress', 'You are up to date.')

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
                                     device_model='Telegram Archive Desktop', system_version='Windows',
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
        if not cursor:
            self.emit('log', 'Scanning full history for ZIP and RAR archives. Existing queue entries are preserved.')
        count, last = 0, 0
        async for message in self.client.iter_messages(self.dialogs[chat].input_entity, min_id=cursor, reverse=True):
            await self.gate.wait()
            item = None
            doc = message.document
            extension = archive_extension(message.file.name, doc.mime_type) if doc and message.file else None
            if extension:
                name = safe_name(message.file.name or f'archive_{message.id}{extension}')
                item = {'document': doc.id, 'name': name, 'size': doc.size,
                        'path': root / f'{message.id}_{doc.id}_{name}'}
            self.store.scanned(account, chat, message.id, item)
            count += 1
            now = time.monotonic()
            if now - last > .5:
                self.emit('scan', count)
                self.snapshot()
                last = now
        self.emit('log', f'Scan complete: checked {count:,} messages. ZIP and RAR queue saved.')

    async def download_queue(self):
        executable = self.checker()
        self.emit('checker', executable)
        if not executable:
            self.emit('log', 'WinRAR is not configured. Downloads will remain Unchecked. Select WinRAR… and use Check archives later.')
        wake = asyncio.Event()
        finished = asyncio.Event()

        async def produce():
            await self.download_jobs(wake)
            finished.set()
            wake.set()
            if executable and self.kind == 'downloading':
                self.kind = 'verifying'
                if self.gate.is_set():
                    self.emit('state', 'verifying')

        # Both tasks belong to the same operation. Stop, chat switching and close
        # cancel and await them together, including the owned WinRAR process.
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(produce(), name='Archive downloads')
            if executable:
                tasks.create_task(self.verify_pending(wake, finished), name='Archive checks')
        self.emit('log', 'Queue pass complete. Failed downloads can be retried with Start; failed checks with Check archives.')

    async def verify_pending(self, wake, finished):
        account, chat = self.account, self.chat
        while True:
            await self.gate.wait()
            wake.clear()
            job = self.store.next_pending_check(account, chat)
            if job is not None:
                if not self.checker():
                    self.emit('checker', None)
                    self.emit('log', 'WinRAR is no longer available. Remaining archives were kept Unchecked.')
                    return
                await self.verify_job(job, background=True)
                await asyncio.sleep(0)
            elif finished.is_set():
                return
            else:
                await wake.wait()

    def emit_transfers(self):
        now = time.monotonic()
        summary = self.store.summary(self.account, self.chat)
        speed = sum(row['speed'] for row in self.transfers.values()
                    if now - row['updated'] < 4) if self.gate.is_set() else 0
        self.emit('transfers', {'active': len(self.transfers), 'speed': speed,
                               'summary': summary})

    async def download_jobs(self, wake):
        account, chat = self.account, self.chat
        after = 0

        async def worker():
            nonlocal after
            while True:
                await self.gate.wait()
                # Claim without yielding: all workers and SQLite share this event loop.
                # The cursor advances even on failure, so retries wait for the next Start.
                job = self.store.next_job(account, chat, after)
                if job is None:
                    return
                after = job['message']
                await self.download_job(job, wake, account, chat)

        self.emit_transfers()
        async with asyncio.TaskGroup() as tasks:
            for index in range(parallel_downloads(self.settings.get('parallel_downloads'))):
                tasks.create_task(worker(), name=f'Download {index + 1}')

    async def download_job(self, job, wake, account, chat):
        after = job['message']
        self.transfers[after] = {'done': job['done'], 'size': job['size'], 'speed': 0, 'updated': time.monotonic()}
        self.emit_transfers()
        self.emit('current', job)
        self.store.update(account, chat, after, state='downloading', error='')
        downloaded = False
        def progress(done, speed):
            self.store.update(account, chat, job['message'], done=done)
            self.transfers[after].update(done=done, speed=speed, updated=time.monotonic())
            self.emit_transfers()
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
                    progress(self.transfers[after]['done'], 0)
                    await asyncio.sleep(exc.seconds)
                    if attempt == 3:
                        raise
                except (ConnectionError, asyncio.TimeoutError, errors.FileReferenceExpiredError, errors.ServerError) as exc:
                    if attempt == 3:
                        raise
                    self.emit('log', f'Connection retry {attempt + 1}/3 ({type(exc).__name__}). Partial file saved.')
                    progress(self.transfers[after]['done'], 0)
                    await asyncio.sleep(2 ** (attempt + 1))
            self.store.update(account, chat, after, done=job['size'], state='done', error='',
                              verification='pending', verification_error='')
            downloaded = True
            # The durable pending state is the check queue. Notify its one
            # consumer, then continue downloading without awaiting WinRAR.
            wake.set()
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            if not downloaded:
                self.store.update(account, chat, after, state='queued')
            raise
        except Exception as exc:
            reason = error_text(exc)
            self.store.update(account, chat, after, state='error', error=reason)
            self.emit('log', f'File #{after}: {reason}')
            # Disk errors should not cause the rest of the queue to fail in a loop.
            if isinstance(exc, OSError) and not isinstance(exc, ConnectionError):
                raise
        finally:
            self.transfers.pop(after, None)
            self.emit_transfers()
        self.snapshot()

    def checker(self):
        return find_winrar(self.settings.get('winrar_path', ''))

    def recover_quarantine(self, job):
        path, destination = Path(job['path']), Path(job['quarantine_path'])
        try:
            if not path.exists() and not destination.is_symlink() and destination.is_file() and destination.stat().st_size == job['size']:
                self.store.update(self.account, self.chat, job['message'], verification='quarantined')
                return True
        except OSError:
            pass  # Keep the saved move plan for a later retry.
        return False

    async def verify_queue(self):
        executable = self.checker()
        self.emit('checker', executable)
        if not executable:
            raise TransferError('Install WinRAR for Windows, then select WinRAR… to locate WinRAR.exe. No archives were moved.')
        after = 0
        while (job := self.store.next_check_job(self.account, self.chat, after)) is not None:
            await self.gate.wait()
            after = job['message']
            await self.verify_job(job)
        self.emit('log', 'Archive check complete. See each file status and tooltip for the result.')

    async def verify_job(self, job, *, background=False):
        account, chat, message = self.account, self.chat, job['message']
        path = Path(job['path'])
        def update(**values):
            self.store.update(account, chat, message, **values)
        try:
            # The destination is committed before moving. Recover a process exit
            # between the filesystem move and the final database commit.
            if job.get('verification') == 'quarantining':
                if self.recover_quarantine(job):
                    return
            executable = self.checker()
            if not executable:
                update(verification='pending', verification_error='WinRAR is not configured. Select WinRAR… and use Check archives.')
                return
            if path.is_symlink() or not path.is_file() or path.stat().st_size != job['size']:
                update(verification='error', verification_error='Local file is missing, changed, or a symbolic link. It was not moved.')
                return
            # Telegram filenames have unique prefixes; multipart sets cannot be
            # tested reliably until the user restores names and gathers all parts.
            if re.search(r'\.part\d+\.rar$', job['name'], re.IGNORECASE):
                update(verification='error', verification_error='Multipart RAR: check the complete set manually in WinRAR. No files were moved.')
                return
            if not background:
                self.emit('current', job)
                self.emit('progress', {'done': job['size'], 'size': job['size'], 'speed': 0,
                                      'message': message, 'summary': self.store.summary(account, chat)})
            update(verification='checking', verification_error='')
            self.snapshot()
            def report(text):
                self.emit('verification', {'name': job['name'], 'detail': text})
                if not background:
                    self.emit('phase', text)
            report('Waiting to check archive…')
            result = await check_archive(path, executable, self.gate, report)
            if result.status == 'bad':
                destination = quarantine_destination(path)
                update(verification='quarantining', quarantine_path=str(destination), verification_error=result.detail)
                move_to_quarantine(path, destination)
                update(verification='quarantined')
                self.emit('log', f'{job["name"]}: moved to wrong_archives. {result.detail}')
            else:
                update(verification=result.status, verification_error='' if result.status == 'ok' else result.detail,
                       quarantine_path='')
                self.emit('log', f'{job["name"]}: {result.detail}')
        except asyncio.CancelledError:
            update(verification='pending', verification_error='Check interrupted. Use Check archives to retry.')
            raise
        except Exception as exc:
            update(verification='error', verification_error=f'Archive check or move failed ({type(exc).__name__}). Inspect the original and quarantine paths before retrying.')
            self.emit('log', f'File #{message}: archive check or move failed ({type(exc).__name__}).')
        finally:
            self.emit('verification', None)
            self.snapshot()
