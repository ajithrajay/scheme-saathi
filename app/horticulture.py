"""Dedicated TN horticulture kit request adapter.

This submits a citizen's chosen request, not an eligibility determination.
Selectors were observed on the live public form on 2026-10-18. Official receipt
format is not certified; absent an explicit success + reference, do not retry.
"""
import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse
from pydantic import BaseModel
from typing import Literal
from fastapi import HTTPException
from .store import now

PORTAL = 'https://tnhorticulture.tn.gov.in/kit/'
TRACK = 'https://tnhorticulture.tn.gov.in/kit/kit-appl-status-track'
ADAPTER = 'tn-horticulture-kit-v1'
SCHEME = {'id': 'tn-horticulture-kit', 'name': 'TN Horticulture kit request',
          'portal_url': PORTAL, 'allowed_hosts': ['tnhorticulture.tn.gov.in']}
PHOTO = {'id': 'beneficiary_photo', 'name': 'Beneficiary photograph (JPG/PNG)', 'name_ta': 'பயனாளியின் புகைப்படம் (JPG/PNG)'}
KITS = {'vegetable': ('Vegetable Seed Kit', 'vegKitReqQty'),
        'fruit': ('Fruit Plant Kit — three plants', 'fruitKitReqQty'),
        'pulses': ('Pulses Kit', 'pulsesKitReqQty')}
REQUIRED = ['full_name', 'aadhaar_number', 'relation_name', 'gender', 'kit_category',
            'phone', 'area_type', 'district', 'block', 'address', 'pincode']

class KitRequest(BaseModel):
    kit: Literal['vegetable', 'fruit', 'pulses']
    disclosure_confirmed: bool = False
    auto_submit: bool = False


def inputs(store):
    facts = store.get('profile', 'citizen', {'facts': {}})['facts']
    docs = {d['document_type']: d for d in store.all('document') if not d.get('is_demo')}
    return facts, docs


def readiness(facts, docs):
    required = REQUIRED + (['village'] if facts.get('area_type') == 'rural' else [])
    missing = [k for k in required if not str(facts.get(k, '')).strip()]
    errors = []
    for key, pattern, message in [
        ('aadhaar_number', r'[2-9][0-9]{11}', 'Aadhaar must contain 12 digits and start with 2–9.'),
        ('phone', r'[6-9][0-9]{9}', 'Mobile number must contain 10 digits.'),
        ('pincode', r'[1-9][0-9]{5}', 'PIN code must contain 6 digits.')]:
        if facts.get(key) and not re.fullmatch(pattern, str(facts[key])):
            errors.append(message)
    if facts.get('state', '').strip().casefold() not in ('tamil nadu', 'tamilnadu', 'தமிழ்நாடு'):
        errors.append('Set your actual state of residence to Tamil Nadu to use this TN request form.')
    photo = docs.get('beneficiary_photo')
    if not photo:
        errors.append('Upload a beneficiary photograph in Documents.')
    elif photo['mime_type'] not in ('image/jpeg', 'image/png'):
        errors.append('The photograph must be JPG or PNG, not PDF.')
    return {'missing_fields': missing, 'errors': errors, 'can_prepare': not missing and not errors,
            'portal_url': PORTAL, 'tracking_url': TRACK, 'photo_name': photo['filename'] if photo else None,
            'note': 'Form completeness only. Department eligibility, stock, approval and collection are not verified.'}


def fingerprint(facts, docs, kit):
    photo = docs.get('beneficiary_photo', {})
    return hashlib.sha256(json.dumps({'facts': facts, 'photo': photo.get('sha256'), 'kit': kit}, sort_keys=True).encode()).hexdigest()


def install_routes(app, store, runner):
    @app.get('/api/horticulture/readiness')
    async def kit_readiness(demo: bool = False):
        if demo:
            from .resume_demo import demo_inputs
            result=readiness(*demo_inputs(store))
            result.update(can_prepare=not result['missing_fields'],errors=[],note="Preloaded demo: browser preparation only. Submission is disabled.")
            return result
        return readiness(*inputs(store))

    @app.post('/api/horticulture/applications')
    async def kit_start(body: KitRequest, demo: bool = False):
        if demo:
            from .demo_application import start
            return start(runner,{**SCHEME,'name':'TN Horticulture — '+KITS[body.kit][0]},body.auto_submit,body.kit)
        if not body.disclosure_confirmed:
            raise HTTPException(422, 'Confirm sharing the listed details and photograph with the TN Horticulture portal.')
        # One local request across all three mutually exclusive kits. Never start
        # a second application after an uncertain submission, even after restart.
        existing = next((j for j in store.all('application') if j.get('adapter') == ADAPTER and not j.get('demo') and j['status'] != 'CANCELLED'), None)
        if existing:
            return existing
        result = readiness(*inputs(store))
        if not result['can_prepare']:
            raise HTTPException(422, 'Complete My profile: ' + ', '.join(result['missing_fields']) + '. ' + ' '.join(result['errors']))
        jid = uuid.uuid4().hex
        job = {'id': jid, 'adapter': ADAPTER, 'scheme_id': SCHEME['id'],
               'scheme_name': 'TN Horticulture — ' + KITS[body.kit][0],
               'kit': body.kit, 'demo': False, 'status': 'QUEUED',
               'auto_submit': body.auto_submit, 'submission_attempted': False,
               'disclosure_confirmed_at': now(), 'consented_at': now() if body.auto_submit else None,
               'created_at': now(), 'updated_at': now(), 'message': 'Preparing your chosen kit request.',
               'unresolved': [], 'submission_blockers': [], 'tracking_url': TRACK}
        store.put('application', jid, job)
        runner.start(jid)
        return job


class FormAttention(Exception):
    pass


def norm(value):
    return ' '.join(value.casefold().split())


async def select_text(page, selector, value):
    """Match bilingual labels exactly, waiting for the dependent options."""
    locator = page.locator(selector)
    for _ in range(32):
        if await locator.is_visible() and await locator.is_enabled():
            options = await locator.locator('option').evaluate_all('(es) => es.map(e=>({value:e.value,label:e.textContent}))')
            matches = [o for o in options if o['value'] not in ('', '-1', '0') and
                       norm(str(value)) in [norm(o['label']), *[norm(p) for p in o['label'].split('/')]]]
            if len(matches) == 1:
                # Avoid firing onchange again when resuming a partly filled form.
                if await locator.input_value() != matches[0]['value']:
                    await locator.select_option(matches[0]['value'])
                return
        await page.wait_for_timeout(250)
    raise FormAttention(f'No available exact option for {selector[1:]}. Check district / block / village spelling and availability in the official browser.')


async def assert_form(page):
    ids = ['kitRegForm', 'aadhaar_no', 'benificiary_name', 'relation_name', 'gender',
           'social_status', 'phoneNumber', 'checkLocality', 'dist_id', 'block_id',
           'village_id', 'fruitKitReqQty', 'vegKitReqQty', 'pulsesKitReqQty',
           'terraceKitReqQty', 'type2FruitKitReqQty', 'address', 'pincode',
           'beneficiary_image', 'btnSubmit']
    if not all([await page.locator('#' + x).count() == 1 for x in ids]):
        raise FormAttention('The official form layout changed. No submission was made; the kit adapter needs an update.')
    # Unknown visible controls, especially new declarations, must not be skipped.
    unknown = await page.locator('#kitRegForm').evaluate('''form => Array.from(form.querySelectorAll('input,textarea,select')).some(e => {
      if (!e.getClientRects().length || e.disabled || e.type === 'hidden') return false;
      return !['aadhaar_no','benificiary_name','relation_name','gender','social_status','phoneNumber','checkLocality','dist_id','block_id','village_id','fruitKitReqQty','vegKitReqQty','pulsesKitReqQty','terraceKitReqQty','type2FruitKitReqQty','address','pincode','beneficiary_image'].includes(e.id)
        && !(e.type === 'checkbox' && ['fruitKitReqQty','vegKitReqQty','pulsesKitReqQty'].includes(e.dataset.selectId));
    })''')
    if unknown:
        raise FormAttention('The portal has an additional control or declaration. Review it manually; automatic submission is paused.')


async def fill(runner, job, page, facts, docs):
    await assert_form(page)
    for selector, key in [('aadhaar_no', 'aadhaar_number'), ('benificiary_name', 'full_name'),
                          ('relation_name', 'relation_name'), ('phoneNumber', 'phone'),
                          ('address', 'address'), ('pincode', 'pincode')]:
        await page.locator('#' + selector).fill(str(facts[key]))
    for selector, key in [('gender','gender'), ('social_status','kit_category'),
                          ('checkLocality','area_type'), ('dist_id','district'), ('block_id','block')]:
        await select_text(page, '#' + selector, facts[key])
    if facts['area_type'] == 'rural':
        await select_text(page, '#village_id', facts['village'])
    selected = KITS[job['kit']][1]
    checkbox = page.locator(f'input[type="checkbox"][data-select-id="{selected}"]')
    if await checkbox.count() != 1 or not await checkbox.is_enabled():
        raise FormAttention('This kit is unavailable for the selected location on the portal. Choose another available kit after cancelling this preparation; none was submitted.')
    await checkbox.check()
    await page.locator('#' + selected).select_option('1')
    # Do not let a manual edit silently add paid kits or a second request.
    for other in ['fruitKitReqQty','vegKitReqQty','pulsesKitReqQty','terraceKitReqQty','type2FruitKitReqQty']:
        if other != selected and await page.locator('#' + other).input_value() not in ('-1', '', '0'):
            raise FormAttention('Another kit quantity is selected. Clear extra kit quantities in the browser and Resume.')
    photo = docs['beneficiary_photo']
    blob = runner.store.get_blob(photo['blob'])
    await page.locator('#beneficiary_image').set_input_files({'name': photo['filename'], 'mimeType': photo['mime_type'], 'buffer': runner.store.cipher.decrypt(blob)})
    await assert_form(page)
    runner.store.event(job['id'], 'browser', 'Kit fields, location, one kit and photograph prepared. No eligibility decision was made.')


async def run(runner, job):
    jid = job['id']
    facts, docs = inputs(runner.store)
    token = fingerprint(facts, docs, job['kit'])
    uncertain = job.get('submission_attempted', False)
    if not uncertain:
        if job.get('profile_fingerprint') and job['profile_fingerprint'] != token:
            runner.status(jid, 'PROFILE_CHANGED', 'Profile or photograph changed. Cancel preparation and start again to use the new data.')
            return
        check = readiness(facts, docs)
        if not check['can_prepare']:
            runner.status(jid, 'NOT_READY', 'Complete profile fields: ' + ', '.join(check['missing_fields']) + '. ' + ' '.join(check['errors']))
            return
    runner.status(jid, 'RUNNING', 'Opening the official horticulture request form.', profile_fingerprint=token, error_code=None, http_status=None)
    _, page = await runner.open(job)
    await runner.wait_for_page(page)
    if urlparse(page.url).hostname not in SCHEME['allowed_hosts']:
        await runner.pause(job, page, 'UNSUPPORTED_PORTAL', 'The portal left the approved horticulture host.')
        return
    if uncertain:
        receipt = await runner.receipt(page, False)
        await runner.pause(job, page, 'SUBMITTED' if receipt else 'SUBMISSION_UNVERIFIED',
                           'Application acknowledgement verified.' if receipt else 'A submit attempt already exists. Check the official tracking page; automatic resubmission is disabled.', **({'receipt': receipt} if receipt else {}))
        return
    challenge = await runner.challenge(page)
    if challenge:
        await runner.pause(job, page, challenge, 'Complete the official verification in the application browser, then Resume.')
        return
    if await runner.receipt(page, False):
        await runner.pause(job, page, 'REVIEW_EXISTING_RECEIPT', 'An existing receipt is visible. No new request was submitted.')
        return
    try:
        await fill(runner, job, page, facts, docs)
    except FormAttention as exc:
        await runner.pause(job, page, 'NEEDS_INFORMATION', str(exc))
        return
    if fingerprint(*inputs(runner.store), job['kit']) != token:
        await runner.pause(job, page, 'PROFILE_CHANGED', 'Profile changed during filling. Cancel preparation and start again.')
        return
    current = runner.store.get('application', jid)
    if not current.get('auto_submit'):
        await runner.pause(job, page, 'READY_FOR_SUBMISSION', 'Your one-kit request is filled. Review the official form, then choose Submit application. The department decides eligibility, availability and approval; collect the kit only after approval.')
        return
    await assert_form(page)
    if not await page.locator('#btnSubmit').is_enabled():
        await runner.pause(job, page, 'NEEDS_INFORMATION', 'The portal has disabled Submit. Check its validation messages.')
        return
    runner.status(jid, 'SUBMITTING', 'Submitting the chosen request once; checking for an official reference.', submission_attempted=True)
    await page.locator('#btnSubmit').click()
    receipt = None
    for _ in range(20):
        await page.wait_for_timeout(500)
        receipt = await runner.receipt(page, False)
        if receipt:
            break
    if receipt:
        await runner.pause(job, page, 'SUBMITTED', 'Application acknowledgement verified. Approval and collection remain with the department.', receipt=receipt)
    else:
        await runner.pause(job, page, 'SUBMISSION_UNVERIFIED', 'Submit was clicked, but a reference could not be verified. Read the portal response and use its Track Application Status page. No automatic retry will be made.')
