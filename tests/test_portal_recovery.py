"""Portal failures must not be confused with browser installation failures."""
import asyncio
import json
from types import SimpleNamespace

from fastapi.responses import HTMLResponse
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright, expect

from app import demo
from app.browser import navigation_error_code
from app.portal_check import STRUCTURE_JS, public_read_guard
from test_browser_e2e import live, start, wait_job


def test_timeout_then_resume_really_reopens_entry(live, monkeypatch):
    client, app = live
    original = Page.goto
    attempts = []

    async def transient_failure(page, url, *args, **kwargs):
        if '/demo/portal' in url:
            attempts.append(url)
            if len(attempts) == 1:
                raise PlaywrightTimeoutError('Navigation timed out at https://example.invalid/?secret=PRIVATE')
        return await original(page, url, *args, **kwargs)

    monkeypatch.setattr(Page, 'goto', transient_failure)
    job_id = start(client, False)
    failure = wait_job(client, job_id, {'PORTAL_UNAVAILABLE'})
    assert failure['error_code'] == 'TIMEOUT'
    assert not failure['submission_attempted']
    assert 'PRIVATE' not in json.dumps(client.get('/api/events/' + job_id).json())
    assert 'Reinstalling Chromium will not fix' in failure['message']
    assert job_id not in app.state.browser.contexts
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(str(client.base_url) + '?access=browser-test')
        # Application is in the isolated practice workspace.
        page.get_by_role('button', name='Try the full demo', exact=True).click()
        page.get_by_role('button', name='Applications', exact=True).click()
        card = page.locator('.application-card')
        expect(card).to_contain_text('Official site unreachable')
        card.get_by_role('button', name='Retry portal', exact=True).click()
        expect(card).to_contain_text('Ready to submit', timeout=30000)
        browser.close()
    recovered = wait_job(client, job_id, {'READY_FOR_SUBMISSION'})
    assert len(attempts) == 2
    assert not recovered['submission_attempted']
    assert not app.state.store.all('demo_receipt')


def test_server_error_is_reported_and_retry_recovers(live):
    client, app = live
    hits = []

    @app.get('/demo/temporarily-unavailable', response_class=HTMLResponse)
    async def temporary(job: str):
        hits.append(job)
        if len(hits) == 1:
            return HTMLResponse('<h1>Service unavailable</h1>', status_code=503)
        return HTMLResponse(demo.profile_form(job))

    scheme = app.state.store.get('scheme', 'demo-education-grant')
    scheme['portal_url'] = '/demo/temporarily-unavailable'
    app.state.store.put('scheme', scheme['id'], scheme)
    job_id = start(client)
    failed = wait_job(client, job_id, {'PORTAL_UNAVAILABLE'})
    assert failed['http_status'] == 503
    assert len(hits) == 1  # No retry loop against a failing official service.
    assert job_id not in app.state.browser.contexts
    client.post('/api/applications/' + job_id + '/resume', json={})
    assert wait_job(client, job_id, {'SUBMITTED'})['receipt']
    assert len(hits) == 2
    assert len(app.state.store.all('demo_receipt')) == 1


def test_form_structure_does_not_export_values_or_option_text():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''
          <label>Full name <input id="name" name="name" value="FICTIONAL_PRIVATE_PERSON"></label>
          <label>Email <input id="mail" value="SECRET_EMAIL@example.invalid"></label>
          <label>Password <input type="password" value="SECRET_PASSWORD"></label>
          <label>Comments <textarea>SECRET_NOTES</textarea></label>
          <label>District <select><option value="SECRET_VALUE">SECRET_OPTION</option></select></label>
          <input type="hidden" name="token" value="SECRET_TOKEN">
          <label>Mobile 9876543210 <input id="phone_9876543210" value="9876543210"></label>
          <button>Register</button><button>SECRET_NON_ACTION</button>
        ''')
        result = page.evaluate(STRUCTURE_JS)
        encoded = json.dumps(result)
        for secret in ['FICTIONAL_PRIVATE_PERSON', 'SECRET_', '9876543210']:
            assert secret not in encoded
        assert {x['label'] for x in result['fields']} >= {'Full name', 'Email', 'Password', 'Comments', 'District'}
        assert result['actions'] == ['Register']
        assert any(x['option_count'] == 1 for x in result['fields'])
        browser.close()


def test_navigation_error_code_does_not_return_private_exception_text():
    assert navigation_error_code(Exception('net::ERR_NAME_NOT_RESOLVED secret')) == 'DNS_ERROR'
    assert navigation_error_code(Exception('net::ERR_CERT_AUTHORITY_INVALID secret')) == 'CERTIFICATE_ERROR'
    assert navigation_error_code(Exception('private unknown failure')) == 'NAVIGATION_FAILED'


def test_portal_check_cannot_submit_or_navigate_outside_fixed_services():
    async def verify(method, url, expected):
        calls = []
        async def abort(): calls.append('abort')
        async def proceed(): calls.append('continue')
        route = SimpleNamespace(
            request=SimpleNamespace(method=method, url=url, is_navigation_request=lambda: True),
            abort=abort, continue_=proceed,
        )
        await public_read_guard(route)
        assert calls == [expected]
    async def run():
        await verify('POST', 'https://tnuwwb.tn.gov.in/applications/register', 'abort')
        await verify('GET', 'https://unrelated.example/form', 'abort')
        await verify('GET', 'http://tnuwwb.tn.gov.in/applications/register', 'abort')
        await verify('GET', 'https://tnuwwb.tn.gov.in/applications/register', 'continue')
    asyncio.run(run())
