import asyncio
import io
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from app.config import Config
from app.main import create_app
from app.models import FIELDS
from app.resume_demo import demo_facts,document_catalogue,seed_demo
from app.tn_portal_data import PORTALS
from app.horticulture import REQUIRED

@pytest.fixture
def client(tmp_path):
    app=create_app(Config(data_dir=tmp_path,token='demo-test',headless=True,demo_default=True))
    with TestClient(app) as c:
        c.post('/api/bootstrap',json={'token':'demo-test'})
        c.headers['X-Saathi-Request']='1'
        yield c


def test_complete_demo_and_document_previews(client):
    assert client.get('/api/health').json()['demo_default'] is True
    facts=client.get('/api/profile?demo=true').json()['facts']
    assert len(facts)==len(FIELDS)-1
    assert 'current_class' not in facts  # College student, not a school applicant.
    required=set(REQUIRED)|{field for portal in PORTALS.values() for field in portal['required']}
    assert required <= facts.keys()
    assert client.get('/api/profile').json()=={'facts':{}}
    documents=client.get('/api/documents?demo=true').json()
    assert len(documents)==13
    for document in documents:
        response=client.get('/api/demo/documents/'+document['id'])
        assert response.status_code==200
        assert 'no-store' in response.headers['cache-control']
        if document['mime_type']=='application/pdf':
            pdf=PdfReader(io.BytesIO(response.content))
            assert len(pdf.pages)==1
            assert 'NOT VALID FOR OFFICIAL USE' in pdf.pages[0].extract_text()
        else:assert response.content.startswith(b'\x89PNG')
    assert client.get('/api/documents').json()==[]
    for portal in client.get('/api/tn-portals?demo=true').json()['portals']:
        assert not portal['missing_fields']
        assert not portal['missing_documents']
        assert portal['can_prepare']
    assert client.get('/api/horticulture/readiness?demo=true').json()['can_prepare']


def test_demo_seed_preserves_real_data_and_demo_edits(client):
    client.put('/api/profile',json={'facts':{'full_name':'Actual workspace sentinel'}})
    upload=client.post('/api/documents',data={'document_type':'study_certificate'},files={'file':('real-test.pdf',b'%PDF-1.4\nSENTINEL','application/pdf')}).json()
    client.put('/api/profile?demo=true',json={'facts':{**demo_facts(),'annual_household_income_inr':190000}})
    for _ in range(2):client.post('/api/demo/setup',json={})
    assert client.get('/api/profile').json()['facts']['full_name']=='Actual workspace sentinel'
    assert client.get('/api/documents').json()[0]['id']==upload['id']
    assert client.get('/api/demo/documents/'+upload['id']).status_code==404
    assert client.get('/api/profile?demo=true').json()['facts']['annual_household_income_inr']==190000
    assert len(client.get('/api/documents?demo=true').json())==13


def test_copied_demo_identity_cannot_open_official_browser(client):
    client.put('/api/profile',json={'facts':demo_facts()})
    runner=client.app.state.browser
    with pytest.raises(ValueError,match='Fictional demo data'):
        asyncio.run(runner.open({'id':'test-block','demo':False,'scheme_id':'any'}))
    assert runner.playwright is None
    assert not runner.contexts


def test_demo_default_can_be_disabled_without_removing_citizen_data(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path,token='test',demo_default=False))) as c:
        c.post('/api/bootstrap',json={'token':'test'})
        assert c.get('/api/health').json()['demo_default'] is False
        assert c.get('/api/profile?demo=true').json()=={'facts':{}}


def test_existing_110_demo_document_is_upgraded(client):
    store=client.app.state.store
    document=next(d for d in store.all('document') if d['document_type']=='study_certificate')
    document.update(filename='FICTIONAL_study_certificate.txt',mime_type='text/plain')
    store.put('document',document['id'],document)
    store.put_blob(document['id'],store.cipher.encrypt(b'Old fictional sample'))
    client.post('/api/demo/setup',json={})
    documents=client.get('/api/documents?demo=true').json()
    assert len(documents)==13
    upgraded=next(d for d in documents if d['document_type']=='study_certificate')
    assert upgraded['id']==document['id']
    assert upgraded['mime_type']=='application/pdf'
    assert client.get('/api/demo/documents/'+document['id']).content.startswith(b'%PDF')
