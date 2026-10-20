"""All government URLs intercepted; no fictional application leaves the tests."""
import asyncio
import time
from types import SimpleNamespace
import pytest
from playwright.async_api import Page
from playwright.sync_api import sync_playwright,expect
from app.demo_application import DemoNetwork,lookup_allowed
from app import horticulture
from test_browser_e2e import live

FORM='''<!doctype html><h1>Fixture page one</h1><main><label>Beneficiary Name<input id=benificiary_name></label>
<label>Gender<select id=gender><option value="">Choose</option><option value=f>Female</option></select></label>
<label>Photo<input type=file id=beneficiary_image></label>
<button type=button id=next>Next</button></main>
<script>document.querySelector('#next').onclick=()=>{document.querySelector('main').innerHTML='<h2>Review page two</h2><label>Address<textarea id=address></textarea></label><button id=btnSubmit>Submit</button>';document.querySelector('#btnSubmit').onclick=()=>fetch('/save',{method:'POST',body:'fictional'}).catch(()=>{})}</script>'''

@pytest.fixture
def portal(monkeypatch):
    original=Page.goto
    fixture={'html':FORM,'launches':0}
    async def goto(page,url,**kwargs):
        if url==horticulture.PORTAL:
            fixture['launches']+=1
            async def initial(route):await route.fulfill(status=200,content_type='text/html',body=fixture['html'])
            await page.route(url,initial,times=1)
        return await original(page,url,**kwargs)
    monkeypatch.setattr(Page,'goto',goto)
    return fixture

def begin(c,auto=False):
    return c.post('/api/horticulture/applications?demo=true',json={'kit':'vegetable','auto_submit':auto})

def wait(c,id):
    for _ in range(150):
        j=next(x for x in c.get('/api/applications?demo=true').json() if x['id']==id)
        if j['status'] not in {'QUEUED','RUNNING'}:return j
        time.sleep(.1)
    raise AssertionError(j)

def test_same_application_job_multistep_review_never_submits(live,portal):
    c,app=live
    c.put('/api/profile',json={'facts':{'full_name':'Real profile sentinel'}})
    assert begin(c,True).status_code==422
    id=begin(c).json()['id']
    job=wait(c,id)
    assert job['status']=='DEMO_REVIEW',job
    assert job['demo'] and job['official_demo'] and not job['submission_attempted'] and not job['auto_submit']
    assert 'address' in job['demo_filled'] and 'full_name' in job['demo_filled']
    assert begin(c).json()['id']==id
    assert c.get('/api/applications').json()==[]
    assert c.post('/api/applications/'+id+'/resume',json={'auto_submit':True}).status_code==422
    assert c.get('/api/browser/'+id+'/frame').status_code==200
    assert c.post('/api/browser/'+id+'/action',json={'action':'click'}).status_code==409
    assert c.post('/api/browser/'+id+'/action',json={'action':'scroll','delta':400}).status_code==200
    async def inspect():
        page=app.state.browser.contexts[id].pages[0]
        assert 'Kavya' not in await page.locator('#address').input_value() # address, not applicant name
        assert 'Demo' in await page.locator('#address').input_value()
        for method in ['GET','POST']:
            assert await page.evaluate("async method=>{try{await fetch('/save',{method});return false}catch{return true}}",method)
    asyncio.run_coroutine_threadsafe(inspect(),app.state.browser.tasks[id].get_loop()).result(10)
    assert c.get('/api/profile').json()['facts']['full_name']=='Real profile sentinel'
    assert not app.state.store.get('application',id).get('receipt')
    c.post('/api/applications/'+id+'/cancel',json={})
    assert id not in app.state.browser.contexts

def test_server_saved_next_is_blocked(live,portal):
    c,app=live
    portal['html']='''<label>Full name<input></label><button id=next>Next</button><script>document.querySelector('#next').onclick=()=>fetch('/save-draft',{method:'POST',body:'demo'}).catch(()=>{})</script>'''
    id=begin(c).json()['id']
    assert wait(c,id)['status']=='DEMO_BLOCKED'
    assert app.state.store.get('application',id)['demo_request_blocked']

def test_normal_ui_no_separate_preview_panel(live,portal):
    c,app=live
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True)
        try:
            page=b.new_page();page.goto(str(c.base_url).rstrip('/')+'/login')
            page.get_by_label('Owner key').fill('browser-test')
            page.get_by_role('button',name='Sign in',exact=True).click()
            page.get_by_role('button',name='TN garden kits',exact=True).click()
            expect(page.get_by_role('region',name='Official website demo')).to_have_count(0)
            page.get_by_role('button',name='Fill official form for review',exact=True).click()
            card=page.locator('.application-card')
            expect(card).to_contain_text('Demo ready for review',timeout=20000)
            expect(card.get_by_role('button',name='Submit application',exact=True)).to_have_count(0)
            expect(card.get_by_role('link',name='Open application browser')).to_be_visible()
        finally:b.close()

def request(path,body='',method='POST',resource='fetch'):
    return SimpleNamespace(url='https://tnhorticulture.tn.gov.in'+path,post_data=body,method=method,
        headers={'content-type':'application/x-www-form-urlencoded'},resource_type=resource,is_navigation_request=lambda:False)

def test_lookup_allowlist_rejects_identity_and_extra_payloads():
    assert lookup_allowed(request('/kit/fetch-block-master-list','postDistId=32'))
    assert lookup_allowed(request('/kit/fetch-village-master-list','postBlockId=12'))
    assert lookup_allowed(request('/kit/check-kit-block-target-limit','block_id=12'))
    for r in [request('/kit/fetch-block-master-list','postDistId=32&name=Kavya'),
              request('/kit/fetch-block-master-list','postDistId=32&postDistId=33'),
              request('/kit/fetch-block-master-list?phone=0000000000','postDistId=32'),
              request('/kit/check-aadhaar-limit-exceeds','aadhaar_no=000000000000'),
              request('/kit/fetch-block-master-list','postDistId=abc')]:
        assert not lookup_allowed(r)
    p=DemoNetwork(None,{},horticulture.SCHEME)
    assert not p.allows(request('/save?name=Kavya',method='GET'))
    assert not p.allows(request('/kit/','name=Kavya'))
