"""Durable, account-scoped queue; no Telegram credentials in this database."""
import json
import os
import re
import sqlite3
from pathlib import Path

CHUNK = 512 * 1024


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def load_settings(path):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid settings")
        return value
    except (ValueError, UnicodeError):
        # Preserve the broken file for inspection; defaults remain usable.
        backup = path.with_suffix(".broken.json")
        os.replace(path, backup)
        return {}


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name)
    name = name.strip(" .")
    stem = name[:-4] if name.lower().endswith(".zip") else name
    stem = stem[:85].rstrip(" .") or "archive"
    if stem.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        stem = "_" + stem
    return stem + ".zip"


def zip_candidate(name, mime):
    # An explicit non-ZIP extension wins over unreliable MIME metadata.
    if name:
        return name.lower().endswith(".zip")
    return mime in {"application/zip", "application/x-zip-compressed"}


def resume_offset(path, expected):
    size = path.stat().st_size if path.exists() else 0
    if size > expected:
        raise ValueError("Partial file exceeds expected size")
    return size if size == expected else size // CHUNK * CHUNK


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
          account INTEGER, chat INTEGER, message INTEGER, document TEXT,
          name TEXT, size INTEGER, path TEXT, done INTEGER DEFAULT 0,
          state TEXT DEFAULT 'queued', error TEXT DEFAULT '',
          PRIMARY KEY(account, chat, message));
        CREATE TABLE IF NOT EXISTS scans (
          account INTEGER, chat INTEGER, cursor INTEGER DEFAULT 0,
          PRIMARY KEY(account, chat));
        ''')
        self.db.execute("UPDATE jobs SET state='queued' WHERE state='downloading'")
        self.db.commit()

    def cursor(self, account, chat):
        row = self.db.execute("SELECT cursor FROM scans WHERE account=? AND chat=?", (account, chat)).fetchone()
        return row[0] if row else 0

    def scanned(self, account, chat, message, item=None):
        with self.db:
            if item:
                self.db.execute('''INSERT OR IGNORE INTO jobs
                    (account,chat,message,document,name,size,path) VALUES (?,?,?,?,?,?,?)''',
                    (account, chat, message, str(item['document']), item['name'], item['size'], str(item['path'])))
            self.db.execute('''INSERT INTO scans VALUES (?,?,?) ON CONFLICT(account,chat)
                DO UPDATE SET cursor=MAX(cursor,excluded.cursor)''', (account, chat, message))

    def update(self, account, chat, message, **values):
        if not values.keys() <= {"done", "state", "error"}:
            raise ValueError("Invalid update")
        with self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in values) +
                            " WHERE account=? AND chat=? AND message=?",
                            (*values.values(), account, chat, message))

    def jobs(self, account, chat, pending=False, limit=None):
        query = "SELECT * FROM jobs WHERE account=? AND chat=?"
        if pending:
            query += " AND state!='done'"
        query += " ORDER BY message"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        return [dict(row) for row in self.db.execute(query, (account, chat))]

    def next_job(self, account, chat, after=0):
        row = self.db.execute("SELECT * FROM jobs WHERE account=? AND chat=? AND message>? AND state!='done' ORDER BY message LIMIT 1",
                              (account, chat, after)).fetchone()
        return dict(row) if row else None

    def summary(self, account, chat):
        row = self.db.execute('''SELECT COUNT(*) total, COALESCE(SUM(size),0) bytes,
            COALESCE(SUM(MIN(done,size)),0) received,
            COALESCE(SUM(state='done'),0) complete,
            COALESCE(SUM(state='error'),0) failed FROM jobs WHERE account=? AND chat=?''', (account, chat)).fetchone()
        return dict(row)

    def close(self):
        self.db.close()
