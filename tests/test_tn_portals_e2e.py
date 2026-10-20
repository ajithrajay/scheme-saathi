"""Offline integration scenarios, NOT certified copies of post-login govt forms.
Every government URL is intercepted. No test data reaches official servers.
"""
import json
import re
import time
import pytest
from playwright.async_api import Page
from fastapi.responses import JSONResponse
from playwright.sync_api import sync_playwright, expect
from app.tn_portal_data import PORTALS
from app import tn_portals
from test_browser_e2e import live
from test_horticulture_e2e import FACTS as KIT_FACTS, PNG, wait_kit

FACTS={**KIT_FACTS,'bank_account':'123456789012','ifsc':'SBIN0001234',
 'agri_department':'Agriculture','agri_scheme':'Fixture subsidy','agri_input_type':'Seeds','agri_component':'Seed component',
 'agri_social_status':'General','land_ownership':'Cultivator','land_district':'Tiruvannamalai',
 'land_taluk':'Chengam','land_village':'Fixture Land Village','land_survey_number':'12/3',
 'land_address_same':False,'land_area_acres':1.5,'cultivable_crop':'Paddy','village':'Fixture Residence',
 'ration_card_number':'123456789012','date_of_birth':'1990-01-01','worker_board':'Manual Workers',
 'occupation':'Fixture occupation','nominee_name':'Fixture Nominee','nominee_relationship':'Parent',
 'marital_status':'Unmarried','worker_registration_number':'FIXTUREMEMBER',
 'worker_claim_name':'Fixture Education Claim'}


def input_(label,kind='text',name=''):
    return f'<label>{label}<input type="{kind}" name="{name}" required></label>'


def select(label,value,ident='',delayed=False):
    return f'<label>{label}<select id="{ident}" required><option value="">Select</option>'+('' if delayed else f'<option value="one">{value}</option>')+'</select></label>'


def end_form():
    return "<button type=submit>Submit</button></form><script>document.querySelector('form').onsubmit=e=>{e.preventDefault(); document.body.innerHTML='<h1>Application submitted successfully</h1><p>Application Number: FIXTURE-TN-000001</p>'}</script>"


def agri_html(extra=''):
    return '''<h1>Apply schemes</h1><form>'''+''.join(select(l,FACTS[k]) for l,k in [
      ('Department / துறை','agri_department'),('Scheme / திட்டம்','agri_scheme'),('Input Type / வகை','agri_input_type'),('Component / இனங்கள்','agri_component')])+''.join(input_(l) for l in ['Aadhaar Number / ஆதார் எண்','Name of the Farmer / விவசாயி பெயர்','Name of Father / Spouse','Mobile Number'])+select('Social status','General')+'''
      <fieldset><legend>Ownership Details</legend><label>Owner / உரிமையாளர்<input type=radio name=owner value=1 required></label><label>Cultivator / சாகுபடியாளர்<input type=radio name=owner value=2></label></fieldset>
      <fieldset><legend>Sex</legend><label>Male / ஆண்<input type=radio name=gender value=1 required></label><label>Female / பெண்<input type=radio name=gender value=2></label></fieldset>'''+input_('Farmer Photo','file')+'''
      <h3>Residential Address / இருப்பிட முகவரி</h3>'''+select('District','Chennai','res-district')+select('Block','Anna nagar','res-block',True)+select('Village','Fixture Residence')+'''
      <h3>Land Details / நில விவரங்கள்</h3>'''+select('District','Tiruvannamalai')+select('Taluk','Chengam')+select('Village','Fixture Land Village')+input_('Survey number /Sub Division Number')+input_('Land Area of Survey Number(Acres)','number')+select('Cutivable Crop','Paddy')+input_('Account Number')+input_('IFSC Code')+extra+end_form()+'''
      <script>document.querySelector('input[type=number]').step='any';document.getElementById('res-district').onchange=()=>setTimeout(()=>document.getElementById('res-block').innerHTML='<option value="">Select</option><option value="one">Anna nagar</option>',400)</script>'''


def worker_form():
    # Representative multi-step fixture only; official forms remain OTP-protected.
    second='<form>'+''.join(input_(label,'file') for label in ['Employment Certificate','Identity Proof','Ration Card','Bank Passbook','Aadhaar Card','Nominee Proof'])+'<button type=submit>Submit</button></form>'
    first='<h1>Member details</h1><form>'+input_('Full name')+select('Board','Manual Workers')+select('Nature of work','Fixture occupation')+input_('Nominee Name')+input_('Nominee Relationship')+'<button type=button id=next>Next</button></form>'
    return first+f'<script>document.getElementById("next").onclick=()=>{{document.body.innerHTML={json.dumps(second)};document.querySelector("form").onsubmit=e=>{{e.preventDefault();document.body.innerHTML="<h1>Registration submitted successfully</h1><p>Registration Number: FIXTURE-REG-000001</p>"}}}}</script>'


def registration_html():
    after=worker_form()
    # Inserted scripts must execute after test-only verification replacement.
    return '''<form id=registerform><input id=mobileno placeholder="கைபேசி எண் / Mobile number"><button type=button id=login>உள்நுழைக/Login</button></form>
    <script>document.getElementById('login').onclick=()=>{document.getElementById('registerform').innerHTML='<input id=rationcardno placeholder="Ration Card or Smart Card or குடும்ப அட்டை எண்"><button type=button id=send>OTP-யைப் பெற/Send OTP</button>';document.getElementById('send').onclick=()=>{document.getElementById('registerform').innerHTML='<input id=otp placeholder="Enter OTP"><button type=button id=verify>Verify OTP</button>';document.getElementById('verify').onclick=()=>{document.body.innerHTML='''+json.dumps(after).replace('</','<\\/')+''';document.querySelectorAll('script').forEach(s=>eval(s.textContent));}}}</script>'''


def claim_html():
    dashboard='<a href="/claims/fixture">Fixture Education Claim</a>'
    return '''<form id=userLogin><input id=username placeholder=Username><input id=password type=password placeholder=Password><input id=captcha placeholder="Enter CAPTCHA"><button type=button id=login>LOGIN</button></form><script>document.getElementById('login').onclick=()=>{document.body.innerHTML='''+json.dumps(dashboard)+'''}</script>'''


@pytest.fixture
def portals_live(live,monkeypatch):
    client,app=live
    fixture={'agri':agri_html(),'worker':registration_html(),'claim':claim_html(),'requests':[]}
    original=Page.goto
    async def goto(page,url,*args,**kwargs):
        if any(url.startswith(s['portal_url']) for s in PORTALS.values()):
            async def serve(route):
                url=route.request.url
                fixture['requests'].append((route.request.method,url))
                if '/ajaxpages/ajaxsendotp/' in url:
                    from urllib.parse import urlparse
                    fixture.setdefault('otp_calls',[]).append(urlparse(url).hostname)
                    await route.fulfill(status=fixture.get('otp_status',200),content_type='text/plain',body='fixture-response')
                    return
                body=fixture['agri'] if 'tnagrisnet' in url else (input_('Full Name')+end_form() if '/claims/fixture' in url else fixture['claim'] if '/users/login' in url else fixture['worker'])
                if '/claims/fixture' in url:body='<h1>Fixture Education Claim</h1><form>'+input_('Full Name')+end_form()
                await route.fulfill(status=200,content_type='text/html',body=body)
            await page.route('https://**/*',serve)
        return await original(page,url,*args,**kwargs)
    monkeypatch.setattr(Page,'goto',goto)
    assert client.put('/api/profile',json={'facts':FACTS}).status_code==200
    for doc in ['beneficiary_photo','occupation_certificate','identity_proof','ration_card','bank_passbook','aadhaar_document','nominee_proof']:
        assert client.post('/api/documents',data={'document_type':doc},files={'file':('fixture.png',PNG,'image/png')}).status_code==200
    @app.post('/demo/human/{jid}')
    async def human(jid:str):
        page=app.state.browser.contexts[jid].pages[-1]
        if await page.locator('#verify').count():
            await page.locator('#otp').fill('000000')
        else:
            assert await page.locator('#username').input_value()=='FIXTUREMEMBER'
            assert await page.locator('#password').input_value()=='fixture-password'
            await page.locator('#captcha').fill('fixture')
        return JSONResponse({'ok':True})
    return client,app,fixture


def begin(c,key='agrisnet',auto=True):
    r=c.post('/api/tn-portals/'+key+'/applications',json={'disclosure_confirmed':True,'auto_submit':auto})
    assert r.status_code==200,r.text
    return r.json()['id']


def test_agrisnet_cascades_land_sections_and_no_duplicates(portals_live):
    c,app,fixture=portals_live
    jid=begin(c)
    job=wait_kit(c,jid,{'SUBMITTED'})
    assert job['receipt']=='FIXTURE-TN-000001'
    assert begin(c)==jid
    fields={f for e in c.get('/api/events/'+jid).json() for f in e.get('fields',[])}
    assert {'district','land_district','village','land_village','land_ownership','doc:beneficiary_photo'}<=fields
    assert all(method=='GET' for method,url in fixture['requests'])


def test_worker_otp_auto_resume_multistep_registration(portals_live):
    c,app,fixture=portals_live
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert job['auto_resume_waiting'] and not job['submission_attempted']
    assert c.post('/demo/human/'+jid).status_code==200
    job=wait_kit(c,jid,{'SUBMITTED'})
    assert job['receipt']=='FIXTURE-REG-000001' and 'officer approval' in job['message']
    assert sum(e.get('status')=='SUBMITTING' for e in c.get('/api/events/'+jid).json())==1


def test_claim_saved_password_captcha_and_exact_link(portals_live):
    c,app,fixture=portals_live
    c.put('/api/tn-portals/worker/credentials',json={'password':'fixture-password'})
    assert 'fixture-password' not in c.get('/api/tn-portals').text
    jid=begin(c,'worker-claim')
    wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert c.post('/demo/human/'+jid).status_code==200
    assert wait_kit(c,jid,{'SUBMITTED'})['claim_opened']
    assert 'fixture-password' not in c.get('/api/events/'+jid).text
    assert 'fixture-password' not in (app.state.store.directory/'app.sqlite3').read_bytes().decode(errors='ignore')


@pytest.mark.parametrize('extra',[input_('Unknown required question'),'<label>I agree to terms<input type=checkbox required></label>'])
def test_unmapped_fields_and_declarations_never_submit(portals_live,extra):
    c,app,fixture=portals_live;fixture['agri']=agri_html(extra)
    jid=begin(c)
    job=wait_kit(c,jid,{'NEEDS_INFORMATION'})
    assert job['unresolved'] and not job['submission_attempted']


def test_unverified_submit_never_retries(portals_live):
    c,app,fixture=portals_live
    fixture['agri']=agri_html().replace('Application Number: FIXTURE-TN-000001','Processing request')
    jid=begin(c)
    wait_kit(c,jid,{'SUBMISSION_UNVERIFIED'})
    c.post('/api/applications/'+jid+'/resume',json={})
    time.sleep(1)
    assert sum(e.get('status')=='SUBMITTING' for e in c.get('/api/events/'+jid).json())==1
    assert c.post('/api/applications/'+jid+'/cancel',json={}).status_code==409


def test_closing_verification_browser_cancels_watcher(portals_live):
    c,app,fixture=portals_live
    jid=begin(c,'worker-registration')
    wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    time.sleep(.3)
    assert c.post('/api/applications/'+jid+'/close-browser',json={}).status_code==200
    assert jid not in app.state.browser.verification_watchers
    assert not next(j for j in c.get('/api/applications').json() if j['id']==jid)['auto_resume_waiting']


def test_ui_and_api_completeness(portals_live):
    c,app,fixture=portals_live
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True);p=b.new_page();p.goto(str(c.base_url)+'?access=browser-test')
        p.get_by_role('button',name='TN applications',exact=True).click()
        expect(p.get_by_role('heading',name='TN applications',exact=True)).to_be_visible()
        expect(p.get_by_role('button',name='Start automatic application')).to_be_disabled()
        p.get_by_role('checkbox').first.check()
        p.get_by_role('button',name='Start automatic application').click()
        expect(p.locator('.application-card')).to_contain_text('FIXTURE-TN-000001',timeout=30000)
        b.close()
    assert c.post('/api/tn-portals/agrisnet/applications',json={}).status_code==422


def screenshot_style_agri():
    """Observed layout features, not a copy of the live portal source."""
    import re
    html=agri_html().replace('<h3>','<div class="section-bar">').replace('</h3>','</div>')
    html=re.sub(r'<label>([^<]+)(<select.*?</select>)</label>',lambda m:'<div class="col"><div>'+m[1]+'</div>'+m[2].replace('<select','<select aria-label="Default select example"')+'</div>',html)
    html=re.sub(r'<label>([^<]+)(<input type=radio[^>]+>)</label>',lambda m:m[2]+m[1]+'<br>',html)
    html=html.replace('<div class="section-bar">Land Details', '<div><input type=checkbox name=same_address>Residence and land location are the same</div><div class="section-bar">Land Details')
    html=html.replace('Name of the Farmer / விவசாயி பெயர்<input','Name of the Farmer / விவசாயி பெயர்<input id=farmer')
    html+='''<script>document.querySelector('input').addEventListener('input',()=>setTimeout(()=>{document.getElementById('farmer').value=''},350),{once:true});</script>'''
    return html


def test_real_layout_features_ignore_legacy_mapping_and_recheck_values(portals_live):
    c,app,fixture=portals_live;fixture['agri']=screenshot_style_agri()
    app.state.store.put('mapping',PORTALS['agrisnet']['id'],{'default select example':'district','block':'district','village':'district'})
    @app.get('/demo/inspect-fields/{jid}')
    async def inspect_fields(jid:str):
        page=app.state.browser.contexts[jid].pages[-1]
        return await page.evaluate('''()=>({name:document.getElementById('farmer').value,
          district:document.getElementById('res-district').selectedOptions[0].text,
          block:document.getElementById('res-block').selectedOptions[0].text,
          same:document.querySelector('[name=same_address]').checked,
          fields:Array.from(document.querySelectorAll('select')).map(e=>e.selectedOptions[0].text),
          owner:document.querySelector('[name=owner]:checked').value})''')
    jid=begin(c,auto=False)
    wait_kit(c,jid,{'READY_FOR_SUBMISSION'})
    values=c.get('/demo/inspect-fields/'+jid).json()
    assert values['name']==FACTS['full_name']
    assert values['district']=='Chennai' and values['block']=='Anna nagar'
    assert 'Fixture Residence' in values['fields'] and 'Fixture Land Village' in values['fields']
    assert 'Tiruvannamalai' in values['fields'] and 'Chengam' in values['fields']
    assert values['owner']=='2' and values['same'] is False


def test_portal_option_picker_updates_job_not_profile_and_rejects_forgery(portals_live):
    c,app,fixture=portals_live
    fixture['agri']=screenshot_style_agri().replace('Fixture subsidy','Available fixture subsidy')
    jid=begin(c,auto=False)
    job=wait_kit(c,jid,{'NEEDS_INFORMATION'})
    field=next(f for f in job['unresolved'] if f.get('field')=='agri_scheme')
    assert [o['label'] for o in field['options']]==['Available fixture subsidy']
    assert c.put('/api/tn-portals/applications/'+jid+'/choice',json={'mapping_key':field['mapping_key'],'value':'Invented'}).status_code==422
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);p=browser.new_page();p.goto(str(c.base_url)+'?access=browser-test')
        p.locator('nav').get_by_role('button',name=re.compile(r'^Applications')).click()
        p.get_by_label('Portal option for '+field['label'],exact=True).select_option(label='Available fixture subsidy')
        expect(p.get_by_text('Choice saved for this application.',exact=False)).to_be_visible()
        expect(p.get_by_label('Portal option for '+field['label'],exact=True)).to_have_value('one')
        p.get_by_role('button',name='Resume',exact=True).click()
        expect(p.locator('.application-card')).to_contain_text('Ready to submit',timeout=30000)
        browser.close()
    assert c.get('/api/profile').json()['facts']['agri_scheme']=='Fixture subsidy'
    job=next(j for j in c.get('/api/applications').json() if j['id']==jid)
    assert job['portal_choices']['agri_scheme']=='Available fixture subsidy'
    assert not job['submission_attempted']
    assert c.put('/api/tn-portals/applications/'+jid+'/choice',json={'mapping_key':field['mapping_key'],'value':'one'}).status_code==409


def test_mapping_keys_do_not_collide_for_unlabelled_fields():
    field={'label':'','name':'','id':'','section':'Land Details','index':1}
    assert tn_portals.mapping_key(field)!=tn_portals.mapping_key({**field,'index':2})


def duplicate_component_html():
    html=screenshot_style_agri()
    return html.replace('<option value="one">Seed component</option>',
        '<option value="component-a">Seed component</option><option value="component-b">Seed component</option><option value="component-c">Seed component</option>')


def test_duplicate_component_label_uses_selected_id_and_survives_resume(portals_live):
    c,app,fixture=portals_live;fixture['agri']=duplicate_component_html()
    @app.get('/demo/component/{jid}')
    async def component(jid:str):
        page=app.state.browser.contexts[jid].pages[-1]
        return {'value':await page.locator('select').nth(3).input_value()}
    jid=begin(c,auto=False)
    job=wait_kit(c,jid,{'NEEDS_INFORMATION'})
    field=next(f for f in job['unresolved'] if f.get('field')=='agri_component')
    assert len(field['options'])==3 and 'same label' in field['reason']
    # Text-only requests from an old tab must not silently select the first ID.
    assert c.put('/api/tn-portals/applications/'+jid+'/choice',json={'mapping_key':field['mapping_key'],'value':'Seed component'}).status_code==422
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);p=browser.new_page();p.goto(str(c.base_url)+'?access=browser-test')
        p.locator('nav').get_by_role('button',name=re.compile(r'^Applications')).click()
        control=p.get_by_label('Portal option for '+field['label'],exact=True)
        expect(control.locator('option',has_text='[ID component-b]')).to_have_count(1)
        control.select_option('component-b')
        expect(p.get_by_text('Selected: Seed component — portal option ID: component-b',exact=True)).to_be_visible()
        expect(control).to_have_value('component-b')
        p.reload();p.locator('nav').get_by_role('button',name=re.compile(r'^Applications')).click()
        expect(p.get_by_label('Portal option for '+field['label'],exact=True)).to_have_value('component-b')
        p.get_by_role('button',name='Resume',exact=True).click()
        expect(p.locator('.application-card')).to_contain_text('Ready to submit',timeout=30000)
        browser.close()
    assert c.get('/demo/component/'+jid).json()['value']=='component-b'
    # Final submission rechecks must retain that ID, not fail the duplicate label.
    c.post('/api/applications/'+jid+'/resume',json={'auto_submit':True})
    assert wait_kit(c,jid,{'SUBMITTED'})['receipt']=='FIXTURE-TN-000001'


def test_saved_option_id_is_rejected_when_parent_context_changes(portals_live):
    c,app,fixture=portals_live;fixture['agri']=duplicate_component_html()
    @app.post('/demo/change-parent/{jid}')
    async def change_parent(jid:str):
        page=app.state.browser.contexts[jid].pages[-1]
        # Same label, changed parent ID. A child ID must not be replayed blindly.
        await page.locator('select').nth(1).locator('option').nth(1).evaluate("e=>e.value='different-scheme'")
        return {'ok':True}
    jid=begin(c,auto=False)
    job=wait_kit(c,jid,{'NEEDS_INFORMATION'})
    field=next(f for f in job['unresolved'] if f.get('field')=='agri_component')
    assert c.put('/api/tn-portals/applications/'+jid+'/choice',json={'mapping_key':field['mapping_key'],'value':'component-b'}).status_code==200
    assert c.post('/demo/change-parent/'+jid).status_code==200
    c.post('/api/applications/'+jid+'/resume',json={})
    # Wait for a new browser pass rather than the old pause response.
    end=time.time()+30
    while time.time()<end:
        job=next(j for j in c.get('/api/applications').json() if j['id']==jid)
        if any('parent selection or form changed' in f['reason'] for f in job.get('unresolved',[])):break
        time.sleep(.15)
    assert any('parent selection or form changed' in f['reason'] for f in job.get('unresolved',[])),job
    assert job['status']=='NEEDS_INFORMATION' and not job['submission_attempted']


MOBILE_CONFIRMATION="Great! You're a member of this family. Do you want to use this mobile number to complete the registration."


def locked_registration_html(lock='readonly',dialog='confirm',mode='normal',phone=None):
    """Reproduce observed locked mobile, plain anchors and the browser popup.

    Synthetic data only. This is a regression fixture, not the portal's source.
    """
    after=worker_form()
    after_json=json.dumps(after).replace('</','<\\/')
    phone=FACTS['phone'] if phone is None else phone
    return f'''<meta charset=utf-8><form id=registerform><input id=mobileno value="{phone}" {lock} placeholder="Mobile number">
      <a id=send href="#">OTP-யைப் பெற/Send OTP ➲</a></form>
      <script>window.sends=0;window.accepted=false;
      document.getElementById('send').onclick=e=>{{e.preventDefault();window.sends++;
        if ({json.dumps(mode)}==='silent' && window.sends===1)return;
        setTimeout(()=>{{
          const message={json.dumps('Unknown declaration: accept all terms' if mode=='unknown' else MOBILE_CONFIRMATION)};
          const accepted={dialog}(message);
          if ({json.dumps(dialog)}==='confirm' && !accepted)return;
          window.accepted=true;
          document.getElementById('send').remove();
          document.getElementById('registerform').insertAdjacentHTML('beforeend','<input id=otp placeholder="Enter OTP"><p>An OTP has been sent to your mobile number</p><a href="#" id=verify>OTP-யைச் சரிபார்க்க/Verify OTP ➲</a>');
          document.getElementById('verify').onclick=e=>{{e.preventDefault();document.body.innerHTML={after_json};document.querySelectorAll('script').forEach(s=>eval(s.textContent));}};
        }},150);
      }};</script>'''


def registration_probe(app):
    @app.get('/demo/registration-state/{jid}')
    async def state(jid:str):
        page=app.state.browser.contexts[jid].pages[-1]
        return await page.evaluate('()=>({sends:window.sends,accepted:window.accepted,otp:!!document.getElementById("otp")})')
    @app.post('/demo/manual-send/{jid}')
    async def manual_send(jid:str):
        page=app.state.browser.contexts[jid].pages[-1]
        await page.locator('#send').click()
        return {'ok':True}


@pytest.mark.parametrize('lock,dialog',[('readonly','confirm'),('disabled','alert')])
def test_locked_mobile_confirmation_and_bilingual_anchor_otp(portals_live,lock,dialog):
    c,app,fixture=portals_live
    fixture['worker']=locked_registration_html(lock,dialog)
    registration_probe(app)
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert 'Enter OTP' in job['message']
    assert c.get('/demo/registration-state/'+jid).json()=={'sends':1,'accepted':True,'otp':True}
    assert c.get('/api/applications/'+jid+'/screenshot').status_code==404
    if dialog=='confirm':
        with sync_playwright() as pw:
            b=pw.chromium.launch(headless=True);p=b.new_page();p.goto(str(c.base_url)+'?access=browser-test')
            p.locator('nav').get_by_role('button',name=re.compile(r'^Applications')).click()
            p.get_by_role('button',name='View browser screenshot',exact=True).click()
            expect(p.get_by_role('dialog').get_by_role('status')).to_contain_text('No screenshot available')
            expect(p.get_by_role('dialog').locator('img')).to_have_count(0)
            b.close()
    assert c.post('/demo/human/'+jid).status_code==200
    assert wait_kit(c,jid,{'SUBMITTED'})['receipt']=='FIXTURE-REG-000001'
    events=c.get('/api/events/'+jid).text
    assert 'Acknowledged the portal mobile-registration confirmation' in events
    assert FACTS['phone'] not in events and MOBILE_CONFIRMATION not in events


def test_unanswered_send_is_not_repeated_and_manual_retry_confirmation_works(portals_live):
    c,app,fixture=portals_live
    fixture['worker']=locked_registration_html(mode='silent')
    registration_probe(app)
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert 'no OTP-service request was observed' in job['message']
    assert c.get('/demo/registration-state/'+jid).json()['sends']==1
    assert c.post('/api/applications/'+jid+'/resume',json={}).status_code==200
    deadline=time.time()+15
    while time.time()<deadline:
        job=next(j for j in c.get('/api/applications').json() if j['id']==jid)
        if 'attempt is already recorded' in job['message']:break
        time.sleep(.1)
    assert 'attempt is already recorded' in job['message']
    assert c.get('/demo/registration-state/'+jid).json()['sends']==1
    assert c.post('/demo/manual-send/'+jid).status_code==200
    deadline=time.time()+5
    while time.time()<deadline:
        state=c.get('/demo/registration-state/'+jid).json()
        if state['otp']:break
        time.sleep(.1)
    assert state=={'sends':2,'accepted':True,'otp':True}
    c.post('/demo/human/'+jid)
    assert wait_kit(c,jid,{'SUBMITTED'})['receipt']=='FIXTURE-REG-000001'


def test_unknown_confirmation_is_not_accepted(portals_live):
    c,app,fixture=portals_live
    fixture['worker']=locked_registration_html(mode='unknown')
    registration_probe(app)
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'VERIFICATION_REQUIRED'})
    assert 'unrecognized registration dialog' in job['message']
    assert c.get('/demo/registration-state/'+jid).json()=={'sends':1,'accepted':False,'otp':False}
    assert not job['submission_attempted']


def test_locked_different_mobile_pauses_before_send(portals_live):
    c,app,fixture=portals_live
    fixture['worker']=locked_registration_html(phone='9888888888')
    registration_probe(app)
    jid=begin(c,'worker-registration')
    job=wait_kit(c,jid,{'LOGIN_REQUIRED'})
    assert 'does not match the saved profile' in job['message']
    assert c.get('/demo/registration-state/'+jid).json()=={'sends':0,'accepted':False,'otp':False}
