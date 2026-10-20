import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Config, ROOT
from .store import Store, now
from .models import FIELDS, ProfileUpdate, Intake, SettingsUpdate, AssessRequest, ApplicationRequest, ReviewRequest, Rule
from .rules import validate_rule, assess
from .sources import seed, refresh_sources, retrieve, graph
from .llm import Groq, ModelUnavailable
from .agents import Agents
from .browser import BrowserRunner, normalize
from . import demo, __version__, horticulture, tn_portals
from .tn_portal_data import BY_ADAPTER, DOCUMENTS

from .resume_demo import seed_demo, demo_facts, document_catalogue

DEMO_PROFILE = demo_facts()

def create_app(config=None):
    config = config or Config()
    config.validate()
    if config.database_url:
        from .postgres_store import PostgresStore
        store = PostgresStore(config.data_dir, config.database_url, config.encryption_key)
    else:
        store = Store(config.data_dir, config.encryption_key)
    seed(store)
    if config.demo_default:
        seed_demo(store)
    llm = Groq(store)
    agents = Agents(store, llm)
    browser = BrowserRunner(store, config)
    tasks = set()
    source_lock = asyncio.Lock()

    def track(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    async def refresh():
        async with source_lock:
            store.put("monitor", "status", {"running": True, "started_at": now()})
            try:
                results = await refresh_sources(store, llm)
                store.put("monitor", "status", {"running": False, "completed_at": now(), "results": results})
            except Exception as exc:
                store.put("monitor", "status", {"running": False, "error": type(exc).__name__})

    async def monitor():
        # Local daily polling while this program is running; first check after 24h.
        elapsed = 0
        while True:
            await asyncio.sleep(60)
            elapsed += 60
            if elapsed >= 86400:
                elapsed = 0
                await refresh()

    @asynccontextmanager
    async def lifespan(app):
        for job in store.all("assessment"):
            if job["status"] == "RUNNING":
                job["status"] = "INTERRUPTED"
                store.put("assessment", job["id"], job)
        for job in store.all("application"):
            if job["status"] in {"QUEUED", "RUNNING", "SUBMITTING", "VERIFICATION_REQUIRED", "LOGIN_REQUIRED"}:
                job["auto_resume_waiting"] = False
                job["status"] = "SUBMISSION_UNVERIFIED" if job.get("submission_attempted") else "INTERRUPTED"
                job["message"] = "The app restarted. Resume to recover; previous submissions will not be retried."
                store.put("application", job["id"], job)
        timer = asyncio.create_task(monitor())
        yield
        timer.cancel()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(timer, *tasks, return_exceptions=True)
        await browser.shutdown()
        store.close()

    app = FastAPI(title="SchemeSaathi", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)
    app.state.store, app.state.config, app.state.browser = store, config, browser
    from .web_security import install_security
    install_security(app, config)
    from .remote_browser import install_remote_browser
    install_remote_browser(app, browser, store)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ModelUnavailable)
    async def model_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    horticulture.install_routes(app, store, browser)
    tn_portals.install_routes(app, store, browser)

    @app.get("/api/health")
    async def health():
        return {"version": __version__, "llm_configured": llm.available(), "model": llm.settings()["model"], "mode": config.mode + "_single_owner", "headless": config.headless, "remote_browser": True, "demo_default": config.demo_default, "storage": "postgresql" if config.database_url else "sqlite"}

    @app.get("/api/fields")
    async def fields():
        return FIELDS

    @app.get("/api/profile")
    async def get_profile(demo: bool = False):
        return store.get("profile", "demo" if demo else "citizen", {"facts": {}})

    @app.put("/api/profile")
    async def update_profile(body: ProfileUpdate, demo: bool = False):
        profile = {"facts": body.facts, "confirmed_at": now()}
        if demo:
            profile.update(is_synthetic=True, label="Fictional resume demonstration")
        return store.put("profile", "demo" if demo else "citizen", profile)

    @app.post("/api/demo/setup")
    async def demo_setup():
        return seed_demo(store)

    @app.get("/api/demo/catalogue")
    async def demo_catalogue():
        return {"documents":document_catalogue(), "notice":"Fictional samples; not valid for official use."}

    @app.get("/api/demo/documents/{id}")
    async def preview_demo_document(id: str):
        document = store.get("document", id)
        if not document or not document.get("is_demo"):
            raise HTTPException(404)
        return Response(store.cipher.decrypt(store.get_blob(document["blob"])),
                        media_type=document["mime_type"],
                        headers={"Content-Disposition":"inline", "Cache-Control":"no-store"})

    @app.post("/api/intake")
    async def intake(body: Intake):
        return await agents.profile(body.text, body.language)

    @app.post("/api/voice")
    async def voice(file: UploadFile = File(...), language: str = Form("ta")):
        data = await file.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024 or language not in {"en", "ta"}:
            raise HTTPException(422, "Use a Tamil or English audio clip under 10 MB")
        if not (file.content_type or "").startswith("audio/"):
            raise HTTPException(422, "Upload an audio recording")
        return {"text": await llm.transcribe(data, Path(file.filename or "voice.webm").name, file.content_type, language)}

    @app.get("/api/settings")
    async def settings():
        value = llm.settings()
        return {"configured": bool(value["api_key"]), "model": value["model"], "provider": "Groq", "key_hint": "••••" + value["api_key"][-4:] if value["api_key"] else None}

    @app.put("/api/settings")
    async def save_settings(body: SettingsUpdate):
        value = store.get("settings", "llm", {})
        if body.api_key is not None:
            value["api_key"] = body.api_key.strip()
        value["model"] = body.model.strip()
        store.put("settings", "llm", value)
        return {"ok": True}

    @app.post("/api/settings/test")
    async def test_settings():
        return {"models": await llm.models()}

    @app.get("/api/schemes")
    async def schemes(demo: bool = False):
        return [s for s in store.all("scheme") if bool(s.get("is_demo")) == demo]

    @app.get("/api/readiness")
    async def readiness(demo: bool = False):
        # Always use current saved facts/documents, not a previous assessment snapshot.
        facts = store.get("profile", "demo" if demo else "citizen", {"facts": {}})["facts"]
        documents = [d["document_type"] for d in store.all("document") if bool(d.get("is_demo")) == demo]
        return [assess(s, facts, documents) for s in store.all("scheme") if bool(s.get("is_demo")) == demo]

    @app.get("/api/sources")
    async def sources(demo: bool = False):
        return [s for s in store.all("source") if (s["snapshot_kind"] == "synthetic") == demo]

    @app.get("/api/sources/versions/{source_id}")
    async def versions(source_id: str):
        return [s for s in store.all("source_version") if s["id"] == source_id]

    @app.post("/api/sources/check")
    async def check_sources():
        if not source_lock.locked():
            track(refresh())
        return {"started": True}

    @app.get("/api/sources/status")
    async def source_status():
        return store.get("monitor", "status", {"running": False})

    @app.post("/api/schemes/{scheme_id}/review")
    async def review_scheme(scheme_id: str, body: ReviewRequest):
        scheme = store.get("scheme", scheme_id)
        if not scheme or scheme.get("is_demo"):
            raise HTTPException(404, "Review a real catalogue scheme")
        validate_rule(body.rule, set(scheme["source_ids"]))
        if body.window_status == "OPEN":
            if not body.window_start or not body.window_end or date.fromisoformat(body.window_start) > date.fromisoformat(body.window_end):
                raise HTTPException(422, "An open window requires valid start and end dates")
        store.put("scheme_version", scheme_id + ":" + scheme["rule_version"], scheme)
        version = "review-" + uuid.uuid4().hex[:12]
        scheme.update(rule=body.rule.model_dump(), rule_version=version, coverage_complete=body.coverage_complete, review_status="REVIEWED" if body.coverage_complete else "DRAFT", review_note=body.review_note, reviewed_at=now(), review_valid_until=(date.today() + timedelta(days=30)).isoformat(), policy_changed=False, source_hashes={sid: store.get("source", sid)["hash"] for sid in scheme["source_ids"]}, window_status=body.window_status, window_start=body.window_start, window_end=body.window_end)
        store.put("scheme", scheme_id, scheme)
        return scheme

    @app.get("/api/retrieve")
    async def evidence(q: str):
        return await asyncio.to_thread(retrieve, store, q[:1000])

    @app.get("/api/graph")
    async def get_graph(demo: bool = False):
        return graph(store, [s["id"] for s in store.all("scheme") if bool(s.get("is_demo")) == demo])

    @app.post("/api/assessments")
    async def create_assessment(body: AssessRequest):
        id = uuid.uuid4().hex
        job = {"id": id, "status": "RUNNING", "created_at": now(), "demo": body.demo, "language": body.language, "profile_snapshot": store.get("profile", "demo" if body.demo else "citizen", {"facts": {}})["facts"], "document_snapshot": [d["document_type"] for d in store.all("document") if bool(d.get("is_demo")) == body.demo]}
        store.put("assessment", id, job)
        async def assess_and_prepare():
            await agents.run(id, body.language, body.demo)
            result = store.get("assessment", id)
            if body.auto_apply and result.get("status") == "COMPLETED":
                summary = {"queued": [], "skipped": []}
                for row in await readiness(body.demo):
                    reasons = row["preparation_blockers"]
                    if row["can_prepare"]:
                        try:
                            application = await create_application(ApplicationRequest(scheme_id=row["scheme_id"], demo=body.demo, auto_submit=body.auto_submit))
                            summary["queued"].append(application["id"])
                            store.event(id, "application", "Application preparation queued. Submission checks remain active.", scheme_id=row["scheme_id"])
                            continue
                        except HTTPException as exc:
                            reasons = [str(exc.detail)]
                    summary["skipped"].append({"scheme_id": row["scheme_id"], "name": row["name"], "reasons": reasons})
                    store.event(id, "application", "Preparation needs attention: " + " ".join(reasons), scheme_id=row["scheme_id"])
                result["application_summary"] = summary
                store.put("assessment", id, result)
        track(assess_and_prepare())
        return {"id": id, "status": "RUNNING"}

    @app.get("/api/assessments")
    async def assessments(demo: bool = False):
        return [j for j in store.all("assessment") if j["demo"] == demo][:30]

    @app.get("/api/assessments/{id}")
    async def get_assessment(id: str):
        job = store.get("assessment", id)
        if not job:
            raise HTTPException(404)
        return job

    @app.post("/api/assessments/{id}/resume")
    async def resume_assessment(id: str):
        job = store.get("assessment", id)
        if not job:
            raise HTTPException(404)
        if job["status"] == "INTERRUPTED":
            job["status"] = "RUNNING"
            store.put("assessment", id, job)
            track(agents.run(id, resume=True))
        return {"id": id}

    @app.get("/api/events/{id}")
    async def get_events(id: str, after: int = 0):
        return store.events(id, after)

    @app.get("/api/events/{id}/stream")
    async def event_stream(id: str, request: Request, after: int = 0):
        async def stream():
            cursor = after
            for _ in range(180):
                if await request.is_disconnected():
                    break
                for event in store.events(id, cursor):
                    cursor = event["seq"]
                    yield "id: " + str(cursor) + "\ndata: " + json.dumps(event) + "\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/documents")
    async def documents(demo: bool = False):
        return [{k: v for k, v in d.items() if k != "blob"} for d in store.all("document") if bool(d.get("is_demo")) == demo]

    @app.post("/api/documents")
    async def upload_document(file: UploadFile = File(...), document_type: str = Form(...)):
        known = {d["id"] for s in store.all("scheme") for d in s["documents"]} | {"beneficiary_photo"} | {d["id"] for d in DOCUMENTS}
        if document_type not in known:
            raise HTTPException(422, "Unknown document type")
        content = await file.read(10 * 1024 * 1024 + 1)
        if not content or len(content) > 10 * 1024 * 1024:
            raise HTTPException(422, "Upload a document between 1 byte and 10 MB")
        mime = "application/pdf" if content.startswith(b"%PDF") else "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if content.startswith(b"\xff\xd8\xff") else None
        if not mime:
            raise HTTPException(422, "Only PDF, PNG and JPEG documents are accepted")
        id = uuid.uuid4().hex
        directory = store.directory / "documents"
        directory.mkdir(exist_ok=True)
        filename = Path((file.filename or "document").replace("\\", "/")).name
        d = {"id": id, "document_type": document_type, "filename": filename, "mime_type": mime, "size": len(content), "blob": id, "is_demo": False, "uploaded_at": now(), "sha256": hashlib.sha256(content).hexdigest()}
        if hasattr(store, "save_document"):
            store.save_document(d, store.cipher.encrypt(content))
            return {"id": id, "filename": filename}
        store.put_blob(id, store.cipher.encrypt(content))
        for old in store.all("document"):
            if old["document_type"] == document_type and not old.get("is_demo"):
                store.delete_blob(old["blob"])
                store.delete("document", old["id"])
        store.put("document", id, d)
        return {"id": id, "filename": filename}

    @app.delete("/api/documents/{id}")
    async def delete_document(id: str):
        d = store.get("document", id)
        if not d:
            raise HTTPException(404)
        if hasattr(store, "remove_document"):
            store.remove_document(d)
        else:
            store.delete_blob(d["blob"])
            store.delete("document", id)
        return {"ok": True}

    @app.post("/api/applications")
    async def create_application(body: ApplicationRequest):
        scheme = store.get("scheme", body.scheme_id)
        if not scheme or bool(scheme.get("is_demo")) != body.demo:
            raise HTTPException(422, "Scheme and mode do not match")
        # One application per scheme per local citizen; cannot duplicate on retry.
        existing = next((j for j in store.all("application") if j["scheme_id"] == body.scheme_id and j["demo"] == body.demo and j["status"] != "CANCELLED"), None)
        if existing:
            return existing
        _, _, _, evaluation = browser.readiness({"scheme_id": body.scheme_id, "demo": body.demo})
        if not evaluation["can_prepare"]:
            raise HTTPException(422, " ".join(evaluation["preparation_blockers"]))
        id = uuid.uuid4().hex
        job = {"id": id, "scheme_id": body.scheme_id, "scheme_name": scheme["name"], "status": "QUEUED", "demo": body.demo, "auto_submit": body.auto_submit, "submission_attempted": False, "consented_at": now() if body.auto_submit else None, "created_at": now(), "updated_at": now(), "message": "Application preparation queued.", "unresolved": [], "submission_blockers": evaluation["submission_blockers"], "preparation_only": not evaluation["can_submit"]}
        store.put("application", id, job)
        browser.start(id)
        return job

    @app.get("/api/applications")
    async def applications(demo: bool = False):
        return [j for j in store.all("application") if j["demo"] == demo]

    @app.post("/api/applications/{id}/resume")
    async def resume_application(id: str, request: Request):
        job = store.get("application", id)
        if not job:
            raise HTTPException(404)
        body = await request.json()
        if job.get('official_demo') and body.get('auto_submit'):
            raise HTTPException(422,'Submission is disabled for fictional demo applicants.')
        if body.get("auto_submit") is True:
            job["auto_submit"] = True
            job["consented_at"] = now()
            store.put("application", id, job)
        browser.start(id)
        return {"id": id}

    @app.post("/api/applications/{id}/cancel")
    async def cancel_application(id: str):
        job = store.get("application", id)
        if not job:
            raise HTTPException(404)
        if job.get("submission_attempted"):
            raise HTTPException(409, "A submission was attempted. Its receipt history must be retained.")
        task = browser.tasks.get(id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await browser.close(id)
        browser.status(id, "CANCELLED", "Application preparation cancelled.")
        return {"ok": True}

    @app.post("/api/applications/{id}/close-browser")
    async def close_browser(id: str):
        job = store.get("application", id)
        if not job:
            raise HTTPException(404)
        if id in browser.tasks and not browser.tasks[id].done():
            raise HTTPException(409, "Wait for the active browser step to finish")
        await browser.close(id)
        return {"ok": True}

    @app.put("/api/mappings/{scheme_id}")
    async def mappings(scheme_id: str, request: Request):
        if not store.get("scheme", scheme_id) and scheme_id not in {s["id"] for s in BY_ADAPTER.values()}:
            raise HTTPException(404)
        body = await request.json()
        allowed = set(FIELDS) | {"doc:" + d["id"] for s in store.all("scheme") for d in s["documents"]} | {"doc:"+d["id"] for d in DOCUMENTS} | {"doc:beneficiary_photo"}
        saved = store.get("mapping", scheme_id, {})
        for key, value in body.items():
            if not isinstance(key, str) or len(key) > 300 or value not in allowed:
                raise HTTPException(422, "Map an observed label to a known profile field or document")
            saved[tn_portals.norm(key) if scheme_id in {s["id"] for s in BY_ADAPTER.values()} else normalize(key)] = value
        store.put("mapping", scheme_id, saved)
        return {"ok": True}

    @app.get("/api/applications/{id}/screenshot")
    async def screenshot(id: str):
        item = store.get("screenshot", id)
        if not item:
            raise HTTPException(404)
        return Response(base64.b64decode(item["png"]), media_type="image/png")

    @app.get("/api/export/{id}")
    async def export(id: str):
        job = store.get("assessment", id)
        if not job:
            raise HTTPException(404)
        report = {"application": "SchemeSaathi", "exported_at": now(), "assessment": job, "events": store.events(id), "notice": "Screening is not government approval. Check the referenced policy versions and application window."}
        return Response(json.dumps(report, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="SchemeSaathi_assessment_{id[:8]}.json"'})

    def demo_job(id):
        if not id or not id.isalnum():
            raise HTTPException(404)
        job = store.get("application", id)
        if not job or not job["demo"]:
            raise HTTPException(404)
        return job

    @app.get("/demo/portal", response_class=HTMLResponse)
    async def demo_portal(job: str):
        demo_job(job)
        receipt = store.get("demo_receipt", job)
        return demo.acknowledgement(receipt["receipt"]) if receipt else demo.profile_form(job)

    @app.post("/demo/details", response_class=HTMLResponse)
    async def demo_details(request: Request):
        form = dict(await request.form())
        job = str(form.pop("job", ""))
        demo_job(job)
        facts = ProfileUpdate(facts=form).facts
        if not facts.get("full_name") or facts.get("state") != "Tamil Nadu" or not 18 <= facts.get("age", 0) <= 30 or not 0 <= facts.get("annual_household_income_inr", -1) <= 250000:
            raise HTTPException(422, "Invalid fixture applicant")
        store.put("demo_form", job, {"details": facts})
        return demo.upload_form(job)

    @app.post("/demo/document", response_class=HTMLResponse)
    async def demo_document(job: str = Form(...), study_certificate: UploadFile = File(...)):
        demo_job(job)
        state = store.get("demo_form", job)
        if not state or not await study_certificate.read(1):
            raise HTTPException(422, "Complete the test profile and upload a file")
        state["document_received"] = True
        store.put("demo_form", job, state)
        return demo.review_form(job, state["details"])

    @app.post("/demo/submit", response_class=HTMLResponse)
    async def demo_submit(job: str = Form(...)):
        demo_job(job)
        state = store.get("demo_form", job)
        if not state or not state.get("document_received"):
            raise HTTPException(422, "Complete the previous test steps")
        receipt = store.get("demo_receipt", job)
        if not receipt:
            receipt = {"receipt": "SS-DEMO-" + secrets.token_hex(6).upper(), "created_at": now()}
            store.put("demo_receipt", job, receipt)
        return demo.acknowledgement(receipt["receipt"])

    static = ROOT / "app/static"
    if (static / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(static / "favicon.svg", media_type="image/svg+xml")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        if not (static / "index.html").exists():
            return HTMLResponse("Frontend is not built. Run npm install and npm run build in frontend/.", status_code=503)
        return FileResponse(static / "index.html")

    return app
