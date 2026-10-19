"""Windows user-scoped DPAPI. No plaintext fallback, including on other OSes."""
import ctypes
from contextlib import closing
import json
import os
import re
import sqlite3
from pathlib import Path
from .storage import atomic_write


def validate_keys(api_id, api_hash):
    api_id = int(api_id)
    api_hash = str(api_hash).strip()
    if not 0 < api_id < 2**31 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise ValueError("API ID must be a positive integer; API Hash must contain 32 hexadecimal characters.")
    return api_id, api_hash


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Secure DPAPI storage requires Windows. Use --demo to preview the interface.")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.POINTER(Blob)
    crypt32.CryptProtectData.argtypes = [ptr, wintypes.LPCWSTR, ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ptr]
    crypt32.CryptUnprotectData.argtypes = [ptr, ctypes.c_void_p, ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ptr]
    crypt32.CryptProtectData.restype = crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), buf), Blob()
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    desc = None if decrypt else "Telegram ZIP Desktop"
    if not fn(ctypes.byref(source), desc, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


class Vault:
    def __init__(self, path):
        self.path = path

    def load(self):
        if not self.path.exists():
            return {}
        return json.loads(_crypt(self.path.read_bytes(), decrypt=True).decode("utf-8"))

    def save(self, values):
        atomic_write(self.path, _crypt(json.dumps(values).encode("utf-8")))


def import_legacy_session(path):
    """Read only the auth key from an old SQLite session; never modify that file."""
    from telethon.sessions import StringSession
    from telethon.crypto import AuthKey
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        row = db.execute("SELECT dc_id, server_address, port, auth_key FROM sessions LIMIT 1").fetchone()
    if not row or not row[3] or len(row[3]) != 256:
        raise ValueError("This file does not contain a valid authorized Telethon session.")
    session = StringSession()
    session.set_dc(row[0], row[1], row[2])
    session.auth_key = AuthKey(row[3])
    return session.save()
