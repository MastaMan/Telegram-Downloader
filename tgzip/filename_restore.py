"""Recoverable, non-overwriting renames of completed legacy queue files."""
import os
from pathlib import Path

from .archive_check import move_to_quarantine as move_without_replacing
from .transfer import TransferError


def legacy_source(job):
    """Only the old app's exact naming scheme qualifies, never a broad disk mask."""
    path = Path(job['path'])
    if path.name != f"{job['message']}_{job['document']}_{job['name']}":
        return None
    if job['verification'] == 'quarantining':
        return None
    if job['verification'] == 'quarantined':
        return Path(job['quarantine_path']) if job['quarantine_path'] else None
    return path


def fingerprint(path):
    if path.is_symlink() or not path.is_file():
        raise TransferError('Filename restoration requires an unchanged regular file.')
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def restore_move(store, account, chat, message, plan):
    """Reconcile exits before/after rename, including POSIX link/unlink fallback."""
    source, destination = Path(plan['source']), Path(plan['destination'])
    expected = plan['fingerprint']
    source_exists, destination_exists = os.path.lexists(source), os.path.lexists(destination)
    if destination_exists:
        if fingerprint(destination) != expected:
            raise TransferError('Filename restoration found a different destination file. Both paths were preserved.')
        if source_exists:
            if fingerprint(source) != expected or not os.path.samefile(source, destination):
                raise TransferError('Filename restoration found conflicting files. Both paths were preserved.')
            source.unlink()
    else:
        if not source_exists or fingerprint(source) != expected:
            raise TransferError('The file changed or disappeared during filename restoration. The saved plan was kept.')
        move_without_replacing(source, destination)
    store.finish_filename_move(account, chat, message, plan)


def rename_completed(store, job, source, destination, name, expected_fingerprint):
    if len(destination.name.encode('utf-16-le')) // 2 > 255:
        raise TransferError('Original filename exceeds the Windows filename limit. The old name was kept.')
    plan = {
        'source': str(source), 'destination': str(destination), 'name': name,
        'quarantined': job['verification'] == 'quarantined',
        'fingerprint': fingerprint(source),
    }
    if plan['fingerprint'] != expected_fingerprint:
        raise TransferError('Local archive changed while fetching its original name. The old name was kept.')
    if plan['fingerprint'][2] != job['size']:
        raise TransferError('Local archive size changed. Its old name was kept.')
    store.plan_filename_move(job['account'], job['chat'], job['message'], plan)
    # No await between journal commit, filesystem move, and final metadata commit.
    try:
        restore_move(store, job['account'], job['chat'], job['message'], plan)
    except OSError:
        # A locked file or unsupported long path may fail without moving
        # anything. Abandon only a plan proven to have left the source intact.
        if not os.path.lexists(destination):
            try:
                unchanged = fingerprint(source) == plan['fingerprint']
            except (OSError, TransferError):
                unchanged = False
            if unchanged:
                store.cancel_filename_move(job['account'], job['chat'], job['message'])
        raise
