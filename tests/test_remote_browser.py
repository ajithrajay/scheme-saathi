import time
from fastapi.responses import HTMLResponse
from test_browser_e2e import live,start,wait_job


def test_remote_otp_handoff_and_control_boundaries(live):
    c,app=live
    @app.get('/demo/remote-otp',response_class=HTMLResponse)
    async def challenge():
        return '''<label>OTP<input id="otp" style="position:absolute;left:20px;top:30px;width:150px;height:40px"></label><button style="position:absolute;left:20px;top:100px" onclick="if(document.querySelector('#otp').value==='123456'){document.body.innerHTML='<h1>Verification complete</h1>'}">Verify</button>'''
    scheme=app.state.store.get('scheme','demo-education-grant')
    scheme['portal_url']='/demo/remote-otp'
    app.state.store.put('scheme',scheme['id'],scheme)
    id=start(c)
    wait_job(c,id,{'VERIFICATION_REQUIRED'})
    # Wait for the paused coroutine to finish before taking human control.
    for _ in range(40):
        state=c.get('/api/browser/'+id+'/state')
        if state.status_code==200:break
        time.sleep(.1)
    assert state.status_code==200,state.text
    frame=c.get('/api/browser/'+id+'/frame')
    assert frame.status_code==200 and frame.headers['content-type']=='image/jpeg'
    assert 'no-store' in frame.headers['cache-control']
    assert app.state.store.get('screenshot',id) is None
    def send(body):
        r=c.post('/api/browser/'+id+'/action',json=body)
        assert r.status_code==200,r.text
    send({'action':'click','x':65,'y':50})
    send({'action':'text','text':'123456'})
    send({'action':'click','x':40,'y':110})
    # Read the page through the existing event loop, never a real government site.
    import asyncio
    future=asyncio.run_coroutine_threadsafe(app.state.browser.contexts[id].pages[-1].inner_text('body'),app.state.browser.tasks[id].get_loop())
    assert 'Verification complete' in future.result(5)
    assert '123456' not in str(app.state.store.events(id))
    job=app.state.store.get('application',id);job['status']='READY_FOR_SUBMISSION';app.state.store.put('application',id,job)
    assert c.post('/api/browser/'+id+'/action',json={'action':'key','key':'Enter'}).status_code==409
    assert c.post('/api/browser/'+id+'/action',json={'action':'key','key':'F12'}).status_code==422
    assert c.post('/api/browser/'+id+'/action',json={'action':'click','x':2000}).status_code==422
    job['status']='RUNNING';app.state.store.put('application',id,job)
    assert c.get('/api/browser/'+id+'/frame').status_code==409
    c.post('/api/logout',json={})
    assert c.get('/api/browser/'+id+'/frame').status_code==401
    assert c.post('/api/browser/'+id+'/action',json={'action':'text','text':'fake'}).status_code==401


def test_login_and_browser_view_ui(live):
    from playwright.sync_api import sync_playwright, expect
    c,app=live
    scheme=app.state.store.get('scheme','demo-education-grant');scheme['portal_url']='/demo/challenge';app.state.store.put('scheme',scheme['id'],scheme)
    id=start(c);wait_job(c,id,{'VERIFICATION_REQUIRED'})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.goto(str(c.base_url).rstrip('/')+'/login')
            page.get_by_label('Owner key').fill('browser-test')
            page.get_by_role('button',name='Sign in',exact=True).click()
            page.wait_for_url(str(c.base_url).rstrip('/')+'/')
            page.get_by_role('button',name='My profile',exact=False).wait_for()
            page.goto(str(c.base_url).rstrip('/')+'/browser?job='+id)
            page.locator('#screen').wait_for(state='visible')
            assert page.locator('#hostname').inner_text()=='127.0.0.1'
            page.get_by_role('button',name='Tab',exact=True).click()
            page.locator('#text').fill('123456')
            page.get_by_role('button',name='Type into selected field').click()
            expect(page.locator('#text')).to_have_value('')
            assert 'Controls are available only' not in page.locator('#status').inner_text()
        finally:
            browser.close()
