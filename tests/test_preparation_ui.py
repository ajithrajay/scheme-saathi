"""Verify that saved profile/documents immediately enable preparation in the UI."""
from playwright.sync_api import sync_playwright, expect

from test_browser_e2e import live


def test_profile_document_changes_enable_preparation_without_new_assessment(live, monkeypatch, tmp_path):
    client, app = live
    queued = []
    # This UI test verifies queueing only. The browser suite exercises actual form filling.
    monkeypatch.setattr(app.state.browser, 'start', queued.append)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(str(client.base_url) + '?access=browser-test')
        page.get_by_role('button', name='My profile', exact=True).click()
        page.get_by_label('Full name', exact=True).fill('Fictional QA Student')
        page.get_by_label('Gender', exact=True).select_option('female')
        page.get_by_label('School type', exact=True).select_option('government')
        page.get_by_label('Classes 6–12 continuously in the stated school type?', exact=True).select_option('true')
        page.get_by_label('Currently enrolled in higher education?', exact=True).select_option('true')
        page.get_by_role('button', name='Save confirmed details', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Profile saved.')
        page.get_by_role('button', name='Find schemes', exact=True).click()
        card = page.locator('.scheme-card').filter(has=page.get_by_role('heading', name='Pudhumai Penn', exact=True))
        expect(card.get_by_role('button', name='Prepare application', exact=True)).to_be_disabled()
        expect(card).to_contain_text('Missing documents:')

        page.get_by_role('button', name='Documents', exact=True).click()
        scheme = app.state.store.get('scheme', 'pudhumai-penn')
        for document in scheme['documents']:
            page.get_by_label('Document type', exact=True).select_option(document['id'])
            filename = document['id'] + '-FICTIONAL.pdf'
            page.locator('input[type=file]').set_input_files({
                'name': filename, 'mimeType': 'application/pdf',
                'buffer': b'%PDF-1.4\nFictional UI regression fixture only',
            })
            expect(page.get_by_text(filename, exact=True)).to_be_visible()
        page.get_by_role('button', name='Find schemes', exact=True).click()
        expect(card).to_contain_text('Preliminary match')
        expect(card).to_contain_text('Form preparation available')
        expect(card.get_by_role('button', name='Prepare application', exact=True)).to_be_enabled()
        card.screenshot(path=str(tmp_path / 'preparation-card.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        card.scroll_into_view_if_needed()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        card.screenshot(path=str(tmp_path / 'preparation-mobile.png'))
        page.set_viewport_size({'width': 1440, 'height': 1100})
        card.get_by_role('button', name='Prepare application', exact=True).click()
        expect(page.get_by_role('heading', name='Applications', exact=True)).to_be_visible()
        expect(page.locator('.application-card')).to_contain_text('Pudhumai Penn')
        expect(page.locator('.application-card')).to_contain_text('Before final submission')
        assert len(queued) == 1
        assert client.get('/api/assessments').json() == []
        assert errors == []
        browser.close()
