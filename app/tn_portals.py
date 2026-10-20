"""Deterministic, bounded TN portal workflows.

AGRISNET and post-login welfare forms use runtime label matching: their live
end-to-end flows have NOT been certified. Unknown/ambiguous fields stop the run.
"""
import asyncio
import hashlib
import json
import re
import uuid
from urllib.parse import urlparse
from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from .tn_portal_data import PORTALS, BY_ADAPTER, DOCUMENTS
from .horticulture import inputs
from .store import now

class StartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    disclosure_confirmed: bool = False
    auto_submit: bool = False

class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=256)

class PortalChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mapping_key: str = Field(min_length=1, max_length=1000)
    value: str = Field(min_length=1, max_length=1000)


def readiness(spec, facts, docs):
    missing = [k for k in spec['required'] if facts.get(k) is None or str(facts.get(k, '')).strip() == '']
    missing_docs = [k for k in spec['documents'] if k not in docs]
    errors = []
    for key, pattern, message in [
        ('phone',r'[6-9][0-9]{9}','Mobile number must contain 10 digits.'),
        ('aadhaar_number',r'[2-9][0-9]{11}','Aadhaar must contain 12 digits and start with 2–9.'),
        ('ifsc',r'[A-Z]{4}0[A-Z0-9]{6}','IFSC must contain 11 characters in the standard format.'),
        ('bank_account',r'[0-9]{6,20}','Bank account number must contain 6–20 digits.')]:
        if key in spec['required'] and facts.get(key) and not re.fullmatch(pattern, str(facts[key])):
            errors.append(message)
    if 'beneficiary_photo' in spec['documents'] and docs.get('beneficiary_photo',{}).get('mime_type') not in (None,'image/jpeg','image/png'):
        errors.append('Farmer photograph must be JPG or PNG.')
    return {**{k:v for k,v in spec.items() if k not in ('allowed_hosts','adapter')},
            'missing_fields':missing, 'missing_documents':missing_docs, 'errors':errors,
            'can_prepare': not missing and not missing_docs and not errors}


def fingerprint(facts, docs):
    return hashlib.sha256(json.dumps({'facts':facts,'documents':{k:v['sha256'] for k,v in docs.items()}},sort_keys=True).encode()).hexdigest()


def install_routes(app, store, runner):
    @app.put('/api/tn-portals/applications/{jid}/choice')
    async def choose_option(jid: str, body: PortalChoice):
        async with runner.locks.setdefault(jid, asyncio.Lock()):
            job=store.get('application',jid)
            if not job or job.get('adapter') not in BY_ADAPTER:
                raise HTTPException(404,'Application not found.')
            if job['status']!='NEEDS_INFORMATION' or job.get('submission_attempted'):
                raise HTTPException(409,'Choices can only be changed during an unsubmitted preparation waiting for details.')
            facts,docs=inputs(store)
            if job.get('profile_fingerprint')!=fingerprint(facts,docs):
                raise HTTPException(409,'Profile changed. Cancel this preparation and start again.')
            fields=[f for f in job.get('unresolved',[]) if f.get('mapping_key')==body.mapping_key]
            if len(fields)!=1 or not fields[0].get('field') or not fields[0].get('options'):
                raise HTTPException(422,'Refresh the application and select a listed portal option.')
            field=fields[0];key=field['field']
            matches=[o for o in field['options'] if o['value']==body.value]
            if key not in ALIASES or key.startswith('doc:') or len(matches)!=1:
                raise HTTPException(422,'That option was not offered by the portal.')
            selected=matches[0]
            choices={**job.get('portal_choices',{}),key:selected['label']}
            records={**job.get('portal_option_ids',{}),key:{
                'value':selected['value'],'label':selected['label'],
                'mapping_key':body.mapping_key,'parents':field.get('parents',{}),
                'page_path':field.get('page_path','')}}
            # A parent selection invalidates choices made under the old parent.
            for child in CASCADES.get(key,[]):
                choices.pop(child,None);records.pop(child,None)
            effective={**facts,**choices}
            if job['adapter']==PORTALS['agrisnet']['adapter']:
                intent=[effective.get(k) for k in ['agri_department','agri_scheme','agri_input_type','agri_component']]
                intent_key=hashlib.sha256(json.dumps(intent).encode()).hexdigest()
                if any(j['id']!=jid and j.get('adapter')==job['adapter'] and j.get('intent_key')==intent_key and j['status']!='CANCELLED' for j in store.all('application')):
                    raise HTTPException(409,'Another application already exists for that selection.')
                job['intent_key']=intent_key
            job.update(portal_choices=choices,portal_option_ids=records,updated_at=now())
            # Reload dependent options after Resume instead of offering stale ones.
            for f in job['unresolved']:
                if f.get('field')==key:
                    f['selected_option_value']=selected['value']
                    f['selected_option_label']=selected['label']
                    f['reason']='Selection saved. Click Resume to apply it to the official form.'
                elif f.get('field') in CASCADES.get(key,[]):
                    f.pop('options',None)
                    f.pop('selected_option_value',None)
                    f.pop('selected_option_label',None)
                    f['reason']='Choose the parent location or scheme first, then Resume to load these options.'
            store.put('application',jid,job)
            return job

    @app.get('/api/tn-portals')
    async def overview(demo: bool = False):
        if demo:
            from .resume_demo import demo_inputs
            facts,docs = demo_inputs(store)
        else:
            facts,docs = inputs(store)
        portals=[{'key':k, **readiness(s,facts,docs)} for k,s in PORTALS.items()]
        if demo:
            for portal in portals:
                portal.update(can_prepare=not portal['missing_fields'] and not portal['missing_documents'],errors=[],validation="Preloaded demo opens the normal browser. Submission and fictional registration are disabled.")
        return {'portals':portals, 'documents':DOCUMENTS,
                'worker_password_saved':False if demo else bool(store.get('portal_secret','worker'))}

    @app.put('/api/tn-portals/worker/credentials')
    async def credentials(body: Credentials):
        store.put('portal_secret','worker',{'password':body.password})
        return {'saved':True}

    @app.delete('/api/tn-portals/worker/credentials')
    async def forget_credentials():
        store.delete('portal_secret','worker')
        return {'saved':False}

    @app.post('/api/tn-portals/{portal}/applications')
    async def start(portal: str, body: StartRequest, demo: bool = False):
        spec = PORTALS.get(portal)
        if not spec:
            raise HTTPException(404, 'Unknown portal workflow')
        if demo:
            from .demo_application import start as start_demo
            return start_demo(runner,spec,body.auto_submit)
        if not body.disclosure_confirmed:
            raise HTTPException(422,'Confirm sharing the required profile details and documents with this official portal.')
        facts,docs = inputs(store)
        # One outstanding or submitted request for each specific intent.
        intent = [facts.get(k) for k in (['agri_department','agri_scheme','agri_input_type','agri_component'] if portal=='agrisnet' else ['worker_claim_name'] if portal=='worker-claim' else [])]
        key = hashlib.sha256(json.dumps(intent).encode()).hexdigest()
        existing = next((j for j in store.all('application') if j.get('adapter')==spec['adapter'] and not j.get('demo') and j.get('intent_key')==key and j['status']!='CANCELLED'),None)
        if existing:
            return existing
        check = readiness(spec,facts,docs)
        if not check['can_prepare']:
            raise HTTPException(422,'Complete the required profile and documents: '+', '.join(check['missing_fields']+check['missing_documents'])+'. '+' '.join(check['errors']))
        jid = uuid.uuid4().hex
        job = {'id':jid,'scheme_id':spec['id'],'scheme_name':spec['name'],'adapter':spec['adapter'],
               'intent_key':key,'demo':False,'status':'QUEUED','auto_submit':body.auto_submit,
               'submission_attempted':False,'created_at':now(),'updated_at':now(),
               'disclosure_confirmed_at':now(),'consented_at':now() if body.auto_submit else None,
               'message':'Opening the selected official workflow.','unresolved':[], 'submission_blockers':[],
               'tracking_url':spec.get('tracking_url'),'validation':spec['validation'], 'stage':'entry'}
        store.put('application',jid,job)
        runner.start(jid)
        return job


def norm(value):
    value = re.sub(r'([a-z])([A-Z])',r'\1 \2',str(value or ''))
    value = re.sub(r'[\u200b-\u200f\ufeff]','',value).casefold()
    return ' '.join(re.sub(r'[_:*#\-–]+|\b(required|optional)\b',' ',value).split())


def parts(value):
    return {norm(value), *[norm(p) for p in str(value).split('/')]}

# Exact semantic matches only. Never ask an LLM to guess bank / land / family data.
ALIASES = {
 'full_name':['name of the farmer','விவசாயி பெயர்','beneficiary name','name of the applicant','applicant name','full name','worker name','தொழிலாளியின் பெயர்'],
 'relation_name':['name of father / spouse','name of father (or) spouse','father name','father / husband name','தந்தை/மனைவி பெயர்','தந்தை (அல்லது) மனைவியின் பெயர்'],
 'phone':['mobile number','mobile no','mobileno','கைபேசி எண்'],
 'aadhaar_number':['aadhaar number','aadhaar no','aadhar number','ஆதார் எண்'],
 'gender':['gender','sex','பாலினம்'], 'date_of_birth':['date of birth','பிறந்த தேதி'],
 'bank_account':['account number','bank account number','வங்கி கணக்கு எண்'],
 'ifsc':['ifsc','ifsc code','IFSC குறியீடு'],
 'address':['address','residential address','முகவரி'], 'pincode':['pincode','pin code','அஞ்சல் குறியீடு'],
 'district':['district','மாவட்டம்'], 'block':['block','வட்டாரம்'], 'village':['village','கிராமம்'],
 'agri_department':['department','துறை'], 'agri_scheme':['scheme','திட்டம்'],
 'agri_input_type':['input type','வகை'], 'agri_component':['component','இனங்கள்'],
 'agri_social_status':['social status','சமூக நிலை'],
 'land_district':['land district'], 'land_taluk':['taluk','land taluk','வட்டம்'], 'land_village':['land village'],
 'land_survey_number':['survey number /sub division number','survey number','sub division number','சர்வே எண் /உட்பிரிவு எண்'],
 'land_area_acres':['land area of survey number(acres)','land area of survey number (acres)','land area (acres)','சாகுபடி பயிர் பரப்பளவு (ஏக்கர்)'],
 'cultivable_crop':['cutivable crop','cultivable crop','சாகுபடி பயிர்'],
 'land_ownership':['ownership details','ownership','land ownership'],
 'land_address_same':['residence and land location are the same','residential address and land address are same','same as residential address','இருப்பிட முகவரியும் நில முகவரியும் ஒன்றாக இருப்பின் இங்கு கிளிக் செய்யவும்'],
 'ration_card_number':['ration card or smart card or குடும்ப அட்டை எண்','ration card number','smart card number','rationcardno','குடும்ப அட்டை எண்'],
 'worker_registration_number':['registration number','worker registration number','username','பயனர் பெயர்'],
 'worker_board':['board','board name','name of the board','நலவாரியம்'],
 'occupation':['nature of work','occupation','தொழில்'],
 'nominee_name':['nominee name','name of nominee','நியமனதாரர் பெயர்'],
 'nominee_relationship':['nominee relationship','relationship with nominee','நியமனதாரர் உறவு'],
 'marital_status':['marital status','திருமண நிலை'],
 'doc:beneficiary_photo':['farmer photo','விவசாயியின் புகைப்படம்','beneficiary image','photograph','photo'],
 'doc:occupation_certificate':['employment certificate','occupation certificate','பணிச்சான்றிதழ்'],
 'doc:identity_proof':['identity proof','identity certificate','அடையாளச் சான்றிதழ்'],
 'doc:ration_card':['ration card','smart card','குடும்ப அட்டை'],
 'doc:bank_passbook':['bank passbook','bank passbook first page','வங்கி கணக்குப்புத்தகம் முதல் பக்கம்'],
 'doc:aadhaar_document':['aadhaar card','aadhaar document','ஆதார் அட்டை'],
 'doc:nominee_proof':['nominee proof','nominee relationship proof','வாரிசாக பரிந்துரைக்கப்படுபவர் ஆவணம்'],
 'doc:study_certificate':['study certificate'], 'doc:admission_proof':['admission proof'],
 'doc:income_certificate':['income certificate'], 'doc:community_certificate':['community certificate'],
 'doc:marksheet':['marksheet'],
}

CASCADES = {
 'agri_department':['agri_scheme','agri_input_type','agri_component'],
 'agri_scheme':['agri_input_type','agri_component'], 'agri_input_type':['agri_component'],
 'district':['block','village'], 'block':['village'],
 'land_district':['land_taluk','land_village'], 'land_taluk':['land_village'],
}

INSPECT = r'''() => {
const selector='input,select,textarea,[role="combobox"]';
const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
const controls=Array.from(document.querySelectorAll(selector));
const textOf=l=>{const c=l.cloneNode(true);c.querySelectorAll(selector+',button,script,style').forEach(e=>e.remove());return c.textContent.replace(/\s+/g,' ').trim()};
const meaningful=t=>t && !/^(default select example|select|choose|select an option)[.…]*$/i.test(t.trim());
const headings=Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6,legend,strong,b,div,p,span'))
 .filter(h=>!h.querySelector(selector) && /^(land details|residential address|நில விவரங்கள்|இருப்பிட முகவரி)(\s*[/—:-]|$)/i.test(h.textContent.trim()) && h.textContent.trim().length<100);
return controls.map((e,i)=>{
 if (!visible(e)||e.disabled||e.readOnly||['hidden','submit','button','reset','image'].includes(e.type)) return null;
 let label=Array.from(e.labels||[]).map(textOf).filter(meaningful).join(' ');
 const aria=e.getAttribute('aria-label')||'';
 if (!label && !['radio','checkbox'].includes(e.type) && meaningful(aria)) label=aria;
 // Real pages often place text beside controls without a label/for attribute.
 // Stop at the smallest field container; never borrow a neighbouring column.
 if (!label && ['radio','checkbox'].includes(e.type)) {
  let n=e.nextSibling, t='';
  while(n && !(n.nodeType===1 && (n.matches(selector+',br')||n.querySelector(selector)))) {
   t+=n.textContent||'';n=n.nextSibling;
  }
  if(meaningful(t.trim())) label=t.trim();
 }
 if (!label && meaningful(aria) && ['radio','checkbox'].includes(e.type)) label=aria;
 if (!label) {
  for(let parent=e.parentElement;parent && parent!==document.body;parent=parent.parentElement){
   const peers=Array.from(parent.querySelectorAll(selector)).filter(x=>x.type!=='hidden');
   if(peers.length!==1) break;
   const text=textOf(parent);
   if(meaningful(text) && text.length<240){label=text;break;}
  }
 }
 if (!label) {
  const prev=e.previousElementSibling;
  if(prev && !prev.matches(selector) && !prev.querySelector(selector) && meaningful(prev.textContent) && prev.textContent.trim().length<180) label=prev.textContent;
 }
 if(!label && meaningful(e.placeholder)) label=e.placeholder;
 let section='';
 for(const h of headings) if(visible(h)&&(h.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING)) section=h.textContent.trim();
 const group=e.closest('fieldset')?.querySelector('legend')?.innerText || '';
 e.setAttribute('data-tn-field',String(i));
 return {index:i,label:label.trim(),id:e.id,name:e.name||'',type:e.type||e.getAttribute('role'),tag:e.tagName.toLowerCase(),section,group,
  required:!!e.required,hasValue:e.type==='checkbox'||e.type==='radio'?e.checked:!!e.value,
  optionValue:e.type==='radio'?e.value:null,
  options:e.tagName==='SELECT'?Array.from(e.options).map(o=>({value:o.value,label:o.text,disabled:o.disabled})):[]};
}).filter(Boolean);
}'''

SENSITIVE_CONTROL = re.compile(r'password|captcha|\botp\b|one.?time|verification.?code|security.?code|declare|declaration|i agree|consent|terms|உறுதிமொழி|ஒப்புக்கொள்கிறேன்', re.I)


def resolve(field, mappings, agri=False):
    label=field['label']
    identity=' '.join([label,field.get('name',''),field.get('id','')])
    if SENSITIVE_CONTROL.search(identity): return None
    # Visual semantics win over old mappings. Versioned identities prevent the
    # previous generic aria-label mappings leaking across different controls.
    names=parts(label) if label else set()
    keys={k for k,aliases in ALIASES.items() if names & {norm(x) for x in aliases}}
    if not keys:
        names={norm(field.get('name')),norm(field.get('id')),norm(field.get('group'))}
        keys={k for k,aliases in ALIASES.items() if names & {norm(x) for x in aliases}}
    if len(keys)==1:
        key=keys.pop()
        if key in ('district','village') and re.search(r'land details|நில விவரங்கள்',field.get('section',''),re.I):key='land_'+key
        return key
    return mappings.get(mapping_key(field))


def mapping_label(field):
    return ((field.get('section','')+' — ') if field.get('section') else '')+(field['label'] or field['name'] or field['id'] or 'Unlabelled field '+str(field['index']+1))


def mapping_key(field):
    identity=field.get('id') or field.get('name') or str(field['index'])
    return norm('v2 | '+mapping_label(field)+' | '+identity)


async def available_options(loc):
    opts=await loc.locator('option').evaluate_all('(es)=>es.map(e=>({value:e.value,label:e.textContent.trim(),disabled:e.disabled}))')
    # Keep distinct portal IDs even when their translated labels are identical.
    # Identical duplicate <option> elements represent the same submitted value.
    unique={(o['value'],o['label']):o for o in opts if not o['disabled'] and o['value'] not in ('','0','-1')}
    return list(unique.values())


async def fill_page(runner, job, page, facts, docs):
    fields=await page.evaluate(INSPECT)
    mappings=runner.store.get('mapping',job['scheme_id'],{})
    unresolved=[]; filled=[]; groups=set(); expected=[]; pending=set(); resolved={}
    # Process controls in document order. Reinspect each control's live options,
    # since district / scheme selections replace dependent option lists.
    for field in fields:
        label=mapping_label(field)
        loc=page.locator(f'[data-tn-field="{field["index"]}"]')
        if not await loc.count() or not await loc.is_visible() or not await loc.is_enabled():continue
        key=resolve(field,mappings,job['adapter']==PORTALS['agrisnet']['adapter'])
        if field['type']=='radio':
            group=field['name']
            if not group or group in groups:continue
            groups.add(group)
            members=[f for f in fields if f['type']=='radio' and f['name']==group]
            labels={p for f in members for p in parts(f['label'])}
            if {'owner','cultivator'}<=labels:key='land_ownership'
            elif {'male','female'}<=labels:key='gender'
            if not key:
                key=resolve({**field,'label':field.get('group') or group},mappings)
            if key:
                val=norm(facts.get(key,''))
                matches=[f for f in members if val and val in (parts(f['label']) | parts(f.get('optionValue','')))]
                if len(matches)==1:
                    await page.locator(f'[data-tn-field="{matches[0]["index"]}"]').check();filled.append(key);expected.append((field,key,matches[0]['index'],'radio'));continue
            unresolved.append({'label':label,'mapping_key':mapping_key(field),'reason':'Radio group needs an exact confirmed choice.'});continue
        if SENSITIVE_CONTROL.search(' '.join([field['label'],field['name'],field['id']])):
            if field['hasValue']:continue  # User explicitly completed a declaration in the browser.
            unresolved.append({'label':label,'mapping_key':mapping_key(field),'reason':'Complete this verification or declaration in the official browser.'});continue
        if not key:
            if field['hasValue']:continue
            if not field['required'] and re.search(r'^search inputs|இடுபொருள் தேடுக',field['label'],re.I):continue
            unresolved.append({'label':label,'mapping_key':mapping_key(field),'reason':'Unknown field: map a confirmed value or complete it in the browser.'});continue
        if any(key in CASCADES.get(parent,[]) for parent in pending):
            pending.add(key)
            unresolved.append({'label':label,'mapping_key':mapping_key(field),'field':key,'reason':'Choose the parent location or scheme first, then Resume to load these options.'})
            continue
        try:
            if key.startswith('doc:'):
                d=docs.get(key[4:])
                if not d or field['type']!='file':raise ValueError('Upload the matching document in Documents.')
                if field['hasValue']:continue
                accept=await loc.get_attribute('accept') or ''
                if accept and not any(x in accept for x in (d['mime_type'],'image/*' if d['mime_type'].startswith('image/') else d['mime_type'],'.'+d['filename'].rsplit('.',1)[-1].lower())):
                    raise ValueError('Document format does not match this upload field.')
                blob=runner.store.get_blob(d['blob'])
                await loc.set_input_files({'name':d['filename'],'mimeType':d['mime_type'],'buffer':runner.store.cipher.decrypt(blob)})
            else:
                value=facts.get(key)
                if value is None:raise ValueError('No confirmed profile value.')
                if field['tag']=='select':
                    found=False
                    record=job.get('portal_option_ids',{}).get(key)
                    parents={p:resolved[p] for p in CASCADES if key in CASCADES[p] and p in resolved}
                    if record and (record['parents']!=parents or record['mapping_key']!=mapping_key(field) or record['page_path']!=urlparse(page.url).path):
                        raise ValueError('The parent selection or form changed. Choose this option again from the current list.')
                    for _ in range(24):
                        opts=await available_options(loc)
                        candidates=([o for o in opts if o['value']==record['value'] and o['label']==record['label']] if record else
                                    [o for o in opts if norm(value) in parts(o['label'])])
                        if len(candidates)==1 and sum(o['value']==candidates[0]['value'] for o in opts)>1:
                            raise ValueError('The portal reuses an option ID for different choices. Check this field in the official browser.')
                        if len(candidates)==1:
                            if await loc.input_value()!=candidates[0]['value']:
                                await loc.select_option(candidates[0]['value'])
                            resolved[key]=candidates[0]['value']
                            expected.append((field,key,candidates[0]['value'],'select'))
                            found=True;break
                        await page.wait_for_timeout(250)
                    if not found:
                        if len(candidates)>1:raise ValueError('Several portal options have the same label. Choose the intended entry by its portal option ID, then Resume.')
                        raise ValueError('Saved choice does not match an available portal option. Select a listed option, then Resume.')
                elif field['type']=='checkbox':
                    if type(value) is not bool:raise ValueError('Checkbox needs an explicit yes/no value.')
                    await loc.set_checked(value)
                    expected.append((field,key,value,'checkbox'))
                elif field['type']=='combobox':raise ValueError('Custom dropdown is not yet supported; select the option in the browser.')
                else:
                    if isinstance(value,float) and value.is_integer():value=int(value)
                    if await loc.input_value()!=str(value):await loc.fill(str(value))
                    expected.append((field,key,str(value),'text'))
            filled.append(key)
        except Exception as exc:
            pending.add(key)
            item={'label':label,'mapping_key':mapping_key(field),'field':key,'reason':str(exc) if isinstance(exc,ValueError) else 'Portal did not accept this field.'}
            if field['tag']=='select':
                try:
                    item['options']=await available_options(loc)
                    item['parents']={p:resolved[p] for p in CASCADES if key in CASCADES[p] and p in resolved}
                    item['page_path']=urlparse(page.url).path
                except Exception:pass
            unresolved.append(item)
    # AJAX lookups may clear earlier values after fill() succeeded. Check the
    # actual form again; never report preparation based on old fill events.
    await page.wait_for_timeout(500)
    for field,key,value,kind in expected:
        loc=page.locator(f'[data-tn-field="{value if kind=="radio" else field["index"]}"]')
        try:
            if not await loc.count():raise ValueError('Field replaced')
            if not await loc.is_visible():continue
            actual=await loc.is_checked() if kind in ('radio','checkbox') else await loc.input_value()
            if kind=='select':
                selected_label=await loc.locator('option:checked').text_content()
                if norm(facts.get(key,'')) not in parts(selected_label or ''):raise ValueError('Options changed')
            wanted=True if kind=='radio' else value
            if actual!=wanted:
                if kind=='text' and not actual and await loc.is_enabled() and await loc.get_attribute('readonly') is None:
                    await loc.fill(value)
                    await page.wait_for_timeout(300)
                    actual=await loc.input_value()
                if actual!=wanted:raise ValueError('Portal changed this value after filling. Check the official form and Resume.')
        except Exception:
            unresolved.append({'label':mapping_label(field),'mapping_key':mapping_key(field),'field':key,'reason':'Portal changed or replaced this field after filling. Check the official form and Resume.'})
    if filled:runner.store.event(job['id'],'browser',f'Prepared {len(filled)} portal fields.',fields=filled)
    return unresolved,len(fields)


async def visible_button(page, names, include_links=False):
    matches=[]
    selector='button,input[type=submit],input[type=button],a' if include_links else 'button,input[type=submit],input[type=button],a[role=button]'
    for item in await page.locator(selector).all():
        if not await item.is_visible() or not await item.is_enabled():continue
        text=await item.inner_text() if await item.evaluate('(e)=>e.tagName')!='INPUT' else await item.input_value()
        clean=re.sub(r'[\uf000-\uf8ff➜➤➔➲▶►→›»]','',text).strip()
        if parts(clean)&{norm(n) for n in names}:matches.append(item)
    return matches[0] if len(matches)==1 else None


async def shape(page):
    # A step fingerprint contains control structure, never entered private values.
    return await page.evaluate('''()=>JSON.stringify([location.pathname,Array.from(document.querySelectorAll('input,select,textarea,button,h1,h2,h3')).filter(e=>e.getClientRects().length).map(e=>[e.tagName,e.id,e.name,e.type,e.tagName.match(/H[123]|BUTTON/)?e.innerText:''])])''')


async def advance(page, button):
    before=await shape(page)
    await button.click()
    for _ in range(24):
        await page.wait_for_timeout(250)
        if await shape(page)!=before:return True
    return False


async def fill_auth_value(runner,job,page,selector,value,title):
    """Do not fill a locked or already-correct entry control."""
    loc=page.locator(selector)
    if not await loc.count() or not await loc.is_visible():return True
    if (await loc.input_value()).strip()==str(value).strip():return True
    if not await loc.is_editable():
        await runner.pause(job,page,'LOGIN_REQUIRED',
            f'The portal has locked the {title} and it does not match the saved profile. Check the official registration session and reset it manually if needed.');return False
    try:
        await loc.fill(str(value))
    except PlaywrightTimeoutError:
        await runner.pause(job,page,'LOGIN_REQUIRED',
            f'The portal did not allow the {title} to be entered. Check the official browser for a locked field or popup, then use Resume.');return False
    return True


def registration_dialogs(runner,job,page):
    """Keep the observed mobile confirmation working during manual retries too.

    Installed only after entry values match the saved profile. Never query the
    DOM with a modal open, or retain dialog text that could contain personal data.
    """
    existing=getattr(page,'_saathi_registration_dialogs',None)
    if existing is not None:return existing
    signals=[]
    confirmed="great! you're a member of this family. do you want to use this mobile number to complete the registration."
    async def dialog_seen(dialog):
        text=' '.join(dialog.message.casefold().replace('’',"'").split())
        current=runner.store.get('application',job['id'],{})
        url=urlparse(page.url)
        permitted=(current.get('disclosure_confirmed_at') and
                   current.get('status') in {'RUNNING','LOGIN_REQUIRED','VERIFICATION_REQUIRED'} and
                   current.get('profile_fingerprint')==fingerprint(*inputs(runner.store)) and
                   url.scheme=='https' and url.hostname in PORTALS['worker-registration']['allowed_hosts'] and
                   url.path.rstrip('/')=='/applications/register')
        if permitted and text==confirmed and dialog.type in {'alert','confirm'}:
            await dialog.accept()
            runner.store.event(job['id'],'browser','Acknowledged the portal mobile-registration confirmation.')
            return
        ration_request='the mobile number provided is already used by another applicant. please enter a valid ration card number.'
        if permitted and dialog.type=='alert' and text==ration_request and not current.get('otp_ration_retry_used'):
            current['otp_ration_card_required']=True
            runner.store.put('application',job['id'],current)
            await dialog.dismiss()
            runner.store.event(job['id'],'browser','The portal requested a ration-card number before continuing the OTP request.')
            return
        reason=('The portal reported a CAPTCHA problem. Complete or refresh CAPTCHA in the official browser.' if 'captcha' in text else
                'The portal reports that this mobile number is already registered. Check the existing account.' if 'already' in text and 'register' in text else
                'The portal reported a mobile-number validation problem. Check the saved number and official form.' if 'mobile' in text and any(w in text for w in ['invalid','enter','valid']) else
                'The portal displayed an unrecognized registration dialog. It was not accepted; check the official registration page.')
        signals.append(reason)
        await dialog.dismiss()
        runner.store.event(job['id'],'browser',reason)
    page.on('dialog',dialog_seen)
    page._saathi_registration_dialogs=signals
    return signals


def registration_network(page):
    """Observe OTP requests without retaining URLs, phone numbers or responses."""
    existing=getattr(page,'_saathi_registration_network',None)
    if existing is not None:return existing
    state={}
    def matches(request):
        url=urlparse(request.url)
        return (url.scheme=='https' and url.hostname in PORTALS['worker-registration']['allowed_hosts'] and
                url.path.startswith('/ajaxpages/ajaxsendotp/'))
    def requested(request):
        if matches(request):
            state.clear()
            state.update(request_seen=True,cross_origin=urlparse(request.url).netloc!=urlparse(page.url).netloc)
    def responded(response):
        if matches(response.request):state['http_status']=response.status
    def failed(request):
        if matches(request):state['request_failed']=True
    page.on('request',requested)
    page.on('response',responded)
    page.on('requestfailed',failed)
    page._saathi_registration_network=state
    return state


async def canonical_registration_entry(runner,job,page):
    """Repair only an unverified entry page; never reload OTP or member forms."""
    url=urlparse(page.url)
    if (job['adapter']!=PORTALS['worker-registration']['adapter'] or
        job.get('submission_attempted') or url.scheme!='https' or
        url.hostname!='www.tnuwwb.tn.gov.in' or url.path.rstrip('/')!='/applications/register'):
        return
    if not await page.locator('#registerform:visible').count():return
    if await page.locator('#registerform #otp:visible').count():return
    if not await page.locator('#registerform #mobileno:visible').count():return
    if await visible_button(page,['Login','Send OTP'],include_links=True) is None:return
    runner.store.event(job['id'],'browser','Reopening the registration entry on the official address used by its OTP service.')
    # Keep any earlier OTP attempt marker: a failed browser callback does not
    # prove that the server did not send an SMS. No automatic duplicate request.
    await page.goto(PORTALS['worker-registration']['portal_url'],wait_until='domcontentloaded',timeout=30000)
    await runner.wait_for_page(page)


async def registration_action(runner,job,page,button,is_send):
    """One Send OTP attempt; wait for the portal, never infer SMS delivery."""
    if is_send:
        current=runner.store.get('application',job['id'])
        ration=page.locator('#registerform #rationcardno:visible')
        complete_ration=(current.get('otp_ration_card_required') and not current.get('otp_ration_retry_used') and
                        await ration.count()==1 and bool((await ration.input_value()).strip()))
        if current.get('otp_request_attempted_at') and not complete_ration:
            await runner.pause(job,page,'VERIFICATION_REQUIRED',
                'A Send OTP attempt is already recorded, but the OTP input is not visible. Check the official browser and any validation message. If needed, use its Send OTP/Resend control yourself; Resume will not send repeated requests.');return True
        if complete_ration:
            current['otp_ration_card_required']=False
            current['otp_ration_retry_used']=True
        current['otp_request_attempted_at']=now()
        runner.store.put('application',job['id'],current)
    before=await shape(page)
    signals=registration_dialogs(runner,job,page)
    signals.clear()
    network=registration_network(page)
    network.clear()
    try:
        await button.click()
        for _ in range(60):
            await page.wait_for_timeout(250)
            if signals:break
            if await shape(page)!=before:return 'advanced'
    except PlaywrightTimeoutError:
        signals.append('The registration button timed out. Check the official browser for a blocked control, popup or slow response.')
    message=signals[-1] if signals else ('Send OTP was clicked, but the portal has not shown an OTP input. SMS delivery is not confirmed. Check CAPTCHA, validation messages and your phone; if needed, use Send OTP/Resend in the official browser.' if is_send else 'The registration entry did not advance. Check the official page for validation messages.')
    if is_send and not signals:
        if network.get('cross_origin'):
            message='The registration page and OTP service use different browser origins. The request could not be confirmed. Close this application browser and Resume to open the corrected entry address. Do not request another OTP if one already arrived.'
        elif network.get('http_status',200)>=400:
            message=f'The OTP service returned HTTP {network["http_status"]}. Check the official browser and try again later; automatic repeat requests are disabled.'
        elif network.get('request_failed'):
            message='The OTP request failed in the browser. Check your connection and the official page. SMS delivery is not confirmed; automatic repeat requests are disabled.'
        elif not network.get('request_seen'):
            message='Send OTP was clicked, but no OTP-service request was observed and no OTP field appeared. The page may not have finished loading or its script failed. Use Send OTP once in the official browser if no code arrived.'
    if is_send and network:
        runner.store.event(job['id'],'browser','OTP request diagnostic (no phone number or code recorded).',otp_network=dict(network))
    await runner.pause(job,page,'VERIFICATION_REQUIRED' if is_send else 'LOGIN_REQUIRED',message)
    return True


async def authentication(runner,job,page,facts):
    """Only observed entry controls. OTP/CAPTCHA content is never read or solved."""
    if job['adapter']==PORTALS['worker-registration']['adapter'] and await page.locator('#registerform').count():
        if not await fill_auth_value(runner,job,page,'#mobileno',facts['phone'],'mobile number'):return True
        if not await fill_auth_value(runner,job,page,'#rationcardno',facts['ration_card_number'],'ration-card number'):return True
        registration_dialogs(runner,job,page)
        registration_network(page)
        otp=page.locator('#otp')
        if await otp.count() and await otp.is_visible():
            await runner.pause(job,page,'VERIFICATION_REQUIRED','Enter OTP in the official browser. The app verifies the entered code and continues automatically.');return True
        captcha=page.locator('input[placeholder*=captcha i]:visible,input[name*=captcha i]:visible,.g-recaptcha:visible,.h-captcha:visible')
        if await captcha.count():
            await runner.pause(job,page,'VERIFICATION_REQUIRED','Complete the registration CAPTCHA in the browser, then Resume to request OTP.');return True
        button=await visible_button(page,['Login','Send OTP'],include_links=True)
        if button:
            text=await button.inner_text() if await button.evaluate('(e)=>e.tagName')!='INPUT' else await button.input_value()
            return await registration_action(runner,job,page,button,'send otp' in norm(text))
        if await runner.challenge(page):
            await runner.pause(job,page,'VERIFICATION_REQUIRED','Complete verification in the official browser. Automation continues after it clears.');return True
        # Never mistake an unexplored authentication Submit for final membership submission.
        await runner.pause(job,page,'UNSUPPORTED_FORM','Registration entry changed. The authenticated membership form has not appeared.');return True
    if job['adapter']==PORTALS['worker-claim']['adapter'] and await page.locator('#userLogin').count():
        username=page.locator('#username');password=page.locator('#password')
        if await username.is_visible():await username.fill(str(facts['worker_registration_number']))
        secret=runner.store.get('portal_secret','worker',{})
        if secret.get('password') and await password.is_visible():await password.fill(secret['password'])
        await runner.pause(job,page,'VERIFICATION_REQUIRED' if secret.get('password') else 'LOGIN_REQUIRED',
                           'Enter CAPTCHA in the official browser. The app clicks Login and continues automatically.' if secret.get('password') else 'Log in with your approved member credentials and CAPTCHA. You can save the password once in TN applications. Automation continues after login.');return True
    return False


async def receipt(runner,page):
    # Conservative English parser plus explicit Tamil acknowledgement. Historical
    # instructions saying an application number WILL be sent never count as success.
    result=await runner.receipt(page,False)
    if result:return result
    text=await page.locator('body').inner_text()
    success=re.search(r'(?:registration|claim|request) (?:has been )?(?:submitted|registered) successfully|(?:registration|claim|request) successfully submitted|விண்ணப்பம் வெற்றிகரமாக சமர்ப்பிக்கப்பட்டது',text,re.I)
    if not success:return None
    match=re.search(r'(?:application|registration|claim|request|acknowledgement)\s*(?:number|no\.?|id|reference)\s*[:#-]\s*([A-Z0-9][A-Z0-9/-]{5,49})|விண்ணப்ப எண்\s*[:#-]\s*([A-Z0-9][A-Z0-9/-]{5,49})',text,re.I)
    return next((x for x in match.groups() if x),None) if match else None


async def run(runner,job):
    spec=BY_ADAPTER[job['adapter']];jid=job['id']
    facts,docs=inputs(runner.store);token=fingerprint(facts,docs)
    facts={**facts,**job.get('portal_choices',{})}
    uncertain=job.get('submission_attempted',False)
    if not uncertain:
        if job.get('profile_fingerprint') and job['profile_fingerprint']!=token:
            runner.status(jid,'PROFILE_CHANGED','Profile or documents changed. Cancel this preparation and start again.');return
        check=readiness(spec,facts,docs)
        if not check['can_prepare']:
            runner.status(jid,'NOT_READY','Complete the required saved profile and documents.');return
    runner.status(jid,'RUNNING','Continuing the selected official workflow.',profile_fingerprint=token,unresolved=[],auto_resume_waiting=False)
    _,page=await runner.open(job)
    await runner.wait_for_page(page)
    await canonical_registration_entry(runner,job,page)
    for step in range(16):
        current=runner.store.get('application',jid)
        if current['status']=='CANCELLED':return
        if urlparse(page.url).hostname not in spec['allowed_hosts']:
            await runner.pause(job,page,'UNSUPPORTED_PORTAL','Navigation left the approved portal.');return
        if uncertain:
            ref=await receipt(runner,page)
            await runner.pause(job,page,'SUBMITTED' if ref else 'SUBMISSION_UNVERIFIED',
                'Official acknowledgement detected. Approval remains with the department.' if ref else 'Previous submission remains unverified. Check the official account; resubmission is disabled.',**({'receipt':ref} if ref else {}));return
        auth=await authentication(runner,job,page,facts)
        if auth=='advanced':continue
        if auth:return
        challenge=await runner.challenge(page)
        if challenge:
            await runner.pause(job,page,challenge,'Complete verification in the official browser. Automation continues when the challenge clears.');return
        if await receipt(runner,page):
            await runner.pause(job,page,'REVIEW_EXISTING_RECEIPT','An existing acknowledgement is visible. No new application was submitted.');return
        if job['adapter']==PORTALS['worker-claim']['adapter'] and not current.get('claim_opened'):
            # Exact text must be supplied by the citizen; no broad "Apply" click on a dashboard.
            links=page.get_by_role('link',name=str(facts['worker_claim_name']),exact=True)
            if await links.count()!=1:
                await runner.pause(job,page,'NEEDS_INFORMATION','The exact selected claim link is not visible. Open the appropriate claim form or update the saved claim title.');return
            href=await links.get_attribute('href')
            from urllib.parse import urljoin
            target=urlparse(urljoin(page.url,href or ''))
            if target.scheme!='https' or target.hostname not in spec['allowed_hosts']:
                await runner.pause(job,page,'UNSUPPORTED_PORTAL','The chosen claim link leaves the approved welfare portal.');return
            await links.click();await runner.wait_for_page(page)
            runner.status(jid,'RUNNING','Selected claim opened.',claim_opened=True,stage='claim');continue
        unresolved,count=await fill_page(runner,job,page,facts,docs)
        if unresolved:
            await runner.pause(job,page,'NEEDS_INFORMATION','Some fields need confirmed details. The exact unresolved labels are listed below.',unresolved=unresolved);return
        if fingerprint(*inputs(runner.store))!=token:
            await runner.pause(job,page,'PROFILE_CHANGED','Profile changed during filling. Cancel preparation and start again.');return
        # Native validity covers required fields even if a portal inserted them late.
        invalid=await page.evaluate('''()=>Array.from(document.querySelectorAll('input,select,textarea')).some(e=>e.getClientRects().length&&!e.disabled&&!e.checkValidity())''')
        if invalid:
            await runner.pause(job,page,'NEEDS_INFORMATION','The portal reports incomplete or invalid fields. Check its validation messages.');return
        next_button=await visible_button(page,['Next','Continue','Save & Next','Save and Continue','அடுத்து','தொடரவும்'])
        if next_button:
            if not await advance(page,next_button):
                await runner.pause(job,page,'NEEDS_INFORMATION','The portal did not advance. Check validation messages; no final submission was made.');return
            continue
        final=await visible_button(page,['Submit','Submit application','Final submit','சமர்ப்பிக்க','சமர்ப்பிக்கவும்'])
        if not final:
            await runner.pause(job,page,'UNSUPPORTED_FORM','No recognized application step is visible. The portal layout may need a dedicated update.');return
        if not runner.store.get('application',jid).get('auto_submit'):
            await runner.pause(job,page,'READY_FOR_SUBMISSION','Fields are prepared. Review the official form, then choose Submit application.');return
        # Final fresh inventory stops newly introduced controls before any irreversible write.
        again,_=await fill_page(runner,job,page,facts,docs)
        if again:
            await runner.pause(job,page,'NEEDS_INFORMATION','The form changed before submission.',unresolved=again);return
        if fingerprint(*inputs(runner.store))!=token:
            await runner.pause(job,page,'PROFILE_CHANGED','Profile changed. Cancel preparation and start again.');return
        runner.status(jid,'SUBMITTING','Submitting once and checking the official acknowledgement.',submission_attempted=True,stage='receipt')
        await final.click()
        ref=None
        for _ in range(20):
            await page.wait_for_timeout(500)
            ref=await receipt(runner,page)
            if ref:break
        await runner.pause(job,page,'SUBMITTED' if ref else 'SUBMISSION_UNVERIFIED',
          ('Registration acknowledgement detected. Wait for officer approval before creating a member benefit claim.' if job['adapter']==PORTALS['worker-registration']['adapter'] else 'Application acknowledgement detected. Government approval is separate.') if ref else 'Submit was attempted, but the acknowledgement could not be verified. Check the official account before any further action; automatic retry is disabled.', **({'receipt':ref} if ref else {}))
        return
    await runner.pause(job,page,'NEEDS_INFORMATION','Reached the bounded step limit. Inspect the current form before resuming.')


async def continue_auth(runner, job, page, memory):
    """Submit a user-entered code on the two observed auth forms.

    Does not read CAPTCHA images or generate codes. Stable input is hashed only
    in ephemeral memory to avoid retrying an unchanged rejected code. No code,
    hash, or password is written to logs, application records or AI prompts.
    """
    # The known no-OTP-yet ration-card challenge may follow a manual Send OTP
    # retry. Resume the normal filler once that specific input becomes visible.
    if job.get('adapter')==PORTALS['worker-registration']['adapter'] and job.get('otp_ration_card_required') and not job.get('otp_ration_retry_used'):
        if await page.locator('#registerform #rationcardno:visible').count()==1:
            active=runner.tasks.get(job['id'])
            if not active or active.done():
                runner.status(job['id'],'QUEUED','Continuing with the saved ration-card number requested by the portal.',auto_resume_waiting=False)
                runner.start(job['id'])
            return
    selector=None; names=[]; minimum=1
    if job.get('adapter')==PORTALS['worker-registration']['adapter'] and await page.locator('#registerform:visible #otp:visible').count()==1:
        selector='#registerform #otp';names=['Verify OTP'];minimum=4
    elif job.get('adapter')==PORTALS['worker-claim']['adapter'] and await page.locator('#userLogin:visible').count()==1:
        captcha=page.locator('#userLogin input[placeholder*=captcha i]:visible')
        if await captcha.count()==1:
            selector='#userLogin input[placeholder*=captcha i]';names=['LOGIN']
            if not await page.locator('#username').input_value() or not await page.locator('#password').input_value():return
    if not selector:return
    code=await page.locator(selector).input_value()
    digest=hashlib.sha256(code.encode()).hexdigest()
    length=len(code.strip());del code
    if length<minimum:return
    if memory.get('last')!=digest:
        memory['last']=digest;memory['stable']=0;return
    memory['stable']=memory.get('stable',0)+1
    if memory['stable']<2 or digest in memory.setdefault('sent',set()) or len(memory['sent'])>=5:return
    # The live registration script validates on OTP input and replaces Verify
    # OTP with a separate Submit control. This scoped Submit completes login,
    # not a membership application; never match a generic page-wide Submit.
    button=None
    if job.get('adapter')==PORTALS['worker-registration']['adapter']:
        if await page.locator('#registerform .formsubmit_btn').count():
            verified=page.locator('#registerform .formsubmit_btn button[type=submit]:visible')
            if await page.locator('#registerform .otp_error:visible').count():return
            if await verified.count()!=1 or not await verified.is_enabled():return
            button=verified
    if button is None:button=await visible_button(page,names,include_links=True)
    if button:
        memory['sent'].add(digest)
        await button.click()
