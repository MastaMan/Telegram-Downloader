"""Resumable streaming independent of the GUI. Part files are the byte authority."""
import asyncio
import os
import time
from pathlib import Path
from .storage import CHUNK, resume_offset


class TransferError(Exception):
    pass


async def download(client, message, job, gate, progress):
    target = Path(job['path'])
    part = Path(job.get('partial_path') or target.with_name(target.name + '.part'))
    if len((target.name + '.part').encode('utf-16-le')) // 2 > 255:
        raise TransferError('Original filename exceeds the Windows filename limit. It was not shortened.')
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = job['size']
    extension = target.suffix.lower()
    if extension not in {'.zip', '.rar'}:
        raise TransferError('Only ZIP and RAR attachments are supported.')
    if part.is_symlink() or target.is_symlink():
        raise TransferError('Symbolic links are not supported as download files.')
    if target.exists():
        if part.exists():
            raise TransferError('Destination already exists beside a partial download. Move the destination away and retry.')
        if job.get('naming_version', 0) >= 2 and job.get('done', 0) != expected:
            raise TransferError('Destination appeared before this download completed. Move it away and retry.')
        if target.stat().st_size == expected:
            progress(expected, 0)
            return
        raise TransferError('Destination already exists with different or invalid contents. Move it away and retry.')
    offset = resume_offset(part, expected)
    stream = None
    with open(part, 'r+b' if part.exists() else 'x+b') as f:
        f.truncate(offset)
        f.seek(offset)
        progress(offset, 0)
        last_time = time.monotonic()
        window_time, window_bytes = last_time, 0
        try:
            if offset < expected:
                stream = client.iter_download(message.media, offset=offset, request_size=CHUNK, chunk_size=CHUNK)
                iterator = stream.__aiter__()
                while offset < expected:
                    if not gate.is_set():
                        f.flush()
                        os.fsync(f.fileno())
                        progress(offset, 0)
                        await gate.wait()
                        window_time, window_bytes = time.monotonic(), 0
                    try:
                        # Cancellation interrupts stalled network reads as well as pauses.
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=60)
                    except StopAsyncIteration:
                        break
                    # A pause received during a request holds this chunk in memory.
                    if not gate.is_set():
                        f.flush()
                        os.fsync(f.fileno())
                        progress(offset, 0)
                        await gate.wait()
                        window_time, window_bytes = time.monotonic(), 0
                    if not chunk or offset + len(chunk) > expected:
                        raise TransferError('The server returned an unexpected file size.')
                    f.write(chunk)
                    offset += len(chunk)
                    window_bytes += len(chunk)
                    now = time.monotonic()
                    if now - last_time >= .25 or offset == expected:
                        f.flush()
                        progress(offset, window_bytes / max(now - window_time, .001))
                        last_time = now
                    if now - window_time > 3:
                        window_time, window_bytes = now, 0
        finally:
            # Persist bytes before reporting the final checkpoint, including cancellation.
            f.flush()
            os.fsync(f.fileno())
            progress(offset, 0)
            if stream is not None:
                await stream.close()
    if offset != expected:
        raise TransferError('Download ended early. Press Start to resume.')
    # A complete download is checked separately by WinRAR, which detects the
    # actual format (including ZIP content named .rar) and can quarantine damage.
    # Same-directory rename. Never silently overwrite an existing destination.
    if target.exists():
        raise TransferError('Destination appeared during download. Move it away and retry.')
    if os.name == 'nt':
        os.rename(part, target)  # Windows fails if destination exists.
    else:
        os.link(part, target)
        part.unlink()
