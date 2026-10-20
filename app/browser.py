"""Bounded browser automation: confirmed facts -> approved portal -> receipt.

No LLM can choose a URL, invent a field value, accept a declaration, or solve a
verification challenge. Real portals use conservative label matching; new layouts
surface exact unmapped labels for user mapping. Final writes require per-job consent.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from .models import FIELDS
from .rules import assess
from .store import now
from . import horticulture, tn_portals
from .tn_portal_data import BY_ADAPTER

ALIASES = {
    "full_name": ["full name", "applicant name", "name of applicant", "student name", "name of the student", "name", "முழுப் பெயர்", "மாணவர் பெயர்"],
    "state": ["state", "state of residence", "domicile state", "மாநிலம்"],
    "district": ["district", "district of residence", "மாவட்டம்"],
    "gender": ["gender", "sex", "பாலினம்"],
    "date_of_birth": ["date of birth", "dob", "பிறந்த தேதி"],
    "age": ["age", "age in years", "வயது"],
    "annual_household_income_inr": ["annual household income", "annual family income", "annual income of parents", "annual household income inr", "family annual income", "ஆண்டு குடும்ப வருமானம்"],
    "phone": ["mobile", "mobile number", "phone", "phone number", "contact number", "கைபேசி எண்"],
    "email": ["email", "email address", "e mail", "மின்னஞ்சல்"],
    "address": ["address", "residential address", "முகவரி"],
    "pincode": ["pincode", "pin code", "postal code", "அஞ்சல் குறியீடு"],
    "category": ["category", "community category", "community", "caste category", "சமூகப் பிரிவு"],
    "bank_account": ["bank account number", "account number", "வங்கி கணக்கு எண்"],
    "ifsc": ["ifsc", "ifsc code"],
    "aadhaar_number": ["aadhaar", "aadhaar number", "aadhar number", "ஆதார் எண்"],
    "institution_name": ["institution name", "school name", "college name", "பள்ளி பெயர்", "கல்லூரி பெயர்"],
    "umis_id": ["umis id", "umis number"],
    "otr_id": ["otr id", "otr number", "one time registration number"],
    "higher_education_enrolled": ["enrolled in higher education", "higher education enrolled"],
    "doc:study_certificate": ["study certificate", "school study certificate", "பள்ளிப் படிப்புச் சான்று"],
    "doc:admission_proof": ["admission proof", "admission certificate"],
    "doc:income_certificate": ["income certificate", "வருமானச் சான்று"],
    "doc:community_certificate": ["community certificate", "caste certificate"],
    "doc:bank_passbook": ["bank passbook", "passbook"],
    "doc:aadhaar_document": ["aadhaar document", "aadhaar card upload"],
    "doc:marksheet": ["class vii marksheet", "class 7 marksheet", "marksheet"],
    "doc:selection_certificate": ["selection certificate", "selection evidence"],
}

def normalize(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    text = text.lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"\(.*?\)|\*|:|₹", " ", text)
    text = re.sub(r"\b(required|optional|inr)\b", " ", text)
    return " ".join(text.split())

LOOKUP = {normalize(alias): key for key, aliases in ALIASES.items() for alias in aliases}
LOOKUP.update({normalize(k): k for k in FIELDS})

def map_field(field, mappings):
    label = normalize(field.get("label", ""))
    if any(word in label for word in ["password", "captcha", "otp", "one time password", "declare", "declaration", "i agree", "consent", "terms"]):
        return None
    if label in mappings:
        return mappings[label]
    # Never fall through from a third-party label to a generic `name` attribute.
    if re.search(r"\b(father|mother|guardian|nominee|spouse)\b", label):
        return None
    labels = [label] if label else [normalize(field.get("name", "")), normalize(field.get("id", ""))]
    candidates = {LOOKUP[x] for x in labels if x in LOOKUP}
    return next(iter(candidates)) if len(candidates) == 1 else None

INSPECT_JS = """() => Array.from(document.querySelectorAll('input,select,textarea,[role="combobox"]')).map((e,i)=> {
  if (!e.getClientRects().length || e.disabled || e.readOnly || ['hidden','submit','button','reset','image'].includes(e.type)) return null;
  const label = (Array.from(e.labels||[]).map(l=>l.innerText).join(' ') || e.getAttribute('aria-label') || (e.getAttribute('aria-labelledby')||'').split(' ').map(id=>document.getElementById(id)?.innerText||'').join(' ') || e.placeholder || '').trim();
  e.setAttribute('data-saathi-field', String(i));
  return {index:i, label, name:e.name||'', id:e.id||'', tag:e.tagName.toLowerCase(), type:e.type||e.getAttribute('role')||'', required:e.required||e.getAttribute('aria-required')==='true', value:e.type==='checkbox'||e.type==='radio'?e.checked:!!e.value, optionValue:e.type==='radio'?e.value:null, options:e.tagName==='SELECT'?Array.from(e.options).map(o=>({value:o.value,label:o.text})):[]};
}).filter(Boolean)"""


class PortalConnectionError(Exception):
    """A portal navigation failure, without URLs containing session/private data."""
    def __init__(self, host, code, http_status=None):
        self.host, self.code, self.http_status = host, code, http_status
        super().__init__(f"{host}: {code}")


def navigation_error_code(exc):
    if isinstance(exc, PlaywrightTimeoutError):
        return "TIMEOUT"
    text = str(exc)
    for token, code in [("ERR_NAME_NOT_RESOLVED", "DNS_ERROR"),
                        ("ERR_CONNECTION_TIMED_OUT", "TIMEOUT"),
                        ("ERR_CONNECTION_REFUSED", "CONNECTION_REFUSED"),
                        ("ERR_CONNECTION_RESET", "CONNECTION_RESET"),
                        ("ERR_INTERNET_DISCONNECTED", "OFFLINE"),
                        ("ERR_CERT_", "CERTIFICATE_ERROR"),
                        ("ERR_SSL_", "CERTIFICATE_ERROR")]:
        if token in text:
            return code
    return "NAVIGATION_FAILED"


def browser_error_page(url):
    return url.startswith(("chrome-error://", "edge-error://", "about:neterror"))

class BrowserRunner:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.playwright = None
        self.contexts = {}
        self.tasks = {}
        self.locks = {}
        self.verification_watchers = {}
        self.open_lock = asyncio.Lock()
        self.database_leases = {}
        self.demo_networks = {}

    def status(self, job_id, status, message, **extra):
        job = self.store.get("application", job_id)
        job.update(status=status, message=message, updated_at=now(), **extra)
        self.store.put("application", job_id, job)
        self.store.event(job_id, "browser", message, status=status)
        return job

    def start(self, job_id):
        if job_id in self.tasks and not self.tasks[job_id].done():
            return
        self.tasks[job_id] = asyncio.create_task(self.run(job_id))

    async def close(self, job_id):
        watcher = self.verification_watchers.pop(job_id, None)
        if watcher and watcher is not asyncio.current_task():
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        job = self.store.get("application", job_id)
        if job and job.get("auto_resume_waiting"):
            job["auto_resume_waiting"] = False
            self.store.put("application", job_id, job)
        context = self.contexts.pop(job_id, None)
        self.demo_networks.pop(job_id, None)
        if context:
            await context.close()
        lease = self.database_leases.pop(job_id, None)
        if lease:
            lease.close()
        if self.config.mode == "cloud":
            import shutil
            shutil.rmtree(self.store.directory / "browser" / job_id, ignore_errors=True)

    async def shutdown(self):
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for jid in list(self.contexts):
            await self.close(jid)
        if self.playwright:
            await self.playwright.stop()

    def scheme(self, job):
        return horticulture.SCHEME if job.get("adapter") == horticulture.ADAPTER else BY_ADAPTER.get(job.get("adapter")) or self.store.get("scheme", job["scheme_id"])

    def readiness(self, job):
        scheme = self.scheme(job)
        docs = self.store.all("document")
        owned = {d["document_type"]: d for d in docs if bool(d.get("is_demo")) == job["demo"]}
        profile = self.store.get("profile", "demo" if job["demo"] else "citizen", {"facts": {}})["facts"]
        result = assess(scheme, profile, owned)
        return scheme, profile, owned, result

    async def open(self, job):
        async with self.open_lock:
            try:
                return await self._open(job)
            except BaseException:
                if job["id"] not in self.contexts:
                    lease = self.database_leases.pop(job["id"], None)
                    if lease:
                        lease.close()
                raise

    async def _open(self, job):
        if not job.get("demo"):
            from .resume_demo import synthetic_facts
            facts = self.store.get("profile", "citizen", {"facts": {}})["facts"]
            if synthetic_facts(facts):
                raise ValueError("Fictional demo data cannot be sent to official websites. Use practice mode.")
        scheme = self.scheme(job)
        if job["id"] in self.contexts:
            context = self.contexts[job["id"]]
            if context.pages:
                page = context.pages[-1]
                if not browser_error_page(page.url) and job.get("status") != "PORTAL_UNAVAILABLE":
                    return context, page
                if job.get("submission_attempted"):
                    # An uncertain submission is never reset to a new form.
                    return context, page
            await self.close(job["id"])
        if len(self.contexts) >= self.config.max_browsers:
            raise RuntimeError("Close the current application browser before opening another")
        if hasattr(self.store, "claim_browser") and job["id"] not in self.database_leases:
            self.database_leases[job["id"]] = self.store.claim_browser()
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        directory = self.store.directory / "browser" / job["id"]
        options = {"headless": self.config.headless, "viewport": {"width": 1200, "height": 850}, "accept_downloads": False}
        if job.get('official_demo'):
            options['service_workers'] = 'block'
        if os.getenv("SCHEMESAATHI_BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["SCHEMESAATHI_BROWSER_EXECUTABLE"]
        try:
            if os.name == "nt" and not options.get("executable_path"):
                context = await self.playwright.chromium.launch_persistent_context(str(directory), channel="msedge", **options)
            else:
                context = await self.playwright.chromium.launch_persistent_context(str(directory), **options)
        except Exception:
            if os.name != "nt":
                raise
            # Prefer the user's updated Edge; bundled Chromium is the fallback.
            context = await self.playwright.chromium.launch_persistent_context(str(directory), **options)
        context.set_default_timeout(4000)
        self.contexts[job["id"]] = context
        allowed = set(scheme["allowed_hosts"])

        if job.get('official_demo'):
            from .demo_application import DemoNetwork
            self.demo_networks[job['id']] = DemoNetwork(self,job,scheme)
            async def block_socket(ws):
                await ws.close()
            await context.route_web_socket('**/*',block_socket)

        from .network_guard import public_destination

        async def guard(route):
            request = route.request
            if job.get('official_demo'):
                policy = self.demo_networks[job['id']]
                permitted = policy.allows(request)
                if permitted and self.config.mode == 'cloud':
                    permitted = await public_destination(urlparse(request.url).hostname)
                if not permitted:
                    policy.blocked()
                    return await route.abort()
                return await route.continue_()
            if request.method not in {"GET", "HEAD", "OPTIONS"} and hasattr(self.store, "claim_browser"):
                try:
                    self.database_leases[job["id"]].execute("SELECT 1")
                except Exception:
                    return await route.abort()
            parsed = urlparse(request.url)
            if parsed.scheme not in {"http", "https", "data", "blob"}:
                return await route.abort()
            if self.config.mode == "cloud" and not job["demo"]:
                if parsed.scheme not in {"https", "data", "blob"} or (parsed.scheme == "https" and not await public_destination(parsed.hostname)):
                    return await route.abort()
            if request.is_navigation_request() or request.method not in {"GET", "HEAD", "OPTIONS"}:
                if job["demo"]:
                    if parsed.netloc != urlparse(self.config.base_url).netloc or not parsed.path.startswith("/demo/"):
                        return await route.abort()
                elif parsed.scheme != "https" or parsed.hostname not in allowed:
                    return await route.abort()
            await route.continue_()
        await context.route("**/*", guard)
        page = context.pages[0] if context.pages else await context.new_page()
        target = self.config.base_url + scheme["portal_url"] + "?job=" + job["id"] if job["demo"] and not job.get('official_demo') else scheme["portal_url"]
        try:
            response = await page.goto(target, wait_until="domcontentloaded", timeout=30000)
            if response and response.status >= 400:
                raise PortalConnectionError(urlparse(target).hostname, "HTTP_ERROR", response.status)
            if browser_error_page(page.url):
                raise PortalConnectionError(urlparse(target).hostname, "NAVIGATION_FAILED")
        except Exception as exc:
            await self.snapshot(job["id"], page)
            # Discard a failed navigation so Resume really retries the entry URL.
            # Never leave an error-page context consuming one of the three slots.
            try:
                await self.close(job["id"])
            except Exception:
                pass
            if isinstance(exc, PortalConnectionError):
                raise
            raise PortalConnectionError(urlparse(target).hostname, navigation_error_code(exc)) from exc
        return context, page

    async def snapshot(self, job_id, page):
        try:
            png = await page.screenshot(full_page=False)
            self.store.put("screenshot", job_id, {"png": base64.b64encode(png).decode()})
        except Exception:
            pass

    async def pause(self, job, page, status, message, **extra):
        auto = not job.get('official_demo') and (job.get("adapter") in BY_ADAPTER or job.get("adapter") == horticulture.ADAPTER)
        verification = status in {"VERIFICATION_REQUIRED", "LOGIN_REQUIRED"}
        if not verification:
            await self.snapshot(job["id"], page)
        else:
            # Do not retain screenshots of authentication codes or credentials.
            self.store.delete("screenshot", job["id"])
        extra["auto_resume_waiting"] = bool(auto and verification)
        if auto and verification:
            message = message.replace("then Resume", "automation resumes after verification").replace("then click Resume here", "automation resumes after verification")
        self.status(job["id"], status, message, **extra)
        if auto and verification:
            existing = self.verification_watchers.get(job["id"])
            if not existing or existing.done():
                self.verification_watchers[job["id"]] = asyncio.create_task(self.watch_verification(job["id"], page))

    async def watch_verification(self, job_id, page):
        """Watch visible challenge state; never read or solve OTP/CAPTCHA values.

        Two clear observations avoid starting while the portal replaces its DOM.
        A closed browser, cancellation, change of status or 15 minutes stops it.
        """
        clear = 0
        auth_memory = {}
        try:
            for _ in range(450):
                await asyncio.sleep(2)
                async with self.locks.setdefault(job_id, asyncio.Lock()):
                    current = self.store.get("application", job_id)
                    if not current or current["status"] not in {"VERIFICATION_REQUIRED", "LOGIN_REQUIRED"} or page.is_closed():
                        return
                    if urlparse(page.url).hostname not in self.scheme(current)["allowed_hosts"]:
                        self.status(job_id, "UNSUPPORTED_PORTAL", "Verification left the approved portal.", auto_resume_waiting=False)
                        return
                    try:
                        if current.get("adapter") in BY_ADAPTER:
                            await tn_portals.continue_auth(self, current, page, auth_memory)
                        has_controls = await page.locator("input:visible,select:visible,textarea:visible,button:visible,a:visible").count()
                        pending = await self.challenge(page)
                        # Registration/login entry pages must actually finish authentication.
                        entry = current.get("adapter") in BY_ADAPTER and await page.locator("#registerform:visible,#userLogin:visible").count()
                        clear = clear + 1 if has_controls and not pending and not entry else 0
                    except Exception:
                        clear = 0
                    if clear >= 2:
                        active = self.tasks.get(job_id)
                        if active and not active.done():
                            continue
                        self.status(job_id, "QUEUED", "Verification completed. Continuing automatically.", auto_resume_waiting=False)
                        self.start(job_id)
                        return
            self.status(job_id, current["status"], "Automatic waiting timed out after 15 minutes. Complete verification and click Resume.", auto_resume_waiting=False)
        except asyncio.CancelledError:
            raise
        finally:
            current = self.store.get("application", job_id)
            if current and current.get("auto_resume_waiting"):
                current["auto_resume_waiting"] = False
                self.store.put("application", job_id, current)

    async def wait_for_page(self, page):
        # DOMContentLoaded precedes SPA login rendering on NSP. Wait for an
        # actual control or acknowledgement rather than calling a blank shell unsupported.
        try:
            await page.wait_for_function("""() => Array.from(document.querySelectorAll('input,select,textarea,button')).some(e=>e.getClientRects().length && e.type!=='hidden') || /application.*submitted successfully/i.test(document.body?.innerText||'')""", timeout=8000)
        except PlaywrightTimeoutError:
            pass

    async def challenge(self, page):
        for frame in page.frames:
            try:
                state = await frame.evaluate(r"""() => {
                  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
                  const inputs = Array.from(document.querySelectorAll('input')).filter(e=>visible(e)&&!e.disabled&&!e.readOnly);
                  const label = e => [e.name,e.id,e.placeholder,e.getAttribute('aria-label'),e.autocomplete,
                    ...Array.from(e.labels||[]).map(l=>l.innerText),
                    ...(e.getAttribute('aria-labelledby')||'').split(' ').map(id=>document.getElementById(id)?.innerText||'')].join(' ');
                  if (inputs.some(e=>/otp|one.?time|captcha|verification.?code|security.?code/i.test(label(e)))) return 'VERIFICATION_REQUIRED';
                  const buttons = Array.from(document.querySelectorAll('button,a,[role=button]')).filter(visible);
                  if (buttons.some(e=>/^(get|send|verify|resend)\s*(otp|one.time password)$/i.test(e.innerText.trim()))) return 'VERIFICATION_REQUIRED';
                  if (inputs.filter(e=>e.maxLength===1).length>=4 && /otp|one.time|verification code/i.test(document.body.innerText)) return 'VERIFICATION_REQUIRED';
                  if (Array.from(document.querySelectorAll('iframe[title*=captcha i],.g-recaptcha,.h-captcha,[data-sitekey],img[alt*=captcha i]')).some(visible)) return 'VERIFICATION_REQUIRED';
                  if (inputs.some(e=>e.type==='password')) return 'LOGIN_REQUIRED';
                  return null;
                }""")
                if state:
                    return state
            except Exception:
                continue
        return None

    async def receipt(self, page, demo):
        if demo:
            locator = page.locator("#ack-code")
            if await locator.count():
                value = (await locator.inner_text()).strip()
                if re.fullmatch(r"SS-DEMO-[A-F0-9]{12}", value):
                    return value
            return None
        text = await page.locator("body").inner_text(timeout=4000)
        if not re.search(r"application (?:has been )?submitted successfully|application successfully submitted|submission successful", text, re.I):
            return None
        match = re.search(r"(?:application|acknowledg(?:e)?ment)\s*(?:number|no\.?|id|reference)\s*[:#-]\s*([A-Z0-9][A-Z0-9/-]{5,49})", text, re.I)
        return match.group(1) if match else None

    async def fill_page(self, job, page, profile, documents):
        mappings = self.store.get("mapping", job["scheme_id"], {})
        unresolved, filled, count = [], [], 0
        for frame in page.frames:
            host = urlparse(frame.url).hostname
            if host not in self.store.get("scheme", job["scheme_id"])["allowed_hosts"]:
                continue
            fields = await frame.evaluate(INSPECT_JS)
            radios_done = set()
            for field in fields:
                count += 1
                label = field["label"] or field["name"] or field["id"] or f"Unlabelled field {field['index']}"
                field_key = map_field(field, mappings)
                locator = frame.locator(f'[data-saathi-field="{field["index"]}"]')
                if field["type"] == "radio":
                    group_name = field["name"]
                    if group_name in radios_done:
                        continue
                    radios_done.add(group_name)
                    group = [f for f in fields if f["type"] == "radio" and f["name"] == group_name]
                    if any(f["value"] for f in group):
                        continue
                    key = LOOKUP.get(normalize(group_name)) or field_key
                    if key and key in profile:
                        val = normalize(str(profile[key]))
                        candidate = next((f for f in group if normalize(f["optionValue"]) == val or normalize(f["label"]) == val), None)
                        if candidate:
                            await frame.locator(f'[data-saathi-field="{candidate["index"]}"]').check()
                            filled.append(key)
                            continue
                    unresolved.append({"label": label, "mapping_key": normalize(label), "reason": "Radio group needs a confirmed matching value", "field": key})
                    continue
                if field["value"] and (not field_key or field_key.startswith("doc:") or field_key not in profile):
                    # Preserve values the user or portal has already entered.
                    continue
                if not field_key:
                    unresolved.append({"label": label, "mapping_key": normalize(label), "reason": "Map this field or complete it in the browser", "type": field["type"]})
                    continue
                try:
                    if field_key.startswith("doc:"):
                        document = documents.get(field_key[4:])
                        if not document or field["type"] != "file":
                            raise ValueError("Required document unavailable or input is not a file upload")
                        blob = self.store.get_blob(document["blob"])
                        await locator.set_input_files({"name": document["filename"], "mimeType": document["mime_type"], "buffer": self.store.cipher.decrypt(blob)})
                    else:
                        value = profile.get(field_key)
                        if value is None:
                            raise ValueError("No confirmed value in profile")
                        if field["tag"] == "select":
                            option = next((o for o in field["options"] if normalize(o["value"]) == normalize(str(value)) or normalize(o["label"]) == normalize(str(value))), None)
                            if not option:
                                raise ValueError("No exact matching option")
                            await locator.select_option(value=option["value"])
                        elif field["type"] == "checkbox":
                            if type(value) is not bool:
                                raise ValueError("Checkbox requires an explicit yes/no answer")
                            await locator.set_checked(value)
                        elif field["type"] == "combobox":
                            raise ValueError("Custom dropdown requires manual selection")
                        else:
                            if isinstance(value, float) and value.is_integer():
                                value = int(value)
                            await locator.fill(str(value))
                    filled.append(field_key)
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, ValueError) else "Portal did not accept this field; complete it manually"
                    unresolved.append({"label": label, "mapping_key": normalize(label), "reason": reason, "field": field_key, "type": field["type"]})
        if filled:
            self.store.event(job["id"], "browser", f"Filled {len(filled)} fields.", fields=filled)
        return unresolved, count

    async def button(self, page, pattern):
        for frame in page.frames:
            if urlparse(frame.url).hostname not in {urlparse(page.url).hostname}:
                continue
            candidates = frame.get_by_role("button", name=re.compile(pattern, re.I))
            for i in range(await candidates.count()):
                item = candidates.nth(i)
                if await item.is_visible() and await item.is_enabled():
                    return item
        return None

    async def run(self, job_id):
        lock = self.locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            job = self.store.get("application", job_id)
            if job["status"] in {"SUBMITTED", "CANCELLED"}:
                return
            scheme = self.scheme(job)
            try:
                if job.get('official_demo'):
                    from .demo_application import run
                    await run(self,job)
                    return
                if job.get("adapter") in BY_ADAPTER:
                    await tn_portals.run(self, job)
                    return
                if job.get("adapter") == horticulture.ADAPTER:
                    await horticulture.run(self, job)
                    return
                scheme, profile, documents, evaluation = self.readiness(job)
                uncertain = job.get("submission_attempted", False)
                fingerprint = hashlib.sha256(json.dumps({"profile": profile, "documents": {k: d["sha256"] for k, d in documents.items()}}, sort_keys=True).encode()).hexdigest()
                if not uncertain and job.get("profile_fingerprint") and job["profile_fingerprint"] != fingerprint:
                    self.status(job_id, "PROFILE_CHANGED", "Confirmed facts or documents changed. Cancel this preparation and start again so every page uses the new values.")
                    return
                if not uncertain:
                    if not evaluation["can_prepare"]:
                        self.status(job_id, "NOT_READY", " ".join(evaluation["preparation_blockers"]), evaluation=evaluation, submission_blockers=evaluation["submission_blockers"])
                        return
                self.status(job_id, "RUNNING", "Opening the application browser to prepare your form.", unresolved=[], profile_fingerprint=fingerprint, submission_blockers=evaluation["submission_blockers"], preparation_only=not evaluation["can_submit"], error_code=None, http_status=None)
                context, page = await self.open(job)
                if uncertain:
                    receipt = await self.receipt(page, job["demo"])
                    if receipt:
                        await self.pause(job, page, "SUBMITTED", "Application acknowledgement verified.", receipt=receipt)
                    else:
                        await self.pause(job, page, "SUBMISSION_UNVERIFIED", "A submission was already attempted. Check the portal; automatic resubmission is disabled.")
                    return
                for step in range(12):
                    if self.store.get("application", job_id)["status"] == "CANCELLED":
                        return
                    current_host = urlparse(page.url).hostname
                    if current_host not in scheme["allowed_hosts"]:
                        await self.pause(job, page, "UNSUPPORTED_PORTAL", "The portal navigated outside its approved hosts.")
                        return
                    await self.wait_for_page(page)
                    challenge = await self.challenge(page)
                    if challenge:
                        await self.pause(job, page, challenge, "Complete login, OTP or CAPTCHA in the application browser, then click Resume here.")
                        return
                    # The next loop must never run on a receipt page and submit again.
                    if await self.receipt(page, job["demo"]):
                        await self.pause(job, page, "REVIEW_EXISTING_RECEIPT", "A receipt already exists on this page. Review it before starting a new application.")
                        return
                    unresolved, count = await self.fill_page(job, page, profile, documents)
                    if unresolved:
                        await self.pause(job, page, "NEEDS_INFORMATION", "Some fields need your input. Update the profile, map labels, or complete them in the browser, then resume.", unresolved=unresolved)
                        return
                    next_button = await self.button(page, r"^(next|continue|save\s*(?:&|and)\s*(?:next|continue)|அடுத்து|தொடரவும்)\s*[›>]?$" )
                    if next_button:
                        signature = hashlib.sha256((page.url + str(await page.locator("body").inner_text())).encode()).hexdigest()
                        await next_button.click()
                        await page.wait_for_timeout(700)
                        if hashlib.sha256((page.url + str(await page.locator("body").inner_text())).encode()).hexdigest() == signature:
                            await self.pause(job, page, "NEEDS_INFORMATION", "The portal did not advance. Check validation messages in the browser.")
                            return
                        continue
                    final_button = await self.button(page, r"^(submit(?: application)?|final submit|apply|apply now|சமர்ப்பிக்கவும்)$")
                    if final_button:
                        job = self.store.get("application", job_id)
                        # Re-evaluate current source version and profile immediately before final write.
                        _, latest_profile, latest_docs, latest_eval = self.readiness(job)
                        if latest_profile != profile or {k: d["sha256"] for k, d in latest_docs.items()} != {k: d["sha256"] for k, d in documents.items()}:
                            await self.pause(job, page, "PROFILE_CHANGED", "Profile or documents changed during filling. Cancel preparation and start again.")
                            return
                        if not latest_eval["can_submit"]:
                            await self.pause(job, page, "SUBMISSION_BLOCKED", "Form preparation reached the final step. " + " ".join(latest_eval["submission_blockers"]), submission_blockers=latest_eval["submission_blockers"], preparation_only=True)
                            return
                        if not job.get("auto_submit"):
                            await self.pause(job, page, "READY_FOR_SUBMISSION", "Fields are filled. Review the browser and choose Submit application when ready.", submission_blockers=[], preparation_only=False)
                            return
                        # Persist submission intent BEFORE clicking. A crash never retries this write.
                        self.status(job_id, "SUBMITTING", "Submitting once and checking for an acknowledgement.", submission_attempted=True, submitted_rule_version=latest_eval["rule_version"])
                        await final_button.click()
                        receipt = None
                        for _ in range(10):
                            await page.wait_for_timeout(500)
                            receipt = await self.receipt(page, job["demo"])
                            if receipt:
                                break
                        if receipt:
                            await self.pause(job, page, "SUBMITTED", "Application acknowledgement verified.", receipt=receipt)
                        else:
                            await self.pause(job, page, "SUBMISSION_UNVERIFIED", "Submit was clicked, but no verifiable receipt was found. Check the portal; automatic resubmission is disabled.")
                        return
                    await self.pause(job, page, "UNSUPPORTED_FORM", "No supported application step is visible. Open the correct application page in this browser, then resume.")
                    return
                await self.pause(job, page, "NEEDS_INFORMATION", "Reached the 12-step limit. Check the browser and resume if needed.")
            except asyncio.CancelledError:
                raise
            except PortalConnectionError as exc:
                latest = self.store.get("application", job_id)
                if latest.get("submission_attempted"):
                    self.status(job_id, "SUBMISSION_UNVERIFIED", "The portal could not be reached to verify your previous submission. Check it manually; the app will not submit again.", error_code=exc.code, http_status=exc.http_status)
                else:
                    reason = f"HTTP {exc.http_status}" if exc.http_status else exc.code.replace("_", " ").lower()
                    self.status(job_id, "PORTAL_UNAVAILABLE", f"The official portal {exc.host} could not be opened ({reason}). No application was submitted by this job. Check whether the site opens normally, then select Retry portal. Reinstalling Chromium will not fix a portal connection problem.", error_code=exc.code, http_status=exc.http_status, portal_url=scheme["portal_url"])
            except Exception as exc:
                latest = self.store.get("application", job_id)
                status = "SUBMISSION_UNVERIFIED" if latest.get("submission_attempted") else "BROWSER_ERROR"
                self.status(job_id, status, "Browser stopped (" + type(exc).__name__ + "). Check the application browser and resume. If the browser did not open, check the browser installation and host memory; see DEPLOY.md. A previous submission will not be retried.")
