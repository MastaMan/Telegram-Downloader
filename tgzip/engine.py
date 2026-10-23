import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import InputMessagesFilterDocument
from telethon.crypto import aes
from PySide6.QtCore import QObject, Signal
from .security import Vault, validate_keys, import_legacy_session
from .storage import (parallel_downloads, speed_limit, Store, atomic_write, load_settings, safe_name, archive_extension,
                      name_matcher, SkipRules, FILTER_MODES, SKIP_KNOWN, SKIP_FILTER, is_network_path, FilenameAllocator)
from .transfer import download, DownloadLimiter, TransferError
from .archive_check import check_archive, find_winrar, quarantine_destination, move_to_quarantine
from .filename_restore import legacy_source, fingerprint, rename_completed, restore_move
from .queue_backup import backup_before_update, backup_queue, copy_queue, daily_backup
from .legacy_import import adopt, find_old_files, remove_empty_old_folders
from updater import UpdateError, UpdateCancelled, prepare_update, load_plan

# Server-requested waits, including FLOOD_PREMIUM_WAIT download throttling of
# non-Premium accounts. They are instructions to pause, not transfer failures.
FLOOD_WAITS = (errors.FloodWaitError, errors.FloodPremiumWaitError)


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
    if isinstance(exc, FLOOD_WAITS):
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
        # Set when the configured queue folder is unavailable; queue operations stay blocked.
        self.database_error = None
        self.store = self.open_store()
        self.hunting_all = False
        # The startup daily backup runs beside operations and is awaited on close.
        self.backup_task = None
        self.client = None
        self.account = None
        self.chat = None
        self.dialogs = {}
        self.task = None
        self.kind = None
        self.finish_requested = False
        self.download_wake = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.password_future = None
        self.closing = False
        self.control = asyncio.Lock()
        self.transfers = {}
        self.queue_mode = 'unfinished'
        self.queue_page = 0
        self.queue_request = 0
        self.queue_dirty = False
        self.limiter = DownloadLimiter(self.settings.get('speed_limit'))
        # Global skip rules of the running download pass; None while idle.
        self.rules = None

    def database_path(self):
        folder = self.settings.get('database_dir')
        return (Path(folder) if folder else self.data_dir) / 'queue.sqlite3'

    def open_store(self):
        folder = self.settings.get('database_dir')
        if folder:
            try:
                if not Path(folder).is_dir():
                    raise OSError('Folder not found')
                return Store(self.database_path())
            except (OSError, sqlite3.Error):
                # Never fall back to the old local queue: two diverging queues
                # would download files twice. Operations stay blocked instead.
                self.database_error = (f'The queue database folder is unavailable: {folder}. Connect it and '
                                       'restart, or choose the queue folder again.')
                return Store(':memory:')
        return Store(self.database_path())

    def start_daily_backup(self):
        """Once per calendar day at startup, in a worker thread so the window and downloads never wait."""
        if self.database_error or (self.backup_task is not None and not self.backup_task.done()):
            return
        settings_bytes = json.dumps(self.settings, indent=2).encode('utf-8')
        database = self.database_path()
        async def run():
            try:
                destination = await asyncio.to_thread(daily_backup, database, settings_bytes)
            except TransferError as exc:
                self.emit('log', f'Daily backup was not created: {exc}')
                return
            if destination is not None:
                self.emit('log', f'Daily backup saved: {destination}')
        self.backup_task = asyncio.create_task(run(), name='Daily backup')

    def require_database(self):
        if self.database_error:
            raise TransferError(self.database_error)

    def emit_database(self):
        self.emit('database', {'path': str(self.database_path()), 'custom': bool(self.settings.get('database_dir')),
                               'error': self.database_error})

    def pinned(self):
        return list(self.settings.get('pinned_chats', {}).get(str(self.account), []))

    def save_settings(self):
        atomic_write(self.settings_path, json.dumps(self.settings, indent=2).encode('utf-8'))

    def snapshot(self):
        self.queue_dirty = False
        if self.account and self.chat:
            for job in self.store.pending_quarantines(self.account, self.chat):
                self.recover_quarantine(job)
            value = self.store.queue_page(self.account, self.chat, self.queue_mode, self.queue_page)
            self.queue_page = value['page']
            self.emit('queue', dict(value, summary=self.store.summary(self.account, self.chat),
                                   request=self.queue_request, account=self.account, chat=self.chat))
        else:
            self.queue_page = 0
            self.emit('queue', {'rows': [], 'summary': {'total': 0, 'bytes': 0, 'received': 0, 'complete': 0, 'failed': 0},
                                'mode': self.queue_mode, 'page': 0, 'pages': 1, 'matching': 0,
                                'request': self.queue_request, 'account': self.account, 'chat': self.chat})

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
        self.gate.set()
        self.limiter.reset()

    def launch(self, kind, fn):
        if self.task and not self.task.done():
            self.emit('error', 'Another operation is still running. Stop it first.')
            return
        self.finish_requested = False
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
                self.finish_requested = False
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
        if command == 'finish_current':
            if self.kind == 'downloading' and not self.finish_requested:
                # No await: workers cannot claim another archive after this flag.
                self.finish_requested = True
                self.download_wake.set()
                self.emit('finish_current', True)
                self.emit('log', 'Finishing current downloads and pending archive checks. No new downloads will start. Resume if paused.')
            return
        if command == 'pause':
            if self.kind in {'downloading', 'scanning', 'verifying', 'renaming'}:
                self.gate.clear()
                self.limiter.reset()
                self.store.flush_progress()
                self.emit('state', 'paused')
            return
        if command == 'resume':
            self.limiter.reset()
            self.gate.set()
            self.download_wake.set()
            if self.kind:
                self.emit('state', self.kind)
            return
        async with self.control:
            if command == 'queue_view':
                mode = args.get('mode', 'unfinished')
                if mode not in {'unfinished', 'downloaded', 'skipped', 'all'}:
                    raise ValueError('Unknown queue filter')
                self.queue_mode = mode
                self.queue_page = max(0, int(args.get('page', 0)))
                self.queue_request = int(args.get('request', 0))
                self.snapshot()
            elif command == 'boot':
                self.emit('settings', self.settings)
                self.emit_database()
                if self.database_error:
                    self.emit('error', self.database_error)
                self.start_daily_backup()
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
                chat = await self.select_chat(args['chat'])
                if self.store.legacy_name_jobs(self.account, chat) or self.store.filename_moves(self.account, chat):
                    self.launch('renaming', self.restore_filenames)
            elif command == 'hunt':
                self.require_database()
                chat = int(args['chat'])
                if chat not in self.dialogs:
                    raise ValueError('Chat not accessible')
                if not self.settings.get('download_dir'):
                    raise TransferError('Choose a download folder first.')
                if chat == self.chat and self.task and not self.task.done():
                    self.emit('log', 'This chat already has an operation running. Stop it to hunt again.')
                    return
                if chat != self.chat:
                    await self.select_chat(chat)
                self.launch('scanning', self.hunt)
            elif command == 'hunt_all':
                self.require_database()
                if not self.account:
                    return
                if not self.settings.get('download_dir'):
                    raise TransferError('Choose a download folder first.')
                if not any(chat in self.dialogs for chat in self.pinned()):
                    raise TransferError('Pin at least one accessible chat first.')
                if self.hunting_all and self.task and not self.task.done():
                    self.emit('log', 'Hunt all is already running. Stop it to start again.')
                    return
                await self.cancel()
                self.launch('scanning', self.hunt_all)
            elif command == 'backup':
                self.require_database()
                self.store.flush_progress()
                settings_bytes = json.dumps(self.settings, indent=2).encode('utf-8')
                destination = await asyncio.to_thread(backup_queue, self.database_path(), settings_bytes)
                self.emit('log', f'Queue backup saved: {destination}')
                self.emit('backup_done', str(destination))
            elif command == 'import_old':
                self.require_database()
                if not self.settings.get('download_dir'):
                    raise TransferError('Choose a download folder first.')
                source = Path(args['path']).resolve()
                if not source.is_dir():
                    raise TransferError('Choose an existing folder with the old downloads.')
                self.launch('importing', lambda: self.import_old_files(source))
            elif command == 'database':
                if self.task and not self.task.done():
                    raise TransferError('Stop the current operation before changing the queue folder.')
                await self.move_database(Path(args['path']), args.get('mode', 'copy'))
            elif command == 'pin':
                if not self.account:
                    return
                chat = int(args['chat'])
                pinned = self.settings.setdefault('pinned_chats', {}).setdefault(str(self.account), [])
                if args.get('pinned', True) and chat not in pinned:
                    pinned.append(chat)
                elif not args.get('pinned', True) and chat in pinned:
                    pinned.remove(chat)
                self.save_settings()
                self.emit('settings', self.settings)
            elif command == 'hunting':
                values = {}
                for key in ('name_filter_enabled', 'skip_known', 'folder_per_chat'):
                    if key in args:
                        values[key] = bool(args[key])
                if 'name_filter_mode' in args:
                    if args['name_filter_mode'] not in FILTER_MODES:
                        raise ValueError('Unknown name filter mode')
                    values['name_filter_mode'] = args['name_filter_mode']
                if 'name_filter' in args:
                    values['name_filter'] = str(args['name_filter'])[:1000]
                try:
                    name_matcher(dict(self.settings, **values))
                except ValueError as exc:
                    raise TransferError(str(exc)) from None
                self.settings.update(values)
                self.save_settings()
                self.emit('settings', self.settings)
                if self.account and self.chat and self.kind not in {'scanning', 'renaming'}:
                    # Rules apply live: waiting files are skipped or restored at once.
                    # Restored files before the running pass position wait for the next Start.
                    self.rules = rules = self.build_rules(self.rules) if self.rules else None
                    self.apply_rules(self.account, self.chat, rules)
                    self.download_wake.set()
                    self.snapshot()
            elif command == 'folder':
                self.settings['download_dir'] = str(Path(args['path']).resolve())
                self.save_settings()
                self.emit('settings', self.settings)
            elif command == 'parallel':
                self.settings['parallel_downloads'] = parallel_downloads(args['value'])
                self.save_settings()
                self.download_wake.set()
                self.emit('settings', self.settings)
            elif command == 'speed_limit':
                self.settings['speed_limit'] = speed_limit(args['value'])
                self.save_settings()
                self.limiter.set_limit(self.settings['speed_limit'])
                self.emit('settings', self.settings)
            elif command == 'scan':
                self.require_database()
                if self.chat and self.account:
                    self.launch('scanning', self.scan)
            elif command == 'start':
                self.require_database()
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
                self.require_database()
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
                if self.backup_task is not None:
                    # Never cut a backup off halfway; the copy takes seconds.
                    await asyncio.gather(self.backup_task, return_exceptions=True)
                try:
                    if self.client:
                        self.persist_session()
                        await asyncio.wait_for(self.client.disconnect(), 15)
                except Exception as exc:
                    self.emit('error', error_text(exc))
                finally:
                    try:
                        self.store.close()
                    finally:
                        asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

    def persist_session(self):
        if self.client and self.client.session.auth_key:
            self.secrets['session'] = self.client.session.save()
            self.vault.save(self.secrets)

    async def update_application(self):
        cancel = threading.Event()
        # An update backs up the queue in use; an unavailable queue folder blocks it.
        self.require_database()
        self.store.flush_progress()
        settings_bytes = json.dumps(self.settings, indent=2).encode('utf-8')
        database = self.database_path()
        def prepare_with_backup():
            plan = prepare_update(Path(__file__).resolve().parents[1],
                                  lambda text: self.emit('update_progress', text), cancel)
            if plan:
                try:
                    backup = backup_before_update(
                        self.data_dir, settings_bytes, cancel,
                        lambda text: self.emit('update_progress', text), database=database)
                    self.emit('log', f'Queue and settings backup saved: {backup}')
                except BaseException:
                    import shutil
                    _, stage, _ = load_plan(plan)
                    shutil.rmtree(stage, ignore_errors=True)
                    raise
            return plan
        def prepare():
            try:
                return prepare_with_backup()
            except UpdateCancelled:
                # Cancellation is awaited below; do not leave an exceptional
                # executor future behind a cancelled asyncio.shield.
                return None
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='Application update') as executor:
            operation = asyncio.get_running_loop().run_in_executor(
                executor, prepare)
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
                                     device_model='Telegram File Hunter', system_version='Windows',
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
            self.emit('qr', None)
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
        self.queue_page = 0
        if previous and not self.chat:
            self.emit('log', 'The saved chat is not currently accessible. Choose a chat from the list.')
        self.emit('selected', self.chat)
        self.emit('current', None)
        self.snapshot()
        if self.chat:
            await self.restore_filenames()

    async def restore_filenames(self):
        account, chat = self.account, self.chat
        if not account or not chat:
            return
        plans = self.store.filename_moves(account, chat)
        jobs = [job for job in self.store.legacy_name_jobs(account, chat) if legacy_source(job) is not None]
        if not jobs and not plans:
            return
        previous_kind = self.kind
        self.kind = 'renaming'
        self.emit('current', None)
        self.emit('state', 'renaming')
        restored, skipped = 0, 0
        try:
            for message_id, plan in plans:
                await self.gate.wait()
                restore_move(self.store, account, chat, message_id, plan)
                restored += 1
            # Recovery may have completed rows fetched above.
            jobs = self.store.legacy_name_jobs(account, chat)
            candidates = []
            for index, job in enumerate(jobs):
                if index % 100 == 0:
                    await self.gate.wait()
                    await asyncio.sleep(0)
                source = legacy_source(job)
                if source is None:
                    continue
                try:
                    identity = fingerprint(source)
                    if identity[2] != job['size']:
                        raise TransferError('Local file size changed.')
                    candidates.append((job, source, identity))
                except (OSError, TransferError):
                    skipped += 1
            filenames = self.store.filename_allocator()
            self.emit('rename_progress', {'done': 0, 'total': len(candidates)})
            for offset in range(0, len(candidates), 100):
                batch = candidates[offset:offset + 100]
                while True:
                    await self.gate.wait()
                    try:
                        messages = await self.client.get_messages(
                            self.dialogs[chat].input_entity, ids=[job['message'] for job, _, _ in batch])
                        break
                    except FLOOD_WAITS as exc:
                        self.emit('log', f'Filename restoration: Telegram requested a {exc.seconds}-second wait.')
                        await asyncio.sleep(max(exc.seconds, 1))
                messages = {message.id: message for message in messages if message is not None}
                for index, (job, source, identity) in enumerate(batch, 1):
                    await self.gate.wait()
                    message = messages.get(job['message'])
                    doc = getattr(message, 'document', None)
                    original = getattr(getattr(message, 'file', None), 'name', None)
                    if (not doc or str(doc.id) != job['document'] or doc.size != job['size']
                            or not archive_extension(original, doc.mime_type)):
                        skipped += 1
                        self.emit('rename_progress', {'done': offset + index, 'total': len(candidates)})
                        continue
                    name = safe_name(original or f"archive_{job['message']}{archive_extension(None, doc.mime_type)}")
                    destination = filenames.allocate(source.parent, name)
                    try:
                        rename_completed(self.store, job, source, destination, name, identity)
                    except (OSError, TransferError) as exc:
                        self.emit('log', f"File #{job['message']}: filename restoration failed. {error_text(exc)}")
                        # A journaled move must be reconciled before any check
                        # can operate on this chat's saved paths.
                        if self.store.filename_moves(account, chat):
                            raise
                        skipped += 1
                    else:
                        restored += 1
                    self.emit('rename_progress', {'done': offset + index, 'total': len(candidates)})
                    await asyncio.sleep(0)
                self.snapshot()
            self.emit('log', f'Filename restoration: {restored} restored, {skipped} skipped (missing, changed, or unavailable).')
            self.emit('rename_result', {'restored': restored, 'skipped': skipped})
        finally:
            self.kind = previous_kind
            self.emit('state', previous_kind or 'idle')
            self.snapshot()

    async def move_database(self, folder, mode):
        """Copy the queue to another folder ('copy'/'replace') or open a queue already there ('use')."""
        folder = folder.resolve()
        if mode not in {'copy', 'replace', 'use'}:
            raise ValueError('Unknown queue folder mode')
        if not folder.is_dir():
            raise TransferError('Choose an existing folder for the queue database.')
        if is_network_path(folder):
            raise TransferError('Network folders are not supported for the queue database. Choose a local or '
                                'synced folder such as Dropbox on this computer.')
        target = folder / 'queue.sqlite3'
        if not self.database_error and target.resolve() == self.database_path().resolve():
            return
        if mode == 'use':
            if not target.is_file():
                raise TransferError('That folder has no queue.sqlite3 to use.')
        else:
            if self.database_error:
                raise TransferError('The current queue is unavailable, so it cannot be copied. '
                                    'Choose a folder that already contains the queue and select Use.')
            self.store.flush_progress()
            moved = await asyncio.to_thread(copy_queue, self.database_path(), target, mode == 'replace')
            if moved:
                self.emit('log', f'The queue that was in that folder was kept as {moved.name}.')
        try:
            store = Store(target)
        except sqlite3.Error as exc:
            raise TransferError('That queue database cannot be opened. Choose another folder or Replace it.') from exc
        previous = self.database_path()
        self.store.close()
        self.store = store
        self.database_error = None
        if folder == self.data_dir.resolve():
            self.settings.pop('database_dir', None)
        else:
            self.settings['database_dir'] = str(folder)
        self.save_settings()
        self.emit('settings', self.settings)
        self.emit_database()
        self.emit('log', f'Queue database: {target}. The previous file stays at {previous} and is no longer used.')
        self.snapshot()

    async def import_old_files(self, source):
        """Rename and move archives from the earlier scripts into the download folder and remember them."""
        folder = Path(self.settings['download_dir']).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        candidates = await asyncio.to_thread(find_old_files, source, self.store.tracked_paths())
        # Imported files keep their original names; only files on disk can block a name.
        allocator = FilenameAllocator([])
        counts = {'moved': 0, 'duplicate': 0, 'kept': 0, 'failed': 0}
        journal = []
        self.emit('import_progress', {'done': 0, 'total': len(candidates)})
        try:
            for index, path in enumerate(candidates, 1):
                await self.gate.wait()
                move = asyncio.ensure_future(asyncio.to_thread(adopt, path, folder, allocator))
                try:
                    destination, status, name, size = await asyncio.shield(move)
                except asyncio.CancelledError:
                    # Stop never interrupts a move halfway; record it, then stop.
                    destination, status, name, size = await move
                    self.store.add_local_file(destination, name, size, status)
                    journal.append(f'{status}\t{path}\t{destination}')
                    raise
                except OSError as exc:
                    counts['failed'] += 1
                    journal.append(f'failed\t{path}\t{type(exc).__name__}')
                    self.emit('log', f'Import: {path.name} was left in place ({type(exc).__name__}).')
                    continue
                self.store.add_local_file(destination, name, size, status)
                counts[status] += 1
                journal.append(f'{status}\t{path}\t{destination}')
                self.emit('import_progress', {'done': index, 'total': len(candidates)})
        finally:
            if journal:
                stamp = time.strftime('%Y%m%d-%H%M%S')
                log = self.data_dir / 'imports' / f'import-{stamp}.tsv'
                atomic_write(log, ('status\told path\tnew path\n' + '\n'.join(journal) + '\n').encode('utf-8'))
                self.emit('log', f'Import journal: {log}')
            self.emit('import_progress', None)
        removed = await asyncio.to_thread(remove_empty_old_folders, source)
        self.emit('log', f"Imported old files: {counts['moved']:,} renamed and moved, {counts['kept']:,} already in place, "
                         f"{counts['duplicate']:,} already present (old copy left in place), {counts['failed']:,} failed; "
                         f'{removed:,} empty group folders removed. They count as downloaded for Skip already downloaded.')
        if self.account and self.chat:
            self.apply_rules(self.account, self.chat)

    async def select_chat(self, chat):
        # Switching chats serializes cancellation and saves the old file first.
        await self.cancel()
        return self.switch_chat(chat)

    def switch_chat(self, chat):
        chat = int(chat)
        if chat not in self.dialogs:
            raise ValueError('Chat not accessible')
        self.chat = chat
        self.queue_page = 0
        self.settings.setdefault('selected_chats', {})[str(self.account)] = chat
        self.save_settings()
        self.emit('selected', chat)
        self.emit('current', None)
        self.snapshot()
        return chat

    def target_folder(self, account, chat):
        """Folder for files that have no downloaded bytes yet, per the current layout setting."""
        folder = self.settings.get('download_dir')
        if not folder:
            return None
        root = Path(folder).resolve()
        if self.settings.get('folder_per_chat', False):
            return root / f'account_{account}' / f'chat_{chat}'
        return root

    def build_rules(self, previous=None):
        try:
            matcher = name_matcher(self.settings)
        except ValueError as exc:
            raise TransferError(str(exc)) from None
        known = None
        if self.settings.get('skip_known', True):
            known = self.store.known_files()
            if previous is not None and previous.known is not None:
                # Keep files claimed by the running pass.
                known[0].update(previous.known[0])
                known[1].update(previous.known[1])
        return SkipRules(matcher, known)

    def apply_rules(self, account, chat, rules=None):
        counts = self.store.apply_skip_rules(account, chat, rules or self.build_rules())
        if counts:
            self.emit('log', f'Hunting rules skip {counts[SKIP_KNOWN]:,} already downloaded and '
                             f'{counts[SKIP_FILTER]:,} not matching the name filter. See the Skipped view.')
        return counts

    async def hunt(self):
        """Scan new messages, then download what the global rules allow."""
        await self.scan()
        self.kind = 'downloading'
        self.emit('state', 'downloading')
        if not self.gate.is_set():
            self.emit('state', 'paused')
        await self.download_queue()

    async def hunt_all(self):
        """Hunt every accessible pinned chat in pin order, one after another."""
        chats = [chat for chat in self.pinned() if chat in self.dialogs]
        self.hunting_all = True
        try:
            for index, chat in enumerate(chats, 1):
                if self.finish_requested:
                    self.emit('log', 'Hunt all stopped after Finish current. Remaining pinned chats were not hunted.')
                    break
                if chat != self.chat:
                    self.switch_chat(chat)
                name = self.dialogs[chat].name
                self.emit('hunt_progress', {'index': index, 'total': len(chats), 'name': name})
                self.emit('log', f'Hunt all {index}/{len(chats)}: {name}')
                self.kind = 'scanning'
                self.emit('state', 'scanning')
                if not self.gate.is_set():
                    self.emit('state', 'paused')
                try:
                    await self.hunt()
                except Exception as exc:
                    # One unavailable chat does not stop the others; Stop still cancels everything.
                    self.emit('log', f'{name}: {error_text(exc)} Continuing with the next pinned chat.')
            else:
                self.emit('log', f'Hunt all finished: {len(chats)} pinned chats checked.')
        finally:
            self.hunting_all = False
            self.emit('hunt_progress', None)

    async def scan(self):
        await self.restore_filenames()
        account, chat = self.account, self.chat
        root = self.target_folder(account, chat)
        if root is None:
            raise TransferError('Choose a download folder first.')
        cursor = self.store.cursor(account, chat)
        filenames = self.store.filename_allocator()
        if not cursor:
            self.emit('log', 'Scanning full history for ZIP and RAR archives. Existing queue entries are preserved.')
        count, last = 0, 0
        while True:
            try:
                # Telegram's document filter skips text, photo and video messages server-side.
                async for message in self.client.iter_messages(self.dialogs[chat].input_entity, min_id=cursor,
                                                               reverse=True, filter=InputMessagesFilterDocument):
                    await self.gate.wait()
                    item = None
                    doc = message.document
                    extension = archive_extension(message.file.name, doc.mime_type) if doc and message.file else None
                    if extension and not self.store.contains(account, chat, message.id):
                        name = safe_name(message.file.name or f'archive_{message.id}{extension}')
                        item = {'document': doc.id, 'name': name, 'size': doc.size,
                                'path': filenames.allocate(root, name), 'naming_version': 1}
                    self.store.scanned(account, chat, message.id, item)
                    cursor = max(cursor, message.id)
                    count += 1
                    now = time.monotonic()
                    if now - last > .5:
                        self.emit('scan', count)
                        self.snapshot()
                        last = now
                break
            except FLOOD_WAITS as exc:
                # Resume from the saved checkpoint after the requested wait.
                self.emit('log', f'Scan: Telegram requested a {exc.seconds}-second wait. Scanning continues automatically.')
                await asyncio.sleep(max(exc.seconds, 1))
        self.emit('log', f'Scan complete: checked {count:,} files. ZIP and RAR queue saved.')
        self.apply_rules(account, chat)
        self.snapshot()

    async def download_queue(self):
        await self.restore_filenames()
        if aes.cryptg is not None:
            self.emit('log', 'Download encryption: cryptg acceleration enabled.')
        elif aes.libssl.decrypt_ige and aes.libssl.encrypt_ige:
            self.emit('log', 'Download encryption: native OpenSSL acceleration enabled.')
        else:
            self.emit('log', 'Download encryption: Python fallback. For cryptg acceleration, use Python 3.11–3.14 with Start.bat.')
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
        if self.finish_requested:
            self.emit('log', 'Current downloads and pending checks finished. Start continues the remaining queue.')
        else:
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
                               'summary': summary,
                               'files': [dict(row, message=message) for message, row in self.transfers.items()]})

    async def download_jobs(self, wake):
        account, chat = self.account, self.chat
        after = 0
        filenames = self.store.filename_allocator()
        self.rules = self.build_rules()
        self.apply_rules(account, chat, self.rules)
        self.snapshot()

        active = 0
        failed = False
        finished = asyncio.Event()

        async def transfer(job):
            nonlocal active, failed
            try:
                await self.download_job(job, wake, account, chat, filenames)
            except BaseException:
                # Stop claiming immediately, before TaskGroup propagates failure/cancellation.
                failed = True
                raise
            finally:
                active -= 1
                self.download_wake.set()

        async def workers():
            nonlocal after, active
            exhausted = False
            # Only claimed files own tasks, bounded by the maximum live limit of 20.
            # Lowering the limit drains existing transfers without canceling them.
            async with asyncio.TaskGroup() as tasks:
                while True:
                    self.download_wake.clear()
                    if failed:
                        break
                    limit = parallel_downloads(self.settings.get('parallel_downloads'))
                    if self.gate.is_set() and not self.finish_requested and not exhausted:
                        while active < limit:
                            # Selection, cursor advance and slot reservation never yield.
                            job = self.store.next_job(account, chat, after)
                            if job is None:
                                exhausted = True
                                break
                            after = job['message']
                            reason = self.rules.reason(job)
                            if reason:
                                # Rules changed or a duplicate was claimed during this pass.
                                self.store.update(account, chat, after, state='skipped', error=reason)
                                self.queue_dirty = True
                                continue
                            self.rules.claim(job)
                            active += 1
                            tasks.create_task(transfer(job), name=f'Download {after}')
                    if active == 0 and (exhausted or self.finish_requested):
                        break
                    await self.download_wake.wait()
            finished.set()

        async def report_progress():
            last_checkpoint = time.monotonic()
            while not finished.is_set():
                try:
                    await asyncio.wait_for(finished.wait(), timeout=.25)
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                if now - last_checkpoint >= 1:
                    self.store.flush_progress()
                    last_checkpoint = now
                self.emit_transfers()
                if self.queue_dirty:
                    self.snapshot()

        self.emit_transfers()
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(workers(), name='Download pool')
                tasks.create_task(report_progress(), name='Download progress')
        finally:
            self.rules = None
            self.store.flush_progress()
            self.emit_transfers()

    def prepare_filename(self, job, message, filenames):
        if job['naming_version'] < 2:
            old = Path(job['path'])
            legacy = (job['naming_version'] == 0 and
                      old.name == f"{job['message']}_{job['document']}_{job['name']}")
            # A completed old file may be awaiting its final database checkpoint.
            if legacy and old.exists():
                return
            # Preserve names from other queue/import formats.
            if job['naming_version'] == 0 and not legacy:
                values = {'naming_version': 2}
            else:
                original = getattr(getattr(message, 'file', None), 'name', None)
                name = safe_name(original or job['name'])
                path = filenames.allocate(old.parent, name, previous=old)
                old_part = old.with_name(old.name + '.part')
                # Persist the old temporary location with the new final path in ONE
                # transaction. No move is needed, even if the process exits here.
                partial = str(old_part) if legacy and old_part.exists() else ''
                values = {'name': name, 'path': str(path), 'partial_path': partial, 'naming_version': 2}
            self.store.update(job['account'], job['chat'], job['message'], **values)
            job.update(values)
        self.relocate_unstarted(job, filenames)

    def relocate_unstarted(self, job, filenames):
        # Files without downloaded bytes follow the current folder settings;
        # partial and completed files always stay where their data is.
        folder = self.target_folder(job['account'], job['chat'])
        path = Path(job['path'])
        part = Path(job['partial_path']) if job.get('partial_path') else path.with_name(path.name + '.part')
        if (folder is None or job['done'] or os.path.lexists(part) or os.path.lexists(path)
                or path.parent.resolve() == folder):
            return
        values = {'path': str(filenames.allocate(folder, job['name'], previous=path)), 'partial_path': ''}
        self.store.update(job['account'], job['chat'], job['message'], **values)
        job.update(values)

    async def download_job(self, job, wake, account, chat, filenames):
        after = job['message']
        self.transfers[after] = {'name': job['name'], 'path': job['path'], 'done': job['done'],
                                 'size': job['size'], 'speed': 0, 'updated': time.monotonic()}
        self.emit_transfers()
        self.emit('current', job)
        self.store.update(account, chat, after, state='downloading', error='')
        self.queue_dirty = True
        downloaded = False
        def progress(done, speed):
            self.store.stage_progress(account, chat, job['message'], done)
            if done == job['size']:
                # Durable evidence before final rename lets a restart recover
                # our completed file without accepting arbitrary same-size files.
                self.store.flush_progress()
                job['done'] = done
            self.transfers[after].update(done=done, speed=speed, updated=time.monotonic())
            self.emit('progress', {'done': done, 'size': job['size'], 'speed': speed,
                                   'message': job['message']})
        try:
            retries = 0
            while True:
                try:
                    await self.gate.wait()
                    # Fresh message refreshes expired file references on every retry/resume.
                    message = await self.client.get_messages(self.dialogs[chat].input_entity, ids=job['message'])
                    if not message or not message.document:
                        raise TransferError('Message was deleted or the attachment is no longer accessible.')
                    if str(message.document.id) != job['document'] or message.document.size != job['size']:
                        raise TransferError('The attachment changed since scanning. The old partial file was preserved.')
                    self.prepare_filename(job, message, filenames)
                    self.transfers[after].update(name=job['name'], path=job['path'])
                    await download(self.client, message, job, self.gate, progress, limiter=self.limiter)
                    break
                except FLOOD_WAITS as exc:
                    # Honoring a requested wait never consumes a connection retry,
                    # so many parallel files cannot fail on short rate limits.
                    kind = 'non-Premium download limit' if isinstance(exc, errors.FloodPremiumWaitError) else 'rate limit'
                    self.emit('log', f'Telegram {kind}: waiting {exc.seconds} seconds. Stop remains available.')
                    progress(self.transfers[after]['done'], 0)
                    await asyncio.sleep(max(exc.seconds, 1))
                except (ConnectionError, asyncio.TimeoutError, errors.FileReferenceExpiredError, errors.ServerError) as exc:
                    if retries == 3:
                        raise
                    retries += 1
                    self.emit('log', f'Connection retry {retries}/3 ({type(exc).__name__}). Partial file saved.')
                    progress(self.transfers[after]['done'], 0)
                    await asyncio.sleep(2 ** retries)
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
        await self.restore_filenames()
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
            # A sibling volume may still be queued or downloading. Check complete
            # multipart sets manually rather than misclassifying missing volumes.
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
                destination = quarantine_destination(path, result.kind)
                update(verification='quarantining', quarantine_path=str(destination), verification_error=result.detail)
                move_to_quarantine(path, destination)
                update(verification='quarantined')
                self.emit('log', f'{job["name"]}: moved to {destination.parent.relative_to(path.parent)}. {result.detail}')
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
