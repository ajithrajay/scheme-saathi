"""Real Chromium tests against the local practice portal; no external applications."""
import socket
import threading
import time
from pathlib import Path
import httpx
import pytest
import uvicorn
from fastapi.responses import HTMLResponse
from app.config import Config
from app.main import create_app

@pytest.fixture
def live(tmp_path):
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    app=create_app(Config(data_dir=tmp_path,token='browser-test',port=port,headless=True))
    @app.get('/demo/challenge',response_class=HTMLResponse)
    async def challenge():
        return '<h1>Verify OTP</h1><label>OTP<input name="otp" required></label><button>Submit</button>'
    @app.get('/demo/unknown',response_class=HTMLResponse)
    async def unknown():
        return '<h1>Extra details</h1><label>Unlisted qualification<input name="unlisted" required></label><button>Next</button>'
    @app.get('/demo/no-receipt',response_class=HTMLResponse)
    async def no_receipt():
        return '<h1>Review</h1><button onclick="this.remove();document.body.innerHTML=\'<h1>Application submitted successfully</h1>\'">Submit application</button>'
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-Saathi-Request':'1'},timeout=20,trust_env=False) as c:
        assert c.post('/api/bootstrap',json={'token':'browser-test'}).status_code==200
        c.post('/api/demo/setup')
        yield c,app
    server.should_exit=True;thread.join(timeout=15)

def wait_job(c,id,expected,limit=25):
    until=time.time()+limit
    last={}
    while time.time()<until:
        jobs=c.get('/api/applications?demo=true').json()
        last=next(j for j in jobs if j['id']==id)
        if last['status'] in expected:return last
        if last['status']=='BROWSER_ERROR':break
        time.sleep(.15)
    raise AssertionError(last)

def start(c,submit=True):
    r=c.post('/api/applications',json={'scheme_id':'demo-education-grant','demo':True,'auto_submit':submit})
    assert r.status_code==200,r.text
    return r.json()['id']

def test_full_application_and_idempotency(live):
    c,app=live
    id=start(c)
    job=wait_job(c,id,{'SUBMITTED'})
    assert job['receipt'].startswith('SS-DEMO-')
    assert c.get('/api/applications/'+id+'/screenshot').headers['content-type']=='image/png'
    assert start(c)==id
    c.post('/api/applications/'+id+'/resume',json={'auto_submit':True})
    assert len(app.state.store.all('demo_receipt'))==1
    events=c.get('/api/events/'+id).json()
    filled={field for e in events for field in e.get('fields',[])}
    assert {'full_name','state','age','annual_household_income_inr','doc:study_certificate'}<=filled
    assert sum(e.get('status')=='SUBMITTING' for e in events)==1

def test_review_pause_then_resume(live):
    c,app=live
    id=start(c,False)
    job=wait_job(c,id,{'READY_FOR_SUBMISSION'})
    assert not job['submission_attempted']
    assert not app.state.store.all('demo_receipt')
    c.post('/api/applications/'+id+'/resume',json={'auto_submit':True})
    assert wait_job(c,id,{'SUBMITTED'})['receipt']

@pytest.mark.parametrize('path,status',[('/demo/challenge','VERIFICATION_REQUIRED'),('/demo/unknown','NEEDS_INFORMATION'),('/demo/no-receipt','SUBMISSION_UNVERIFIED')])
def test_challenge_missing_field_and_unverified_submission(live,path,status):
    c,app=live
    s=app.state.store.get('scheme','demo-education-grant');s['portal_url']=path;app.state.store.put('scheme',s['id'],s)
    id=start(c)
    job=wait_job(c,id,{status})
    if status=='SUBMISSION_UNVERIFIED':
        c.post('/api/applications/'+id+'/resume',json={'auto_submit':True})
        wait_job(c,id,{status})
        assert sum(e.get('status')=='SUBMITTING' for e in c.get('/api/events/'+id).json())==1
    else:
        assert not job['submission_attempted']

@pytest.mark.parametrize('draft', [False, True])
def test_auto_queue_after_assessment(live, draft):
    c,app=live
    if draft:
        scheme = app.state.store.get('scheme', 'demo-education-grant')
        scheme.update(coverage_complete=False, review_status='DRAFT', window_status='UNKNOWN',
                      window_start=None, window_end=None)
        app.state.store.put('scheme', scheme['id'], scheme)
    r=c.post('/api/assessments',json={'demo':True,'auto_apply':True,'auto_submit':True,'language':'ta'})
    for _ in range(50):
        jobs=c.get('/api/applications?demo=true').json()
        if jobs:break
        time.sleep(.1)
    assert jobs
    expected = 'SUBMISSION_BLOCKED' if draft else 'SUBMITTED'
    job = wait_job(c, jobs[0]['id'], {expected})
    if draft:
        assert not job['submission_attempted']
    else:
        assert job['receipt']
    assessment = c.get('/api/assessments/' + r.json()['id']).json()
    assert assessment['application_summary']['queued'] == [job['id']]
    assert assessment['application_summary']['skipped'] == []

@pytest.mark.parametrize('auto_submit', [False, True])
def test_provisional_preparation_fills_then_stops_before_submission(live, auto_submit):
    c, app = live
    store = app.state.store
    scheme = store.get('scheme', 'demo-education-grant')
    scheme.update(coverage_complete=False, review_status='DRAFT', window_status='UNKNOWN',
                  window_start=None, window_end=None)
    store.put('scheme', scheme['id'], scheme)
    id = start(c, auto_submit)
    job = wait_job(c, id, {'SUBMISSION_BLOCKED'})
    assert job['preparation_only'] and not job['submission_attempted']
    assert len(job['submission_blockers']) == 2
    assert not store.all('demo_receipt')
    events = c.get('/api/events/' + id).json()
    filled = {field for event in events for field in event.get('fields', [])}
    assert {'full_name', 'state', 'age', 'annual_household_income_inr', 'doc:study_certificate'} <= filled
    # Asking to submit does not bypass the unresolved checks.
    c.post('/api/applications/' + id + '/resume', json={'auto_submit': True})
    for _ in range(60):
        blocked_events = [e for e in c.get('/api/events/' + id).json() if e.get('status') == 'SUBMISSION_BLOCKED']
        if len(blocked_events) >= 2:
            break
        time.sleep(.1)
    assert len(blocked_events) >= 2
    wait_job(c, id, {'SUBMISSION_BLOCKED'})
    assert not store.all('demo_receipt')
    # Once the fictional policy and dates are reviewed, the same prepared form resumes.
    scheme.update(coverage_complete=True, review_status='REVIEWED', window_status='OPEN',
                  window_start='2020-01-01', window_end='2099-12-31')
    store.put('scheme', scheme['id'], scheme)
    c.post('/api/applications/' + id + '/resume', json={'auto_submit': True})
    assert wait_job(c, id, {'SUBMITTED'})['receipt']
    assert len(store.all('demo_receipt')) == 1
