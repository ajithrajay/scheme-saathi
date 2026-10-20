"""Fictional applicants use normal application jobs, with no government writes."""
import re
import uuid
from urllib.parse import urlparse, parse_qs
from fastapi import HTTPException
from .store import now
from .resume_demo import seed_demo, demo_inputs
from . import horticulture, tn_portals

HORT_IDS = dict(aadhaar_no='aadhaar_number',benificiary_name='full_name',relation_name='relation_name',
    phoneNumber='phone',address='address',pincode='pincode',gender='gender',social_status='kit_category',
    checkLocality='area_type',dist_id='district',block_id='block',village_id='village',beneficiary_image='doc:beneficiary_photo')

# Public lookup calls observed in the official horticulture JavaScript. No
# applicant identifiers, free text, uploads, credentials or extra keys allowed.
LOOKUPS = {
 '/kit/fetch-block-master-list': 'postDistId',
 '/kit/fetch-village-master-list': 'postBlockId',
 '/kit/check-kit-block-target-limit': 'block_id',
}
CDNS = {'code.jquery.com','cdn.jsdelivr.net','cdnjs.cloudflare.com'}

def lookup_allowed(request):
    url = urlparse(request.url)
    key = LOOKUPS.get(url.path)
    if url.scheme!='https' or url.hostname!='tnhorticulture.tn.gov.in' or url.query or not key or request.method!='POST':return False
    if not request.headers.get('content-type','').startswith('application/x-www-form-urlencoded'):return False
    values=parse_qs(request.post_data or '',keep_blank_values=True)
    return set(values)=={key} and len(values[key])==1 and bool(re.fullmatch(r'[1-9][0-9]{0,6}',values[key][0]))

class DemoNetwork:
    def __init__(self,runner,job,scheme):
        self.runner,self.job,self.scheme=runner,job,scheme
        self.loading=True
        self.assets=set()
        self.blocks=0

    def allows(self,request):
        url=urlparse(request.url)
        if lookup_allowed(request):return self.scheme['id']==horticulture.SCHEME['id']
        if request.method not in {'GET','HEAD'} or url.scheme!='https':return False
        if request.is_navigation_request():
            return self.loading and request.url==self.scheme['portal_url']
        if self.loading and request.resource_type in {'script','stylesheet','image','font'} and url.hostname in set(self.scheme['allowed_hosts'])|CDNS:
            self.assets.add(request.url)
            return True
        return request.url in self.assets

    def blocked(self):
        self.blocks+=1
        current=self.runner.store.get('application',self.job['id'])
        if current and not current.get('demo_request_blocked'):
            current['demo_request_blocked']=True
            self.runner.store.put('application',self.job['id'],current)
            self.runner.store.event(self.job['id'],'browser','Demo blocked a request outside the verified public lookup list. No applicant data was sent.')

def start(runner,spec,auto_submit=False,kit=None):
    if auto_submit:raise HTTPException(422,'Submission is disabled for fictional demo applicants.')
    seed_demo(runner.store)
    existing=next((j for j in runner.store.all('application') if j.get('official_demo') and j['scheme_id']==spec['id'] and j.get('kit')==kit and j['status']!='CANCELLED'),None)
    if existing:return existing
    id=uuid.uuid4().hex
    job=dict(id=id,scheme_id=spec['id'],scheme_name=spec['name'],adapter=spec.get('adapter',horticulture.ADAPTER),
             demo=True,official_demo=True,kit=kit,status='QUEUED',auto_submit=False,submission_attempted=False,
             created_at=now(),updated_at=now(),message='Opening the browser with your preloaded demo profile. Submission is disabled.',
             unresolved=[],submission_blockers=[],auto_resume_waiting=False)
    runner.store.put('application',id,job)
    runner.start(id)
    return job

async def fill(runner,job,page,facts,docs):
    """Fill visible controls; choose available demo dropdown examples explicitly."""
    from .browser import map_field
    unresolved,filled,choices=[],[],{}
    fields=await page.evaluate(tn_portals.INSPECT)
    for f in fields:
        label=tn_portals.mapping_label(f)
        loc=page.locator(f'[data-tn-field="{f["index"]}"]')
        identity=' '.join(str(f.get(k,'')) for k in ('label','name','id'))
        if tn_portals.SENSITIVE_CONTROL.search(identity) or f['type']=='password':
            unresolved.append({'label':label,'reason':'Demo stops before authentication or a declaration.'})
            continue
        key=HORT_IDS.get(f['id']) if job['adapter']==horticulture.ADAPTER else None
        key=key or tn_portals.resolve(f,{},job['adapter']=='tn-agrisnet-v1') or map_field(f,{})
        if f['type']=='radio' and not key:
            peers={part for x in fields if x['type']=='radio' and x['name']==f['name'] for part in tn_portals.parts(x['label'])}
            if {'male','female'}<=peers:key='gender'
            elif {'owner','cultivator'}<=peers:key='land_ownership'
        if not key:
            # Kit quantities are handled once below; do not select other kits.
            if job.get('kit') and (f['id'].endswith('KitReqQty') or await loc.get_attribute('data-select-id')):continue
            if not f['hasValue'] and f['required']:unresolved.append({'label':label,'reason':'No unambiguous demo field mapping.'})
            continue
        try:
            if key.startswith('doc:'):
                d=docs.get(key[4:])
                if not d or f['type']!='file':raise ValueError('Sample document is missing.')
                if not f['hasValue']:
                    await loc.set_input_files({'name':d['filename'],'mimeType':d['mime_type'],
                        'buffer':runner.store.cipher.decrypt(runner.store.get_blob(d['blob']))},timeout=1500)
            elif f['tag']=='select':
                options=[]
                for _ in range(12):
                    options=await tn_portals.available_options(loc)
                    if options:break
                    await page.wait_for_timeout(150)
                matches=[o for o in options if tn_portals.norm(facts.get(key,'')) in tn_portals.parts(o['label'])]
                selected=matches[0] if len(matches)==1 else next(iter(options),None)
                if not selected:raise ValueError('No available options; a live lookup or verified account may be required.')
                if await loc.input_value()!=selected['value']:
                    await loc.select_option(selected['value'],timeout=1500)
                    await page.wait_for_timeout(200)
                choices[key]=selected['label']
            elif f['type']=='radio':
                if tn_portals.norm(facts.get(key,'')) not in tn_portals.parts(f['label']):continue
                if not await loc.is_checked():await loc.check(timeout=1500)
            elif f['type']=='checkbox':
                if key!='land_address_same':raise ValueError('Confirmation is not accepted by the demo.')
                await loc.set_checked(bool(facts.get(key)),timeout=1500)
            elif key in facts:
                if await loc.input_value()!=str(facts[key]):await loc.fill(str(facts[key]),timeout=1500)
            else:raise ValueError('No saved demo value.')
            filled.append(key)
        except Exception as exc:
            unresolved.append({'label':label,'reason':str(exc) if isinstance(exc,ValueError) else 'The portal rejected the demo value or changed the field.'})
    if job.get('kit'):
        qty=horticulture.KITS[job['kit']][1]
        selector=page.locator(f'input[type=checkbox][data-select-id="{qty}"]')
        if await selector.count():
            try:
                if not await selector.is_checked():await selector.check(timeout=1500)
                await page.locator('#'+qty).select_option('1',timeout=1500)
                filled.append('kit')
            except Exception:unresolved.append({'label':'Selected kit','reason':'The selected kit is unavailable for the demo location.'})
    current=runner.store.get('application',job['id'])
    current['demo_choices']={**current.get('demo_choices',{}),**choices}
    current['demo_filled']=sorted(set(current.get('demo_filled',[])+filled))
    runner.store.put('application',job['id'],current)
    return unresolved

async def run(runner,job):
    jid=job['id']
    runner.status(jid,'RUNNING','Opening the official browser. Demo preparation cannot submit.',auto_submit=False,submission_attempted=False)
    facts,docs=demo_inputs(runner.store)
    _,page=await runner.open(job)
    await runner.wait_for_page(page)
    policy=runner.demo_networks[jid]
    policy.loading=False
    # Visible banner within the browser, not a separate application panel.
    await page.evaluate("""() => {if(!document.getElementById('saathi-demo-notice')){
      const b=document.createElement('div');b.id='saathi-demo-notice';
      b.textContent='SchemeSaathi demo — fictional applicant — submission disabled';
      b.style.cssText='padding:10px;background:#fff3cd;color:#332700;font:14px sans-serif';document.body.prepend(b)}}""")
    for _ in range(8):
        if runner.store.get('application',jid)['status']=='CANCELLED':return
        unresolved=await fill(runner,job,page,facts,docs)
        challenge=await runner.challenge(page)
        if challenge:
            await runner.pause(job,page,'DEMO_BLOCKED','The public form is filled where possible. Later pages require a genuine login or OTP; fictional registration is not sent.',unresolved=unresolved);return
        if unresolved:
            await runner.pause(job,page,'NEEDS_INFORMATION','Review the listed fields in the browser. Demo values or live portal validation prevent further preparation.',unresolved=unresolved);return
        invalid=await page.evaluate("() => [...document.querySelectorAll('input,select,textarea')].some(e=>e.getClientRects().length&&!e.disabled&&!e.checkValidity())")
        if invalid:
            await runner.pause(job,page,'DEMO_BLOCKED','The official form rejects one or more fictional values. Demo stops here without creating an application.');return
        next_button=await tn_portals.visible_button(page,['Next','Continue','Save & Next','Save and Continue','அடுத்து','தொடரவும்'])
        if next_button:
            before=policy.blocks
            try:
                advanced=await tn_portals.advance(page,next_button)
            except Exception:
                if policy.blocks==before:raise
                advanced=False
            if not advanced or policy.blocks>before:
                await runner.pause(job,page,'DEMO_BLOCKED','This step needs a server request outside the public lookup list. Demo stops before sending fictional applicant data.');return
            continue
        final=await tn_portals.visible_button(page,['Submit','Submit application','Final submit','சமர்ப்பிக்க','சமர்ப்பிக்கவும்','சமர்ப்பிக்கவும் / Submit'])
        if final or await page.locator('#btnSubmit').count():
            await runner.pause(job,page,'DEMO_REVIEW','Reached the final form for review. Nothing was submitted; demo submission remains disabled.',unresolved=[]);return
        await runner.pause(job,page,'DEMO_BLOCKED','Available fields filled. No supported next or final step was found. Nothing was submitted.');return
    await runner.pause(job,page,'DEMO_BLOCKED','Reached the bounded preparation limit. Nothing was submitted.')
