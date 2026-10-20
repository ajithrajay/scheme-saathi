"""Read-only public-form diagnostics on the user's own Windows connection.

No profile/database access, typing, submission, authentication or screenshots.
Only field structure is exported; input values, option text/values, page text,
cookies, request headers and actual navigation URLs are never collected.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from .browser import browser_error_page, navigation_error_code
from .config import ROOT

PORTALS = (
    ("e-Sevai citizen registration", "https://www.tnesevai.tn.gov.in/Citizen/Registration.aspx"),
    ("TN Unorganised Workers Welfare Board registration", "https://tnuwwb.tn.gov.in/applications/register"),
    ("TN MSME entrepreneur registration", "https://www.msmeonline.tn.gov.in/registration.php"),
    ("UMIS student login", "https://umis.tn.gov.in/auth/login"),
)

STRUCTURE_JS = r"""() => {
  const clean = s => (s || '').replace(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/gi, '[email]')
    .replace(/(?:\+?\d[\s().-]*){6,}/g, '[number]')
    .replace(/https?:\/\/\S+/gi, '[url]').replace(/\s+/g, ' ').trim().slice(0, 180);
  const labelText = e => {
    const labels = [...(e.labels || [])].map(label => {
      const copy = label.cloneNode(true);
      copy.querySelectorAll('input,textarea,select,option,script,style').forEach(n => n.remove());
      return copy.textContent || '';
    }).join(' ');
    return clean(labels || e.getAttribute('aria-label') || '');
  };
  return {
    fields: [...document.querySelectorAll('input,select,textarea,[role="combobox"]')]
      .filter(e => e.getClientRects().length && !['hidden','submit','button','reset','image'].includes(e.type))
      .slice(0, 120).map(e => ({
        tag: e.tagName.toLowerCase(), type: clean(e.type || e.getAttribute('role') || ''),
        id: clean(e.id), name: clean(e.getAttribute('name')), label: labelText(e),
        required: !!e.required || e.getAttribute('aria-required') === 'true',
        disabled: !!e.disabled, readonly: !!e.readOnly,
        option_count: e.tagName === 'SELECT' ? e.options.length : null
      })),
    actions: [...document.querySelectorAll('button,input[type=submit],input[type=button],a,[role=button]')]
      .filter(e => e.getClientRects().length).map(e => clean(e.tagName === 'INPUT' ? e.getAttribute('value') : e.textContent))
      .filter(s => /^(register|sign up|sign in|login|log in|next|continue|submit|apply|save|verify|send otp|get otp|resend otp|refresh|reset|cancel)(\s|$)/i.test(s))
      .slice(0, 40)
  };
}"""


def allowed_host(host):
    return any(host == urlparse(url).hostname for _, url in PORTALS) or host in {
        "tnesevai.tn.gov.in", "msmeonline.tn.gov.in", "www.tnuwwb.tn.gov.in"
    }


async def public_read_guard(route):
    request = route.request
    parsed = urlparse(request.url)
    # Diagnostic visits can never create an account or submit a form.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        return await route.abort()
    if request.is_navigation_request() and (parsed.scheme != "https" or not allowed_host(parsed.hostname or "")):
        return await route.abort()
    await route.continue_()


async def inspect_portal(context, label, entry_url, timeout=15000):
    item = {"service": label, "entry_url": entry_url, "status": "UNREACHABLE"}
    page = await context.new_page()
    try:
        response = await page.goto(entry_url, wait_until="domcontentloaded", timeout=timeout)
        status = response.status if response else None
        item["http_status"] = status
        host = urlparse(page.url).hostname or ""
        if not allowed_host(host):
            item.update(status="UNEXPECTED_DESTINATION", error_code="HOST_NOT_IN_SERVICE_LIST")
        elif browser_error_page(page.url):
            item["error_code"] = "NAVIGATION_FAILED"
        elif status and status >= 400:
            item["error_code"] = "HTTP_ERROR"
        else:
            # Wait for a dynamic public form if one is still rendering.
            try:
                await page.locator('input:visible, select:visible, textarea:visible').first.wait_for(timeout=3000)
            except Exception:
                pass
            structure = await page.evaluate(STRUCTURE_JS)
            item.update(status="PUBLIC_FORM_VISIBLE" if structure["fields"] else "PAGE_REACHED_NO_FORM", structure=structure)
    except Exception as exc:
        item["error_code"] = navigation_error_code(exc)
    finally:
        await page.close()
    return item


async def check(output):
    report = {
        "format": "schemesaathi-public-form-structure-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Public pages only; no input values, passwords, OTPs, cookies, screenshots or private page content collected.",
        "portals": [],
    }
    async with async_playwright() as playwright:
        options = {"headless": False}
        if os.getenv("SCHEMESAATHI_BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["SCHEMESAATHI_BROWSER_EXECUTABLE"]
        try:
            if os.name == "nt" and not options.get("executable_path"):
                browser = await playwright.chromium.launch(channel="msedge", **options)
            else:
                browser = await playwright.chromium.launch(**options)
        except Exception:
            if os.name != "nt" or options.get("executable_path"):
                raise
            browser = await playwright.chromium.launch(**options)
        context = await browser.new_context(accept_downloads=False)

        await context.route("**/*", public_read_guard)
        try:
            for label, url in PORTALS:
                print(f"Checking {label} ...", flush=True)
                item = await inspect_portal(context, label, url)
                report["portals"].append(item)
                print("  " + item["status"] + (" - " + item["error_code"] if item.get("error_code") else ""), flush=True)
                # A partial report survives an interruption or failed later portal.
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            await browser.close()
    print(f"\nSaved: {output.name}")
    print("Attach this JSON file in the chat. It contains public field structure only.")
    print("This check did not apply for a benefit or create any account.")


def main():
    print("SchemeSaathi - check official TN public forms")
    print("This opens a separate browser. Do not enter personal details or log in.")
    print("Four fixed government pages are visited once; this may take about a minute.\n")
    try:
        asyncio.run(check(ROOT / "TN_PORTAL_CHECK.json"))
    except KeyboardInterrupt:
        print("\nStopped. Any completed checks remain in TN_PORTAL_CHECK.json.")
        return 1
    except Exception as exc:
        print(f"Browser could not complete the check ({type(exc).__name__}).")
        print("Run python -m playwright install chromium if the browser is unavailable.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
