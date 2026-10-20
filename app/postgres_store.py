"""Single-owner encrypted persistence. Never put plaintext files into PostgreSQL.

Each operation owns a connection, so idle serverless database disconnects are safe.
There are no automatic write retries (an uncertain write must not be duplicated).
"""
import threading
from contextlib import contextmanager
from cryptography.fernet import Fernet
from .store import Store, now

class PostgresStore(Store):
    def __init__(self, directory, database_url, encryption_key):
        import psycopg
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.cipher = Fernet(encryption_key.encode())
        self.lock = threading.RLock()
        self.url = database_url
        self.connect = psycopg.connect
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body BYTEA NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS events (seq BIGSERIAL PRIMARY KEY, job_id TEXT, body BYTEA NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS events_job_seq ON events(job_id,seq)")
            db.execute("CREATE TABLE IF NOT EXISTS private_blobs (id TEXT PRIMARY KEY, body BYTEA NOT NULL)")
        # Fail fast if the configured key cannot decrypt an existing installation.
        self.all("profile")

    @contextmanager
    def connection(self):
        with self.lock, self.connect(self.url, connect_timeout=15) as db:
            db.execute("SET statement_timeout = '20s'")
            yield db

    def get(self, kind, id, default=None):
        with self.connection() as db:
            row = db.execute("SELECT body FROM records WHERE kind=%s AND id=%s", (kind,id)).fetchone()
            return self.decode(bytes(row[0])) if row else default

    def put(self, kind, id, value):
        with self.connection() as db:
            db.execute("INSERT INTO records VALUES (%s,%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,updated=excluded.updated", (kind,id,self.encode(value),now()))
        return value

    def all(self, kind):
        with self.connection() as db:
            return [self.decode(bytes(r[0])) for r in db.execute("SELECT body FROM records WHERE kind=%s ORDER BY updated DESC", (kind,)).fetchall()]

    def delete(self, kind, id):
        with self.connection() as db:
            db.execute("DELETE FROM records WHERE kind=%s AND id=%s", (kind,id))

    def event(self, job_id, agent, message, **details):
        event = {"time":now(), "agent":agent, "message":message, **details}
        with self.connection() as db:
            db.execute("INSERT INTO events(job_id,body) VALUES (%s,%s)", (job_id,self.encode(event)))
        return event

    def events(self, job_id, after=0):
        with self.connection() as db:
            return [{"seq":seq, **self.decode(bytes(body))} for seq,body in db.execute("SELECT seq,body FROM events WHERE job_id=%s AND seq>%s ORDER BY seq", (job_id,after)).fetchall()]

    def put_blob(self, id, encrypted):
        with self.connection() as db:
            db.execute("INSERT INTO private_blobs VALUES (%s,%s) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (id,encrypted))

    def get_blob(self, id):
        with self.connection() as db:
            row = db.execute("SELECT body FROM private_blobs WHERE id=%s", (id,)).fetchone()
            if not row:
                raise FileNotFoundError("Document is unavailable")
            return bytes(row[0])

    def delete_blob(self, id):
        with self.connection() as db:
            db.execute("DELETE FROM private_blobs WHERE id=%s", (id,))

    def close(self):
        pass

    def claim_browser(self):
        # Use a direct (not transaction-pooled) PostgreSQL URL: this session owns
        # the advisory lock until its browser closes. It covers rolling deploys.
        db = self.connect(self.url, connect_timeout=15, autocommit=True)
        try:
            if not db.execute('SELECT pg_try_advisory_lock(1935761713)').fetchone()[0]:
                raise RuntimeError('Another server is running an application browser. Wait for it to close.')
            return db
        except Exception:
            db.close()
            raise

    def save_document(self, metadata, encrypted):
        """Replace one document type atomically, including its encrypted blob."""
        with self.connection() as db:
            old = [self.decode(bytes(r[0])) for r in db.execute("SELECT body FROM records WHERE kind='document' FOR UPDATE").fetchall()]
            db.execute("INSERT INTO private_blobs VALUES (%s,%s)", (metadata['blob'],encrypted))
            db.execute("INSERT INTO records VALUES ('document',%s,%s,%s)", (metadata['id'],self.encode(metadata),now()))
            for document in old:
                if document['document_type'] == metadata['document_type'] and not document.get('is_demo'):
                    db.execute("DELETE FROM records WHERE kind='document' AND id=%s", (document['id'],))
                    db.execute("DELETE FROM private_blobs WHERE id=%s", (document['blob'],))

    def remove_document(self, document):
        with self.connection() as db:
            db.execute("DELETE FROM records WHERE kind='document' AND id=%s", (document['id'],))
            db.execute("DELETE FROM private_blobs WHERE id=%s", (document['blob'],))
