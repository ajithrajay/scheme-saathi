# SchemeSaathi 1.1.0 — local and cloud edition

Scheme discovery, Tamil/English intake, encrypted profile/document storage, and supervised Playwright applications. Includes the existing TN portal adapters, a practice portal, a React frontend, and a Docker/Render deployment configuration. No BAT files are used.

**Deployment scope:** one owner and one application browser at a time. This is a deployable pilot with production-oriented safeguards, not an audited multi-user government service. Read [DEPLOY.md](DEPLOY.md) for the free-hosting limits and the production acceptance checklist. No live government submission or Render deployment has been certified by these tests.

## Run locally on Windows

Install Python **3.12**. Open Command Prompt or PowerShell inside this extracted folder. The built frontend is included; Node.js is unnecessary unless you edit it.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m ensurepip --upgrade
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe run.py
```

Open `http://127.0.0.1:8765/login` and enter the **owner key printed in your terminal**. Leave the terminal open; Ctrl+C stops the app. These commands do not require activating a virtual environment or changing PowerShell execution policy.

For later starts, run only:

```powershell
.venv\Scripts\python.exe run.py
```

To reuse an existing virtual environment, use its Python path for the last two install commands and `run.py`. Install the updated requirements once. Keep the old project and `runtime` backed up before changing versions.

## Run locally on macOS/Linux

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python run.py
```

On Linux, missing system browser dependencies can be installed with `.venv/bin/python -m playwright install --with-deps chromium` (system package installation may require administrator access). Use a desktop session for a visible browser, or set `SCHEMESAATHI_HEADLESS=1` in `.env` and use the in-app live browser.

## Configuration and first use

Copy `.env.example` to `.env` if you want persistent settings. Local mode otherwise works without it. The server loads UTF-8 explicitly.

1. Sign in, fill **My profile** and **TN applications**, and upload the required documents.
2. Add your own Groq key under **Settings**, then **Test connection**. AI is optional for structured forms and deterministic adapters. Account quotas/model access are controlled by Groq; there is no payment or automatic plan upgrade in this app.
3. Try practice mode before using real documents.
4. Start an application. When verification is needed, open **Applications → Open live browser / OTP**. Click the portal field in the image, enter the code in the text box, and press **Type into selected field**. Click the portal's verify button if needed. CAPTCHA is solved by you.
5. TN verification watchers continue automatically when the challenge clears. Other flows use **Resume**. With automatic submission off, review the prepared application before clicking **Submit application**.

Application dates, policy review, documents and eligibility remain independent checks. Deployment does not remove these checks or make an unavailable government scheme open. Unknown fields still require accurate information. No invented answers, CAPTCHA solving service, guaranteed eligibility, or guaranteed acceptance is provided.

## Deploy

Follow [DEPLOY.md](DEPLOY.md). Recommended free pilot: **Render Docker web service + Neon PostgreSQL**. The included `render.yaml` provisions only the web service; create the external database separately.

For an always-on deployment, use the same Docker image with a suitable paid host, persistent PostgreSQL, backups and HTTPS. Keep **one process/replica**. Browser state is transient; records and documents are durable in PostgreSQL.

## Develop and test

Python 3.12 and Node.js 22:

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium --only-shell
cd frontend
npm ci
npm run build
cd ..
python -m pytest -q
```

`TEST_DATABASE_URL` enables the PostgreSQL integration tests against an **empty disposable database**. GitHub Actions provisions PostgreSQL 16, runs the deployment/API/browser tests, and builds the Docker image. See [docs/VALIDATION.md](docs/VALIDATION.md) for what was actually verified in this release.

## Key files

| File | Purpose |
|---|---|
| `run.py` | Local desktop launch, bound to loopback |
| `serve.py` | Single-process cloud launch on `0.0.0.0:$PORT` |
| `Dockerfile` / `render.yaml` | Container build and free Render pilot |
| `app/web_security.py` | Owner sessions, CSRF, bounded request bodies, security headers |
| `app/postgres_store.py` | Encrypted records/files and cross-process browser lock |
| `app/remote_browser.py` | Authenticated live view and human verification controls |
| `scripts/generate_secrets.py` | Generate separate owner and encryption secrets |
| `scripts/migrate_local.py` | Import a local backup into an empty PostgreSQL database |
| `DEPLOY.md` | Environment variables, deployment, backups and troubleshooting |

Keep secrets, your runtime directory and personal files out of Git. Third-party UI/font licenses are in `docs/licenses/`.
