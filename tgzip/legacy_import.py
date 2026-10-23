"""Adopt archives saved by the earlier downloader scripts: group_<id> folders and name__msg<id>.ext files."""
import errno
import os
from pathlib import Path
import re
import shutil
import uuid

OLD_NAME = re.compile(r'^(?P<stem>.+?)__msg\d+(?P<extension>\.(?:zip|rar))$', re.IGNORECASE)
OLD_FOLDER = re.compile(r'^group_-?\d+$', re.IGNORECASE)


def original_name(name):
    match = OLD_NAME.match(name)
    return match['stem'] + match['extension'] if match else name


def find_old_files(source, tracked):
    """Old-layout archives the queue does not track. Partial files and wrong_archives are left alone."""
    found = []
    for root, directories, files in os.walk(source):
        directories[:] = sorted(name for name in directories if name.casefold() != 'wrong_archives')
        in_old_folder = OLD_FOLDER.match(Path(root).name) is not None
        for name in files:
            if not name.lower().endswith(('.zip', '.rar')):
                continue
            path = Path(root) / name
            if path.is_symlink() or str(path.absolute()).casefold() in tracked:
                continue
            if in_old_folder or OLD_NAME.match(name):
                found.append(path)
    return sorted(found)


def move_without_overwrite(source, destination):
    try:
        if os.name == 'nt':
            os.rename(source, destination)  # Windows refuses an existing destination.
        else:
            os.link(source, destination)
            os.unlink(source)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, 'winerror', None) != 17:  # ERROR_NOT_SAME_DEVICE
            raise
    # Another drive: copy beside the destination, publish without overwriting, then remove the source.
    temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
    try:
        shutil.copy2(source, temporary)
        if os.name == 'nt':
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    os.unlink(source)


def adopt(path, folder, allocator):
    """Give one old file its original name in folder. Returns (destination, status, name, size)."""
    name = original_name(path.name)
    size = path.stat().st_size
    existing = folder / name
    if existing.exists():
        if os.path.samefile(existing, path):
            return path, 'kept', name, size
        if existing.stat().st_size == size:
            # The same archive is already present under its original name; keep the old copy untouched.
            return existing, 'duplicate', name, size
    destination = allocator.allocate(folder, name)
    move_without_overwrite(path, destination)
    return destination, 'moved', name, size


def remove_empty_old_folders(source):
    removed = 0
    for root, directories, files in os.walk(source, topdown=False):
        path = Path(root)
        if path != Path(source) and OLD_FOLDER.match(path.name) and not any(path.iterdir()):
            path.rmdir()
            removed += 1
    return removed
