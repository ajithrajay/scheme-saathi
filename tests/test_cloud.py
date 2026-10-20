import os
from unittest.mock import patch
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.store import Store


def test_cloud_fails_without_durable_storage(tmp_path,monkeypatch):
    monkeypatch.setenv('SCHEMESAATHI_OWNER_KEY','x'*48)
    with pytest.raises(ValueError,match='DATABASE_URL'):
        create_app(Config(mode='cloud',public_url='https://saathi.example',data_dir=tmp_path))


def test_cloud_owner_security(tmp_path,monkeypatch):
    monkeypatch.setenv('SCHEMESAATHI_OWNER_KEY','x'*48)
    config=Config(mode='cloud',public_url='https://saathi.example',data_dir=tmp_path,
                  database_url='postgresql://placeholder/db?sslmode=require',encryption_key=Fernet.generate_key().decode())
    # Authentication test only. Real Postgres persistence is tested separately.
    with patch('app.postgres_store.PostgresStore',side_effect=lambda d,u,k:Store(d,k)):
        app=create_app(config)
    with TestClient(app,base_url='https://saathi.example') as c:
        assert c.get('/healthz').json()=={'status':'ok'}
        assert c.get('/api/profile').status_code==401
        assert c.get('/docs').status_code==404
        assert c.post('/api/bootstrap',json={'token':config.token}).status_code==403
        h={'X-Saathi-Request':'1','Origin':'https://attacker.example'}
        assert c.post('/api/bootstrap',json={'token':config.token},headers=h).status_code==403
        h['Origin']='https://saathi.example'
        r=c.post('/api/bootstrap',json={'token':config.token},headers=h)
        assert r.status_code==200
        cookie=r.headers['set-cookie']
        assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie
        assert config.token not in cookie
        assert c.get('/api/profile').status_code==200
        assert c.get('/api/profile',headers={'Host':'evil.example'}).status_code==400
        assert c.put('/api/profile',headers={**h,'Origin':'https://attacker.example'},json={'facts':{}}).status_code==403
        old=c.cookies.get('saathi_session')
        assert c.post('/api/logout',headers=h).status_code==200
        assert c.get('/api/profile',headers={'Cookie':'saathi_session='+old}).status_code==401


def test_login_rate_limit_and_large_body(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path,token='test'))) as c:
        for _ in range(10):
            assert c.post('/api/bootstrap',json={'token':'wrong'}).status_code==401
        assert c.post('/api/bootstrap',json={'token':'test'}).status_code==429
        r=c.post('/api/bootstrap',content=b'x'*(12*1024*1024+1))
        assert r.status_code==413


def test_local_persistence_restart(tmp_path):
    key=Fernet.generate_key().decode()
    s=Store(tmp_path,key);s.put('profile','citizen',{'name':'Private Applicant'})
    s.put_blob('file1',s.cipher.encrypt(b'private document'))
    s.close()
    s=Store(tmp_path,key)
    assert s.get('profile','citizen')['name']=='Private Applicant'
    assert s.cipher.decrypt(s.get_blob('file1'))==b'private document'
    s.close()
