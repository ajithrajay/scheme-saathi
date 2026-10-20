"""Public official-page demonstration; network locked before synthetic filling.

Separate from the application runner: no submit, resume, credentials, uploads,
receipts or registration. Only configured HTTPS entry pages can be opened.
"""
import asyncio
import os
import uuid
from urllib.parse import urlparse
from fastapi import HTTPException
from fastapi.responses import Response
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field
from . import horticulture, tn_portals
from .browser import map_field
from .network_guard import public_destination
from .resume_demo import demo_facts, document_catalogue, ASSETS

SITES = {'horticulture': horticulture.SCHEME, **tn_portals.PORTALS}
HORT_IDS = dict(aadhaar_no='aadhaar_number', benificiary_name='full_name',
    relation_name='relation_name', phoneNumber='phone', address='address',
    pincode='pincode', gender='gender', social_status='kit_category',
    checkLocality='area_type', dist_id='district', block_id='block', village_id='village',
    beneficiary_image='doc:beneficiary_photo')

class PreviewRequest(BaseModel):
    portal: str
    model_config = {'extra': 'forbid'}

class ScrollRequest(BaseModel):
    delta: int = Field(ge=-800, le=800)
    model_config = {'extra': 'forbid'}

async def fill_visible(page, facts, portal):
    filled, skipped = [], []
    documents = {d['id']:d for d in document_catalogue()}
    for field in await page.evaluate(tn_portals.INSPECT):
        label = tn_portals.mapping_label(field)
        identity = ' '.join(str(field.get(k,'')) for k in ('label','name','id'))
        if tn_portals.SENSITIVE_CONTROL.search(identity) or field['type'] == 'password':
            skipped.append(label + ': verification or confirmation left untouched')
            continue
        key = HORT_IDS.get(field['id']) if portal == 'horticulture' else None
        key = key or tn_portals.resolve(field, {}, portal == 'agrisnet') or map_field(field, {})
        if field['type'] == 'file' and key and key.startswith('doc:') and key[4:] in documents:
            loc = page.locator(f'[data-tn-field="{field["index"]}"]')
            try:
                await loc.set_input_files(str(ASSETS/'documents'/documents[key[4:]]['filename']),timeout=1000)
                filled.append(label + ' (sample selected locally; not uploaded)')
            except Exception:
                skipped.append(label + ': sample file could not be selected')
            continue
        if key not in facts or str(facts[key]).strip() == '':
            skipped.append(label + ': no unambiguous saved value')
            continue
        loc = page.locator(f'[data-tn-field="{field["index"]}"]')
        try:
            if field['type'] == 'checkbox':
                if key != 'land_address_same':
                    skipped.append(label + ': confirmation left untouched')
                    continue
                await loc.set_checked(bool(facts[key]),timeout=1000)
            elif field['type'] == 'radio':
                if key not in {'gender','land_ownership'}:
                    skipped.append(label + ': unrecognised radio group')
                    continue
                if tn_portals.norm(facts[key]) not in tn_portals.parts(field['label']):
                    continue
                await loc.check(timeout=1000)
            elif field['tag'] == 'select':
                value = tn_portals.norm(facts[key])
                matches = [o for o in field['options'] if not o.get('disabled') and
                    o['value'] not in ('','-1','0') and
                    (value in tn_portals.parts(o['label']) or value == tn_portals.norm(o['value']))]
                if len(matches) != 1:
                    skipped.append(label + ': matching option unavailable; dependent options need a live request')
                    continue
                await loc.select_option(matches[0]['value'], timeout=1000)
            elif field['tag'] in {'input','textarea'}:
                await loc.fill(str(facts[key]), timeout=1000)
            else:
                skipped.append(label + ': unsupported custom control')
                continue
            filled.append(label)
        except Exception:
            skipped.append(label + ': portal rejected the placeholder or changed the control')
    return filled, skipped

class OfficialPreviews:
    def __init__(self, runner):
        self.runner = runner
        self.sessions = {}
        self.lock = asyncio.Lock()

    async def close(self, id):
        async with self.lock:
            session = self.sessions.pop(id, None)
            if session:
                task = session.get('task')
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await self.runner.close(id)

    async def prepare(self, id, portal):
        s = self.sessions[id]
        runner, spec = self.runner, SITES[portal]
        try:
            async with runner.open_lock:
                if len(runner.contexts) >= runner.config.max_browsers:
                    raise RuntimeError('Close the current application browser before opening a preview.')
                if hasattr(runner.store, 'claim_browser'):
                    runner.database_leases[id] = runner.store.claim_browser()
                if runner.playwright is None:
                    runner.playwright = await async_playwright().start()
                # Always headless: interaction is read-only through the app view.
                # A unique profile cannot reuse citizen login/session cookies.
                options = dict(headless=True, viewport={'width':1200,'height':850},
                               accept_downloads=False, service_workers='block')
                if os.getenv('SCHEMESAATHI_BROWSER_EXECUTABLE'):
                    options['executable_path'] = os.environ['SCHEMESAATHI_BROWSER_EXECUTABLE']
                directory = runner.store.directory / 'browser' / id
                try:
                    if os.name == 'nt' and 'executable_path' not in options:
                        context = await runner.playwright.chromium.launch_persistent_context(str(directory),channel='msedge',**options)
                    else:
                        context = await runner.playwright.chromium.launch_persistent_context(str(directory),**options)
                except Exception:
                    if os.name != 'nt': raise
                    context = await runner.playwright.chromium.launch_persistent_context(str(directory),**options)
                runner.contexts[id] = context
            context.set_default_timeout(3000)
            # No live socket or service worker may survive the network lock.
            async def websocket(ws):
                await ws.close()
            await context.route_web_socket('**/*', websocket)
            async def guard(route):
                request = route.request
                url = urlparse(request.url)
                allowed = (not s['network_locked'] and request.method in {'GET','HEAD'}
                           and url.scheme == 'https' and url.hostname in spec['allowed_hosts'])
                if allowed and runner.config.mode == 'cloud':
                    allowed = await public_destination(url.hostname)
                if allowed and not s['network_locked']:
                    await route.continue_()
                else:
                    await route.abort()
            await context.route('**/*', guard)
            page = context.pages[0] if context.pages else await context.new_page()
            s['page'] = page
            response = await page.goto(spec['portal_url'], wait_until='load', timeout=30000)
            if response is None or response.status >= 400:
                raise RuntimeError('The official page returned an error. Close and retry later.')
            if urlparse(page.url).hostname not in spec['allowed_hosts']:
                raise RuntimeError('The official page redirected outside the approved portal.')
            # All writes already blocked. Lock even GETs before any demo input.
            s['network_locked'] = True
            await context.set_offline(True)
            await page.evaluate("""() => {
              window.stop();
              document.addEventListener('submit', e=>{e.preventDefault();e.stopImmediatePropagation()},true);
              document.addEventListener('click', e=>{if(e.target.closest('button,a,input[type=submit]')){e.preventDefault();e.stopImmediatePropagation()}},true);
            }""")
            # Bundled persona only; never copy the user's citizen profile.
            s['filled'], s['skipped'] = await fill_visible(page, demo_facts(), portal)
            await page.evaluate("""() => {
              document.querySelectorAll('button,input[type=submit],input[type=file]').forEach(e=>e.disabled=true);
              const b=document.createElement('div');b.textContent='SCHEMASAATHI DEMO — FICTIONAL DATA — NETWORK DISCONNECTED — NOTHING SUBMITTED';
              b.style.cssText='position:fixed;top:0;left:0;right:0;padding:12px;background:#fff1c2;color:#321f00;font: bold 14px sans-serif;z-index:2147483647;text-align:center';
              document.body.appendChild(b);window.scrollTo(0,0);
            }""")
            s.update(status='PREVIEW_READY', message='Official public page loaded; network disconnected before filling. No application submitted.')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            s.update(status='PREVIEW_ERROR', message=str(exc) if isinstance(exc,RuntimeError) else
                     'Could not load this public form. The portal may be unavailable or require a live session. No demo data was submitted.')
            await runner.close(id)

    def get(self, id):
        if id not in self.sessions:
            raise HTTPException(404, 'Preview expired. Open a new preview.')
        return self.sessions[id]

def install_official_previews(app, runner, track):
    previews = OfficialPreviews(runner)
    app.state.official_previews = previews

    @app.get('/api/official-previews')
    async def active():
        return {'id':next(iter(previews.sessions), None)}

    @app.post('/api/official-previews')
    async def start(body:PreviewRequest):
        if body.portal not in SITES:
            raise HTTPException(422,'Choose one of the supported official portals.')
        async with previews.lock:
            if previews.sessions:
                raise HTTPException(409,'Close the existing preview first.')
            id = 'preview-' + uuid.uuid4().hex
            previews.sessions[id] = {'id':id, 'portal':body.portal, 'status':'LOADING',
                'host':urlparse(SITES[body.portal]['portal_url']).hostname,
                'message':'Loading the official public page without applicant data…',
                'filled':[], 'skipped':[], 'network_locked':False}
            previews.sessions[id]['task'] = track(previews.prepare(id, body.portal))
            return {'id':id}

    @app.get('/api/official-previews/{id}')
    async def state(id:str):
        s = previews.get(id)
        return {k:v for k,v in s.items() if k not in {'page','task'}}

    @app.get('/api/official-previews/{id}/frame')
    async def frame(id:str):
        s = previews.get(id)
        if s['status'] != 'PREVIEW_READY': raise HTTPException(409,'Preview is not ready.')
        return Response(await s['page'].screenshot(type='jpeg',quality=70),media_type='image/jpeg',
                        headers={'Cache-Control':'no-store, private'})

    @app.post('/api/official-previews/{id}/scroll')
    async def scroll(id:str, body:ScrollRequest):
        s = previews.get(id)
        if s['status'] != 'PREVIEW_READY': raise HTTPException(409,'Preview is not ready.')
        await s['page'].evaluate('(dy)=>window.scrollBy(0,dy)',body.delta)
        return {'ok':True}

    @app.delete('/api/official-previews/{id}')
    async def close(id:str):
        await previews.close(id)
        return {'ok':True}
