"""Synthetic, fully intercepted regressions for the observed welfare login DOM."""
import json
import time
import pytest
from urllib.parse import urlparse
from test_browser_e2e import live
from app.store import now
from app.tn_portal_data import PORTALS
from test_tn_portals_e2e import portals_live, FACTS, begin, worker_form, wait_kit

CANONICAL='https://tnuwwb.tn.gov.in/applications/register'
LEGACY='https://www.tnuwwb.tn.gov.in/applications/register'


def observed_login(otp_visible=False):
    after=json.dumps(worker_form()).replace('</','<\\/')
    return f'''<meta charset=utf-8><form id=registerform>
    <input id=mobileno value="{FACTS['phone']}" readonly>
    <button id=send type=button>Send OTP</button>
    <div id=code style="display:{'block' if otp_visible else 'none'}"><input id=otp type=password placeholder="Enter OTP">
      <label class=otp_error style="display:none">Invalid OTP</label></div>
    <div class=verifydiv2 style="display:none"><button id=verify type=button>Verify OTP</button></div>
    <div class=formsubmit_btn style="display:none"><button type=submit>சமர்ப்பிக்க/Submit</button></div></form>
    <button id=unrelated type=submit>Submit</button>
    <script>
    window.wrongClicks=0;window.verifyClicks=0;
    document.getElementById('unrelated').onclick=()=>window.wrongClicks++;
    document.getElementById('verify').onclick=()=>window.verifyClicks++;
    document.getElementById('send').onclick=()=>{{
      fetch('https://tnuwwb.tn.gov.in/ajaxpages/ajaxsendotp/{FACTS['phone']}/11/0')
      .then(r=>{{if(!r.ok)throw Error('OTP unavailable');return r.text()}})
      .then(()=>{{document.getElementById('send').style.display='none';document.getElementById('code').style.display='block';document.querySelector('.verifydiv2').style.display='block'}}).catch(()=>{{}});
    }};
    document.getElementById('otp').oninput=()=>{{
      const valid=document.getElementById('otp').value==='000000';
      document.querySelector('.otp_error').style.display=valid?'none':'block';
      document.querySelector('.verifydiv2').style.display=valid?'none':'block';
      document.querySelector('.formsubmit_btn').style.display=valid?'block':'none';
    }};
    document.getElementById('registerform').onsubmit=e=>{{e.preventDefault();
      if(document.getElementById('otp').value!=='000000')return;
      document.body.innerHTML={after};document.querySelectorAll('script').forEach(s=>eval(s.textContent));
    }};
    </script>'''


def intercept_otp(app,monkeypatch,fixture,legacy=False,status=200,recorded=False):
    runner=app.state.browser;original=runner.open;opened=set();calls=[]
    fixture['otp_calls']=calls;fixture['otp_status']=status
    async def open_(job):
        context,page=await original(job)
        if job['id'] not in opened:
            opened.add(job['id'])
            if recorded:
                current=runner.store.get('application',job['id'])
                current['otp_request_attempted_at']=now();runner.store.put('application',job['id'],current)
            if legacy:await page.goto(LEGACY)
        return context,page
    monkeypatch.setattr(runner,'open',open_)
    @app.get('/demo/origin-state/{jid}')
    async def state(jid:str):
        page=runner.contexts[jid].pages[-1]
        return {'url':page.url,**await page.evaluate('()=>({wrong:window.wrongClicks,verify:window.verifyClicks,otp:!!document.querySelector("#otp")?.getClientRects().length})')}
    @app.post('/demo/bad-otp/{jid}')
    async def bad(jid:str):
        await runner.contexts[jid].pages[-1].locator('#otp').fill('111111')
        return {'ok':True}
    return calls


@pytest.mark.parametrize('legacy',[False,True])
def test_canonical_origin_and_live_style_verified_submit(portals_live,monkeypatch,legacy):
    c,app,fixture=portals_live;fixture['worker']=observed_login()
    calls=intercept_otp(app,monkeypatch,fixture,legacy=legacy)
    assert PORTALS['worker-registration']['portal_url']==CANONICAL
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert 'Enter OTP' in job['message']
    assert c.get('/demo/origin-state/'+jid).json()['url']==CANONICAL
    assert calls==['tnuwwb.tn.gov.in']
    c.post('/demo/human/'+jid)
    assert wait_kit(c,jid,{'SUBMITTED'})['receipt']=='FIXTURE-REG-000001'
    assert c.get('/demo/origin-state/'+jid).json()['wrong']==0
    assert c.get('/demo/origin-state/'+jid).json()['verify']==0


def test_recorded_attempt_survives_origin_repair_without_resending(portals_live,monkeypatch):
    c,app,fixture=portals_live;fixture['worker']=observed_login()
    calls=intercept_otp(app,monkeypatch,fixture,legacy=True,recorded=True)
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert 'attempt is already recorded' in job['message'] and job['otp_request_attempted_at']
    assert c.get('/demo/origin-state/'+jid).json()['url']==CANONICAL
    assert calls==[]


def test_active_otp_not_reloaded_and_invalid_code_not_submitted(portals_live,monkeypatch):
    c,app,fixture=portals_live;fixture['worker']=observed_login(otp_visible=True)
    calls=intercept_otp(app,monkeypatch,fixture,legacy=True)
    jid=begin(c,'worker-registration')
    wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert c.get('/demo/origin-state/'+jid).json()['url']==LEGACY
    c.post('/demo/bad-otp/'+jid)
    time.sleep(7)
    state=c.get('/demo/origin-state/'+jid).json()
    assert state['otp'] and state['wrong']==0 and state['verify']==0
    assert calls==[]
    c.post('/demo/human/'+jid)
    assert wait_kit(c,jid,{'SUBMITTED'})['receipt']=='FIXTURE-REG-000001'


def test_otp_http_failure_reports_status_without_private_request_data(portals_live,monkeypatch):
    c,app,fixture=portals_live;fixture['worker']=observed_login()
    calls=intercept_otp(app,monkeypatch,fixture,status=503)
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert 'HTTP 503' in job['message']
    events=c.get('/api/events/'+jid).json()
    diagnostic=next(e['otp_network'] for e in events if 'otp_network' in e)
    assert diagnostic=={'request_seen':True,'cross_origin':False,'http_status':503}
    serialized=json.dumps(events)
    assert FACTS['phone'] not in serialized and 'ajaxsendotp' not in serialized
    assert calls==['tnuwwb.tn.gov.in'] and not job['submission_attempted']


@pytest.mark.parametrize('manual',[False,True])
def test_known_ration_card_challenge_uses_saved_value_once(portals_live,monkeypatch,manual):
    c,app,fixture=portals_live
    html=observed_login()
    html=html.replace('<button id=send type=button>', '<input id=rationcardno style="display:none"><button id=send type=button>')
    html=html.replace("document.getElementById('send').onclick=()=>{", """window.entryClicks=0;
    document.getElementById('send').onclick=()=>{
      window.entryClicks++;
      if(!document.getElementById('rationcardno').value){
        alert('The mobile number provided is already used by another applicant. Please enter a valid Ration Card number.');
        document.getElementById('rationcardno').style.display='block';return;
      }""")
    if manual:html=html.replace('window.entryClicks++;','window.entryClicks++;if(window.entryClicks===1)return;')
    fixture['worker']=html
    calls=intercept_otp(app,monkeypatch,fixture)
    @app.get('/demo/ration-check/{jid}')
    async def check(jid:str):
        page=app.state.browser.contexts[jid].pages[-1]
        return {'matched':await page.locator('#rationcardno').input_value()==FACTS['ration_card_number'],
                'clicks':await page.evaluate('()=>window.entryClicks')}
    @app.post('/demo/ration-manual/{jid}')
    async def send(jid:str):
        await app.state.browser.contexts[jid].pages[-1].locator('#send').click()
        return {'ok':True}
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    if manual:
        c.post('/demo/ration-manual/'+jid)
        deadline=time.time()+15
        while time.time()<deadline:
            job=next(j for j in c.get('/api/applications').json() if j['id']==jid)
            if 'Enter OTP' in job['message']:break
            time.sleep(.1)
    assert 'Enter OTP' in job['message'] and job['otp_ration_retry_used']
    assert c.get('/demo/ration-check/'+jid).json()=={'matched':True,'clicks':3 if manual else 2}
    assert calls==['tnuwwb.tn.gov.in']
    c.post('/demo/human/'+jid)
    assert wait_kit(c,jid,{'SUBMITTED'})['receipt']=='FIXTURE-REG-000001'
