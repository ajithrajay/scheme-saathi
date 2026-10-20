"""Offline Chromium integration; every official URL is fulfilled locally.
No personal data or fabricated applications are sent to government servers.
"""
import base64
import time
from pathlib import Path
import pytest
from playwright.async_api import Page
from playwright.sync_api import sync_playwright, expect
from app import horticulture as kit
from test_browser_e2e import live, start, wait_job
from fastapi.responses import HTMLResponse

FACTS = {'full_name':'Fixture Applicant','state':'Tamil Nadu','aadhaar_number':'222222222222',
'relation_name':'Fixture Parent','gender':'female','kit_category':'General','phone':'9000000000',
'area_type':'urban','district':'Chennai','block':'Anna nagar','address':'Fictional test address','pincode':'600001'}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')

@pytest.fixture
def kit_live(live, monkeypatch):
    client, app = live
    html = (Path(__file__).parent/'fixtures/horticulture.html').read_text(encoding='utf-8')
    fixture = {'html':html, 'requests':[]}
    original = Page.goto
    async def go(page,url,*args,**kwargs):
        if url.startswith(kit.PORTAL):
            async def serve(route):
                fixture['requests'].append(route.request.method)
                await route.fulfill(status=200,content_type='text/html',body=fixture['html'])
            await page.route('https://tnhorticulture.tn.gov.in/**', serve)
        return await original(page,url,*args,**kwargs)
    monkeypatch.setattr(Page,'goto',go)
    assert client.put('/api/profile',json={'facts':FACTS}).status_code==200
    assert client.post('/api/documents',data={'document_type':'beneficiary_photo'},files={'file':('fixture.png',PNG,'image/png')}).status_code==200
    return client,app,fixture


def begin(client,auto=False,consent=True):
    return client.post('/api/horticulture/applications',json={'kit':'vegetable','disclosure_confirmed':consent,'auto_submit':auto})


def wait_kit(client,jid,states,limit=30):
    last={}
    end=time.time()+limit
    while time.time()<end:
        last=next(j for j in client.get('/api/applications').json() if j['id']==jid)
        if last['status'] in states:return last
        if last['status']=='BROWSER_ERROR':break
        time.sleep(.15)
    raise AssertionError(last)


def test_kit_ui_review_submit_and_no_duplicates(kit_live):
    client,app,fixture=kit_live
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        page.goto(str(client.base_url)+'?access=browser-test')
        page.get_by_role('button',name='TN garden kits',exact=True).click()
        expect(page.get_by_role('button',name='Fill official form for review')).to_be_disabled()
        page.get_by_role('checkbox').first.check()
        page.get_by_role('button',name='Fill official form for review').click()
        card=page.locator('.application-card')
        expect(card).to_contain_text('Ready to submit',timeout=30000)
        assert not app.state.store.all('application')[0]['submission_attempted']
        card.get_by_role('button',name='Submit application',exact=True).click()
        expect(card).to_contain_text('FIXTURE-KIT-000001',timeout=30000)
        expect(card.get_by_role('link',name='Check official application status')).to_have_attribute('href',kit.TRACK)
        browser.close()
    job=client.get('/api/applications').json()[0]
    assert job['status']=='SUBMITTED'
    assert begin(client,True).json()['id']==job['id']
    client.post('/api/applications/'+job['id']+'/resume',json={'auto_submit':True})
    assert sum(e.get('status')=='SUBMITTING' for e in client.get('/api/events/'+job['id']).json())==1
    assert fixture['requests']==['GET']


def test_consent_and_readiness(kit_live):
    client,app,_=kit_live
    assert begin(client,consent=False).status_code==422
    assert not app.state.store.all('application')
    assert client.get('/api/horticulture/readiness').json()['can_prepare']
    client.put('/api/profile',json={'facts':{**FACTS,'area_type':'rural'}})
    assert 'village' in client.get('/api/horticulture/readiness').json()['missing_fields']
    assert begin(client).status_code==422


def test_disabled_kit_never_changes_choice_or_submits(kit_live):
    client,app,fixture=kit_live
    fixture['html']=fixture['html'].replace('type="checkbox" data-select-id="vegKitReqQty"','type="checkbox" disabled data-select-id="vegKitReqQty"')
    jid=begin(client,True).json()['id']
    job=wait_kit(client,jid,{'NEEDS_INFORMATION'})
    assert 'unavailable' in job['message']
    assert not job['submission_attempted']


def test_receipt_missing_never_retries(kit_live):
    client,app,fixture=kit_live
    fixture['html']=fixture['html'].replace('Application Number: FIXTURE-KIT-000001','Response pending')
    jid=begin(client,True).json()['id']
    wait_kit(client,jid,{'SUBMISSION_UNVERIFIED'})
    client.post('/api/applications/'+jid+'/resume',json={'auto_submit':True})
    time.sleep(.7)
    assert sum(e.get('status')=='SUBMITTING' for e in client.get('/api/events/'+jid).json())==1
    assert client.post('/api/applications/'+jid+'/cancel',json={}).status_code==409


def test_profile_change_requires_fresh_preparation(kit_live):
    client,app,_=kit_live
    jid=begin(client).json()['id']
    wait_kit(client,jid,{'READY_FOR_SUBMISSION'})
    client.put('/api/profile',json={'facts':{**FACTS,'full_name':'Changed Fixture'}})
    client.post('/api/applications/'+jid+'/resume',json={'auto_submit':True})
    assert not wait_kit(client,jid,{'PROFILE_CHANGED'})['submission_attempted']


def test_unknown_new_control_stops_before_filling(kit_live):
    client,app,fixture=kit_live
    fixture['html']=fixture['html'].replace('<form id="kitRegForm">','<form id="kitRegForm"><input id="new-declaration" type="checkbox" required>')
    jid=begin(client,True).json()['id']
    job=wait_kit(client,jid,{'NEEDS_INFORMATION'})
    assert 'additional control' in job['message'] and not job['submission_attempted']


def test_delayed_nsp_style_verification_is_not_unsupported(live):
    client,app=live
    @app.get('/demo/delayed-login',response_class=HTMLResponse)
    async def delayed():
        return '''<h1>Loading</h1><script>setTimeout(()=>document.body.innerHTML='<h1>Login with Aadhaar</h1><input placeholder="Enter Aadhaar"><button>Get OTP</button><p>Enter OTP</p>'+Array(6).fill('<input maxlength="1">').join('')+'<input placeholder="Enter Captcha">',400)</script>'''
    scheme=app.state.store.get('scheme','demo-education-grant');scheme['portal_url']='/demo/delayed-login';app.state.store.put('scheme',scheme['id'],scheme)
    jid=start(client)
    assert not wait_job(client,jid,{'VERIFICATION_REQUIRED'})['submission_attempted']
