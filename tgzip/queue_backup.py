"""Consistent queue snapshots: before app updates, on request, and when moving the queue."""
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile

from updater import UpdateError, cancelled
from .transfer import TransferError


BACKUP_LIMIT = 10
BACKUP_NAME = re.compile(r"update-\d{8}T\d{12}Z-[0-9a-f]{32}")
MANUAL_ZIP = re.compile(r"manual-\d{8}T\d{12}Z-[0-9a-f]{32}\.zip")


def copy_database(source, target, *, pages=-1, cancel=None):
    """Write a standalone, integrity-checked copy using SQLite's backup API (includes committed WAL data)."""
    source_uri = Path(source).resolve().as_uri() + '?mode=ro'
    progress = (lambda *_: cancelled(cancel)) if cancel is not None else None
    with closing(sqlite3.connect(source_uri, uri=True)) as reader, closing(sqlite3.connect(target)) as writer:
        # A single step (pages=-1) copies one consistent snapshot even while
        # downloads keep committing through the application's connection.
        reader.backup(writer, pages=pages, progress=progress, sleep=0.05)
        writer.execute('PRAGMA journal_mode=DELETE')
        if cancel is not None:
            def check_cancel():
                cancelled(cancel)
                return 0
            writer.set_progress_handler(check_cancel, 1000)
        if writer.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise sqlite3.DatabaseError('Integrity check failed')
    with Path(target).open('r+b') as stream:
        os.fsync(stream.fileno())


def snapshot(database, backups, settings_bytes, prefix, pattern, *, pages=-1, cancel=None):
    """Create backups/<prefix>-<time>-<id> with queue.sqlite3 and settings.json; keep the newest ten."""
    backups = Path(backups)
    temporary = None
    try:
        backups.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=backups))
        copy_database(database, temporary / 'queue.sqlite3', pages=pages, cancel=cancel)
        if cancel is not None:
            cancelled(cancel)
        with (temporary / 'settings.json').open('wb') as stream:
            stream.write(settings_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        destination = backups / f'{prefix}-{stamp}-{uuid.uuid4().hex}'
        if cancel is not None:
            cancelled(cancel)
        temporary.rename(destination)
        temporary = None
        # Prune only completed snapshots owned by this feature, after success.
        snapshots = sorted(
            (item for item in backups.iterdir()
             if item != destination and pattern.fullmatch(item.name) and item.is_dir() and not item.is_symlink()
             and (item / 'queue.sqlite3').is_file() and (item / 'settings.json').is_file()),
            key=lambda item: item.name, reverse=True)
        for old in snapshots[BACKUP_LIMIT - 1:]:
            shutil.rmtree(old)
        return destination
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def backup_before_update(data_dir, settings_bytes, cancel, report, database=None):
    """Snapshot the queue into the local application data folder before an update."""
    data_dir = Path(data_dir)
    try:
        cancelled(cancel)
        report('Backing up queue and settings…')
        return snapshot(database or data_dir / 'queue.sqlite3', data_dir / 'backups', settings_bytes,
                        'update', BACKUP_NAME, pages=256, cancel=cancel)
    except (OSError, sqlite3.Error) as exc:
        cancelled(cancel)
        raise UpdateError('Cannot create or retain the queue backup. The update was not installed. '
                          'Check free disk space and permissions in the application data folder.') from exc


def backup_queue(database, settings_bytes):
    """Manual ZIP snapshot (queue.sqlite3 + settings.json) next to the queue database, so synced folders sync it too."""
    database = Path(database)
    backups = database.parent / 'backups'
    temporary = None
    try:
        backups.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=backups))
        copy_database(database, temporary / 'queue.sqlite3')
        package = temporary / 'backup.zip'
        with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(temporary / 'queue.sqlite3', 'queue.sqlite3')
            archive.writestr('settings.json', settings_bytes)
        with zipfile.ZipFile(package) as archive:
            if archive.testzip() is not None or sorted(archive.namelist()) != ['queue.sqlite3', 'settings.json']:
                raise zipfile.BadZipFile('Backup archive verification failed')
        with package.open('r+b') as stream:
            os.fsync(stream.fileno())
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        destination = backups / f'manual-{stamp}-{uuid.uuid4().hex}.zip'
        package.rename(destination)
        # Keep the newest ten ZIP backups; folders from earlier versions are left alone.
        archives = sorted((item for item in backups.iterdir()
                           if item != destination and MANUAL_ZIP.fullmatch(item.name) and item.is_file()
                           and not item.is_symlink()), key=lambda item: item.name, reverse=True)
        for old in archives[BACKUP_LIMIT - 1:]:
            old.unlink()
        return destination
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise TransferError('Cannot create the queue backup. Check free space and permissions in the queue folder.') from exc
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def move_aside(database):
    """Rename an existing queue and its WAL/SHM files so a stale WAL is never applied to another database."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    database = Path(database)
    moved = None
    for suffix in ('', '-wal', '-shm'):
        path = database.with_name(database.name + suffix)
        if os.path.lexists(path):
            target = database.with_name(f'{database.name}.replaced-{stamp}{suffix}')
            os.replace(path, target)
            moved = moved or target
    return moved


def copy_queue(source, target, replace=False):
    """Copy the current queue into another folder; an existing queue there is kept under a .replaced name."""
    target = Path(target)
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        copy_database(source, temporary)
        moved = None
        if os.path.lexists(target) or os.path.lexists(target.with_name(target.name + '-wal')):
            if not replace:
                raise TransferError('That folder already contains a queue. Choose to use it or replace it.')
            moved = move_aside(target)
        os.replace(temporary, target)
        return moved
    except (OSError, sqlite3.Error) as exc:
        raise TransferError('Cannot copy the queue to that folder. Check free space and permissions.') from exc
    finally:
        if temporary.exists():
            temporary.unlink()
