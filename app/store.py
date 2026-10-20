import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet

def now():
    return datetime.now(timezone.utc).isoformat()

class Store:
    """Encrypted JSON records, private files, append-only public source versions.

    SQLite remains local to one user. It is not a multi-tenant cloud database.
    """
    def __init__(self, directory: Path, encryption_key=""):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)
        keyfile = directory / "encryption.key"
        if not encryption_key and not keyfile.exists():
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        self.cipher = Fernet(encryption_key.encode() if encryption_key else keyfile.read_bytes())
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / "app.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body BLOB NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(kind,id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, body BLOB NOT NULL)")
        self.db.commit()

    def encode(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def decode(self, value):
        return json.loads(self.cipher.decrypt(value))

    def get(self, kind, id, default=None):
        with self.lock:
            row = self.db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, id)).fetchone()
            return self.decode(row[0]) if row else default

    def put(self, kind, id, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,updated=excluded.updated", (kind, id, self.encode(value), now()))
        return value

    def all(self, kind):
        with self.lock:
            return [self.decode(r[0]) for r in self.db.execute("SELECT body FROM records WHERE kind=? ORDER BY updated DESC", (kind,)).fetchall()]

    def delete(self, kind, id):
        with self.lock, self.db:
            self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, id))

    def event(self, job_id, agent, message, **details):
        # Never include profile values, document contents or API keys here.
        event = {"time": now(), "agent": agent, "message": message, **details}
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(job_id,body) VALUES (?,?)", (job_id, self.encode(event)))
        return event

    def events(self, job_id, after=0):
        with self.lock:
            rows = self.db.execute("SELECT seq,body FROM events WHERE job_id=? AND seq>? ORDER BY seq", (job_id, after)).fetchall()
            return [{"seq": seq, **self.decode(body)} for seq, body in rows]

    def close(self):
        self.db.close()

    def put_blob(self, id, encrypted):
        directory = self.directory / "documents"
        directory.mkdir(exist_ok=True)
        (directory / id).write_bytes(encrypted)

    def get_blob(self, id):
        return (self.directory / "documents" / id).read_bytes()

    def delete_blob(self, id):
        (self.directory / "documents" / id).unlink(missing_ok=True)
