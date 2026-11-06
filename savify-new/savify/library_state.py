"""Durable audit checkpoints, lookup cache, and a single-process batch lock."""
from contextlib import contextmanager
import csv
import json
import os
from pathlib import Path
import sqlite3
import time


class AuditState:
    policy = 'fingerprint-audit-v1'

    def __init__(self, directory, root):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.directory / 'audit.sqlite3'))
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, digest TEXT,
                policy TEXT, status TEXT, detail TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS cache (kind TEXT, key TEXT, value TEXT,
                updated REAL, PRIMARY KEY(kind, key));
            CREATE TABLE IF NOT EXISTS replacements (id INTEGER PRIMARY KEY,
                path TEXT, old_digest TEXT, new_digest TEXT, backup TEXT,
                source_url TEXT, status TEXT, updated REAL,
                verification TEXT NOT NULL DEFAULT 'fingerprint');
        ''')
        if 'verification' not in {row[1] for row in self.db.execute('PRAGMA table_info(replacements)')}:
            with self.db:
                self.db.execute("ALTER TABLE replacements ADD COLUMN verification TEXT NOT NULL DEFAULT 'fingerprint'")
        old_root = self.db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
        if old_root and old_root[0] != str(Path(root).resolve()):
            self.close()
            raise ValueError('This state directory belongs to a different music root; choose another --state-dir.')
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('root', ?)", (str(Path(root).resolve()),))

    def close(self):
        self.db.close()

    def cached_file(self, path, digest):
        row = self.db.execute('SELECT digest, policy, status, detail FROM files WHERE path=?', (str(path),)).fetchone()
        if row and row[0] == digest and row[1] == self.policy and row[2] in ('verified', 'repaired'):
            return json.loads(row[3])
        return None

    def record(self, path, digest, result):
        detail = json.dumps(result, ensure_ascii=False)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)',
                            (str(path), digest, self.policy, result['status'], detail, time.time()))
        # SQLite is authoritative; a failed report append does not erase a repair.
        self.log_event('file_result', **result)

    def log_event(self, event, **details):
        with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps(dict(time=time.time(), event=event, **details), ensure_ascii=False) + '\n')

    def cache_get(self, kind, key, max_age):
        row = self.db.execute('SELECT value, updated FROM cache WHERE kind=? AND key=?', (kind, key)).fetchone()
        if row and time.time() - row[1] <= max_age:
            return json.loads(row[0])
        return None

    def cache_put(self, kind, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)',
                            (kind, key, json.dumps(value, ensure_ascii=False), time.time()))

    def prepare_replacement(self, path, old_digest, new_digest, backup, source_url, verification='fingerprint'):
        with self.db:
            cursor = self.db.execute('INSERT INTO replacements VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)',
                                     (str(path), old_digest, new_digest, str(backup), source_url, 'prepared',
                                      time.time(), verification))
        return cursor.lastrowid

    def commit_replacement(self, replacement_id):
        with self.db:
            self.db.execute("UPDATE replacements SET status='committed', updated=? WHERE id=?",
                            (time.time(), replacement_id))

    def replacement_for(self, path, digest):
        row = self.db.execute('SELECT id, backup, source_url, status, verification FROM replacements '
                              'WHERE path=? AND new_digest=? ORDER BY id DESC LIMIT 1',
                              (str(path), digest)).fetchone()
        if row:
            if row[3] == 'prepared':
                self.commit_replacement(row[0])  # Interrupted after atomic replacement.
            return {'backup': row[1], 'source_url': row[2], 'verification': row[4]}
        return None

    def export_report(self):
        target = self.directory / 'report.csv'
        temporary = target.with_suffix('.csv.tmp')
        keys = ['path', 'status', 'artist', 'title', 'track_id', 'reason', 'duration',
                'original_duration', 'expected_duration', 'duration_tolerance', 'repair_trigger',
                'catalog_note', 'verification', 'score', 'identified_as', 'backup', 'source_url', 'discarded_tags']
        with temporary.open('w', encoding='utf-8-sig', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=keys, extrasaction='ignore')
            writer.writeheader()
            for (detail,) in self.db.execute('SELECT detail FROM files ORDER BY path'):
                result = json.loads(detail)
                result['discarded_tags'] = '; '.join(result.get('discarded_tags') or [])
                # Quoted CSV is not enough to stop spreadsheet formula evaluation.
                for key, value in result.items():
                    if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                        result[key] = "'" + value
                writer.writerow(result)
        temporary.replace(target)
        return target


@contextmanager
def audit_lock(directory):
    """OS releases this lock on a crash; a stale lockfile never blocks a resume."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'audit.lock').open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another audit is using this state directory.') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
