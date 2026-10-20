"""Run against a disposable database: TEST_DATABASE_URL. Never use production."""
import os
import uuid
import pytest
from cryptography.fernet import Fernet, InvalidToken
from app.postgres_store import PostgresStore

@pytest.fixture
def db(tmp_path):
    url=os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL not configured; CI supplies PostgreSQL 16')
    key=Fernet.generate_key().decode()
    store=PostgresStore(tmp_path,url,key)
    store.test_key=key
    return store

def test_postgres_encryption_restart_and_blobs(db,tmp_path):
    id=uuid.uuid4().hex;kind='test-'+id
    try:
        db.put(kind,id,{'secret':'citizen-private-value'})
        db.put_blob(id,db.cipher.encrypt(b'document-private-value'))
        db.event(id,'test','event')
        reopened=PostgresStore(tmp_path/'new-process',db.url,db.test_key)
        assert reopened.get(kind,id)=={'secret':'citizen-private-value'}
        assert reopened.cipher.decrypt(reopened.get_blob(id))==b'document-private-value'
        assert reopened.events(id)[0]['message']=='event'
        with db.connection() as conn:
            raw=bytes(conn.execute('SELECT body FROM records WHERE kind=%s AND id=%s',(kind,id)).fetchone()[0])
            assert b'citizen-private-value' not in raw
        with pytest.raises(InvalidToken):
            Fernet(Fernet.generate_key()).decrypt(raw)
    finally:
        db.delete(kind,id);db.delete_blob(id)
        with db.connection() as conn:
            conn.execute('DELETE FROM events WHERE job_id=%s',(id,))

def test_global_browser_mutex(db):
    first=db.claim_browser()
    try:
        with pytest.raises(RuntimeError,match='Another server'):
            db.claim_browser()
        first.execute('SELECT 1')
    finally:
        first.close()
    db.claim_browser().close()

def test_document_replacement_atomic(db):
    kind='test-doc-'+uuid.uuid4().hex
    first={'id':uuid.uuid4().hex,'blob':uuid.uuid4().hex,'document_type':kind,'is_demo':False}
    second={**first,'id':uuid.uuid4().hex,'blob':uuid.uuid4().hex}
    try:
        db.save_document(first,db.cipher.encrypt(b'old'))
        db.save_document(second,db.cipher.encrypt(b'new'))
        assert db.get('document',first['id']) is None
        assert db.cipher.decrypt(db.get_blob(second['blob']))==b'new'
        with pytest.raises(FileNotFoundError):
            db.get_blob(first['blob'])
        # A duplicate primary key fails without deleting the existing file.
        with pytest.raises(Exception):
            db.save_document(second,db.cipher.encrypt(b'bad'))
        assert db.cipher.decrypt(db.get_blob(second['blob']))==b'new'
    finally:
        db.remove_document(first);db.remove_document(second)
