"""Durable, account-scoped queue; no Telegram credentials in this database."""
import json
import os
import re
import sqlite3
from pathlib import Path

CHUNK = 512 * 1024


def parallel_downloads(value=None):
    """Use a bounded pool even if settings were edited outside the app."""
    try:
        return max(1, min(20, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 20


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
    extension = archive_extension(name, None) or '.zip'
    stem = name[:-4] if name.lower().endswith(extension) else name
    stem = stem[:85].rstrip(" .") or "archive"
    if stem.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        stem = "_" + stem
    return stem + extension


def archive_extension(name, mime):
    """Select ZIP/RAR attachments; an explicit filename wins over MIME metadata."""
    if name:
        return next((extension for extension in ('.zip', '.rar')
                     if name.lower().endswith(extension)), None)
    mime = (mime or '').split(';', 1)[0].strip().lower()
    if mime in {'application/zip', 'application/x-zip-compressed'}:
        return '.zip'
    if mime in {'application/vnd.rar', 'application/x-rar-compressed', 'application/x-rar', 'application/rar'}:
        return '.rar'
    return None


def resume_offset(path, expected):
    size = path.stat().st_size if path.exists() else 0
    if size > expected:
        raise ValueError("Partial file exceeds expected size")
    return size if size == expected else size // CHUNK * CHUNK


class Store:
    def __init__(self, path):
        self.progress_pending = {}
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
        with self.db:
            columns = {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
            for column, default in [('verification', 'pending'), ('verification_error', ''), ('quarantine_path', '')]:
                if column not in columns:
                    self.db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
            if self.db.execute('PRAGMA user_version').fetchone()[0] < 1:
                # Earlier versions skipped RAR attachments. Revisit history once,
                # without discarding completed ZIPs, partial files or queued jobs.
                self.db.execute('UPDATE scans SET cursor=0')
                self.db.execute('PRAGMA user_version=1')
            self.db.execute("UPDATE jobs SET state='queued' WHERE state='downloading'")
            self.db.execute("UPDATE jobs SET verification='pending' WHERE verification='checking'")
            self.db.execute("CREATE INDEX IF NOT EXISTS pending_archive_checks ON jobs(account,chat,message) WHERE state='done' AND verification='pending'")

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

    def stage_progress(self, account, chat, message, done):
        key = (account, chat, message)
        if key not in self.progress_pending:
            row = self.db.execute('SELECT done,size FROM jobs WHERE account=? AND chat=? AND message=?', key).fetchone()
            if row is None:
                raise ValueError('Unknown download')
            self.progress_pending[key] = [row['done'], row['size'], done]
        else:
            self.progress_pending[key][2] = done

    def flush_progress(self):
        if not self.progress_pending:
            return
        # One durable transaction for all active transfers, not one per callback.
        with self.db:
            self.db.executemany('UPDATE jobs SET done=? WHERE account=? AND chat=? AND message=?',
                                [(value[2], *key) for key, value in self.progress_pending.items()])
        self.progress_pending.clear()

    def update(self, account, chat, message, **values):
        if not values.keys() <= {"done", "state", "error", "verification", "verification_error", "quarantine_path"}:
            raise ValueError("Invalid update")
        key = (account, chat, message)
        if key in self.progress_pending:
            values.setdefault('done', self.progress_pending[key][2])
        with self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in values) +
                            " WHERE account=? AND chat=? AND message=?",
                            (*values.values(), account, chat, message))
        self.progress_pending.pop(key, None)

    def jobs(self, account, chat, pending=False, limit=None):
        query = "SELECT * FROM jobs WHERE account=? AND chat=?"
        if pending:
            query += " AND state!='done'"
        query += " ORDER BY message"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = [dict(row) for row in self.db.execute(query, (account, chat))]
        for row in rows:
            pending = self.progress_pending.get((account, chat, row['message']))
            if pending is not None:
                row['done'] = pending[2]
        return rows

    def next_job(self, account, chat, after=0):
        row = self.db.execute("SELECT * FROM jobs WHERE account=? AND chat=? AND message>? AND state!='done' ORDER BY message LIMIT 1",
                              (account, chat, after)).fetchone()
        return dict(row) if row else None

    def next_check_job(self, account, chat, after=0):
        row = self.db.execute("SELECT * FROM jobs WHERE account=? AND chat=? AND message>? AND state='done' AND verification!='quarantined' ORDER BY message LIMIT 1",
                              (account, chat, after)).fetchone()
        return dict(row) if row else None

    def next_pending_check(self, account, chat):
        row = self.db.execute("SELECT * FROM jobs WHERE account=? AND chat=? AND state='done' AND verification='pending' ORDER BY message LIMIT 1",
                              (account, chat)).fetchone()
        return dict(row) if row else None

    def pending_quarantines(self, account, chat):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM jobs WHERE account=? AND chat=? AND verification='quarantining'", (account, chat))]

    def summary(self, account, chat):
        row = self.db.execute('''SELECT COUNT(*) total, COALESCE(SUM(size),0) bytes,
            COALESCE(SUM(MIN(done,size)),0) received,
            COALESCE(SUM(state='done'),0) complete,
            COALESCE(SUM(state='error'),0) failed,
            COALESCE(SUM(verification='quarantined'),0) quarantined,
            COALESCE(SUM(verification='ok'),0) verified,
            COALESCE(SUM(state='done' AND verification='pending'),0) unchecked,
            COALESCE(SUM(verification='error'),0) check_failed
            FROM jobs WHERE account=? AND chat=?''', (account, chat)).fetchone()
        result = dict(row)
        result['received'] += sum(min(done, size) - min(saved, size)
                                  for (a, c, _), (saved, size, done) in self.progress_pending.items()
                                  if (a, c) == (account, chat))
        return result

    def close(self):
        try:
            self.flush_progress()
        finally:
            self.db.close()
