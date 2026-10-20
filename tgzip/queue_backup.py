"""Consistent local queue snapshots made before installing app updates."""
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import uuid

from updater import UpdateError, cancelled


BACKUP_LIMIT = 10
BACKUP_NAME = re.compile(r"update-\d{8}T\d{12}Z-[0-9a-f]{32}")


def backup_before_update(data_dir, settings_bytes, cancel, report):
    """Use SQLite's backup API so committed WAL data is included."""
    data_dir = Path(data_dir)
    backups = data_dir / 'backups'
    temporary = None
    try:
        cancelled(cancel)
        report('Backing up queue and settings…')
        backups.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=backups))
        database = temporary / 'queue.sqlite3'
        source_uri = (data_dir / 'queue.sqlite3').resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(source_uri, uri=True)) as source, closing(sqlite3.connect(database)) as target:
            source.backup(target, pages=256, progress=lambda *_: cancelled(cancel), sleep=0.05)
            target.execute('PRAGMA journal_mode=DELETE')
            def check_cancel():
                cancelled(cancel)
                return 0
            target.set_progress_handler(check_cancel, 1000)
            if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise UpdateError('Queue backup failed its integrity check. The update was not installed.')
        cancelled(cancel)
        with database.open('r+b') as stream:
            os.fsync(stream.fileno())
        with (temporary / 'settings.json').open('wb') as stream:
            stream.write(settings_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        destination = backups / f'update-{stamp}-{uuid.uuid4().hex}'
        cancelled(cancel)
        temporary.rename(destination)
        temporary = None
        # Prune only completed snapshots owned by this feature, after success.
        snapshots = sorted(
            (item for item in backups.iterdir()
             if item != destination and BACKUP_NAME.fullmatch(item.name) and item.is_dir() and not item.is_symlink()
             and (item / 'queue.sqlite3').is_file() and (item / 'settings.json').is_file()),
            key=lambda item: item.name, reverse=True)
        for old in snapshots[BACKUP_LIMIT - 1:]:
            shutil.rmtree(old)
        return destination
    except (OSError, sqlite3.Error) as exc:
        cancelled(cancel)
        raise UpdateError('Cannot create or retain the queue backup. The update was not installed. '
                          'Check free disk space and permissions in the application data folder.') from exc
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
