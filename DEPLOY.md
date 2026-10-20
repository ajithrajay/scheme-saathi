# Deploy SchemeSaathi

## What is free, and what is production?

This package supports a free **single-owner pilot** on Render + Neon. Render explicitly advises against production use of its free services. Free web services sleep after 15 minutes without inbound traffic, take time to wake, and discard their local filesystem on restart, redeploy or sleep. A running browser cannot survive that process ending. This app stores profiles, documents, settings, application records and events in external PostgreSQL so those records survive; browser cookies and active pages do not.

Do not use Render's free PostgreSQL as permanent storage: its free database expires after 30 days. Neon offers a free PostgreSQL tier with usage limits. The guide uses that instead. Neither service promises unlimited free operation. Review the current provider quotas before deploying.

For reliable production use, choose always-on compute with enough memory for Chromium, durable PostgreSQL and tested backups. Size from your actual portal's memory use; 2 GB is a starting point for testing one browser, not a guarantee. A small free instance can run out of memory on government portals. The deployment is **not multi-tenant**: the owner key gives access to the entire installation.

Official references, checked 20 October 2026:

- https://render.com/docs/free
- https://render.com/docs/web-services
- https://render.com/docs/blueprint-spec
- https://render.com/docs/environment-variables
- https://neon.com/docs/introduction/plans
- https://neon.com/docs/connect/connect-from-any-app
- https://playwright.dev/python/docs/docker

## 1. Create the database

1. Create a Neon account and a Free project. Pick a region near your Render region where available.
2. Open **Connect**. Choose the database and role, and turn **connection pooling OFF**. Copy the **direct** PostgreSQL connection string.
3. Keep the connection string's `sslmode=require` (or use `verify-full`). Never commit this string to Git.

The direct connection is required because the app holds a PostgreSQL advisory lock for the lifetime of an active browser. A transaction pooler cannot provide this guarantee. A `-pooler` Neon hostname is rejected at startup. Use a dedicated database for this single SchemeSaathi installation.

## 2. Generate secrets locally

After installing Python requirements, run:

```powershell
.venv\Scripts\python.exe scripts\generate_secrets.py
```

On macOS/Linux:

```bash
.venv/bin/python scripts/generate_secrets.py
```

Save both printed values in your password manager. **Back up the encryption key separately.** Losing it makes stored profiles and documents unreadable. The owner key can be changed; changing the encryption key without re-encrypting existing records cannot be used as a password reset.

## 3. Upload the code to a Git repository

Create a private GitHub repository. Upload the **contents of this folder**, so `Dockerfile`, `render.yaml`, `requirements.txt`, `app/` and `frontend/` are at the repository root.

Do not upload `.env`, `.venv`, `runtime`, API keys, personal documents or `node_modules`. `.gitignore` and `.dockerignore` are included. If you use the GitHub website upload UI, check that `.github/workflows/test.yml` is also present; uploading with Git is the more reliable option for hidden files.

The CI workflow tests PostgreSQL persistence and builds the Docker image. Check its result before putting real personal information into the hosted app. Docker and PostgreSQL integration could not be executed in the source-preparation environment; this is an explicit release validation gate, not a claim that CI has already passed.

## 4. Create the Render web service

### Option A: Blueprint

In Render, select **New → Blueprint**, connect the repository and let it read `render.yaml`. It selects a Docker web service on the Free plan and disables automatic deployment during active applications.

Enter the requested secrets:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Direct Neon connection string with TLS required |
| `SCHEMESAATHI_ENCRYPTION_KEY` | Fernet key generated in step 2 |

The Blueprint generates `SCHEMESAATHI_OWNER_KEY`. After creation, find this secret under the service's **Environment** settings and save it privately, or replace it with the owner key generated in step 2. This is your app login key.

The app discovers its HTTPS URL from Render's `RENDER_EXTERNAL_URL`; no guessed URL is required.

### Option B: New Web Service

Choose **New → Web Service**, connect the same repository, and configure:

| Setting | Value |
|---|---|
| Runtime | Docker |
| Root directory | Empty when files are at repository root |
| Dockerfile path | `./Dockerfile` |
| Instance | Free for pilot/testing |
| Health check path | `/healthz` |
| Automatic deploys | Off; deploy when application browsers are closed |
| Docker command | Leave empty; Dockerfile starts `python serve.py` |

Set these environment variables:

```text
SCHEMESAATHI_MODE=cloud
SCHEMESAATHI_HEADLESS=1
SCHEMESAATHI_MAX_BROWSERS=1
DATABASE_URL=<direct PostgreSQL connection string with sslmode=require>
SCHEMESAATHI_OWNER_KEY=<generated random owner key>
SCHEMESAATHI_ENCRYPTION_KEY=<generated Fernet key>
```

Optional: `GROQ_API_KEY` and `GROQ_MODEL`, or enter the Groq key in Settings after sign-in. For a custom domain set `SCHEMESAATHI_PUBLIC_URL=https://your-domain.example` with no path. Use that origin to sign in; writes from other origins are rejected. Render sets `PORT`; the service binds to `0.0.0.0` and respects it.

Start deployment. The Docker build installs Python dependencies, Chromium and its Linux libraries, builds the frontend, and runs the service as an unprivileged user. It does not need a persistent Render disk.

## 5. Verify the hosted app

1. Open the service's HTTPS URL. Sign in with `SCHEMESAATHI_OWNER_KEY`.
2. Check `https://YOUR-SERVICE.onrender.com/healthz`; it should show `{"status":"ok"}`.
3. Run a **practice** application to its fictional receipt. Exercise the live browser before trying a real OTP flow.
4. Save a fictional profile and document, manually redeploy with all browser jobs closed, sign in again, and check the data survived. Sessions intentionally expire on restart.
5. Only then test a real portal you are authorized to use. Government sites may block cloud IP addresses, change fields, require additional login methods, or be temporarily closed. Those conditions are not bypassed.

The server stores uploaded documents encrypted in PostgreSQL. The browser view is fetched through authenticated API routes with no-store caching. Live OTP/CAPTCHA frames are not saved. The UI supports clicks, text entry, a few navigation keys and scrolling, not a full remote desktop: drag gestures, audio CAPTCHAs and hardware authentication are not implemented. Use local desktop mode for those flows.

The TN adapters normally resume automatically after verification clears, for up to 15 minutes. If a job reports an uncertain submission, check its official receipt/status; do not start a second application just to retry it. Closing a browser or restarting the server requires logging into the official portal again.

## Other Docker hosts

Use the same image on a Linux container host with an HTTPS reverse proxy and PostgreSQL:

```bash
docker build -t schemesaathi:1.1.0 .
docker run --name schemesaathi --env-file .env.cloud -p 127.0.0.1:10000:10000 --shm-size=256m schemesaathi:1.1.0
```

`.env.cloud` contains the cloud variables above plus `SCHEMESAATHI_PUBLIC_URL` and `PORT=10000`. Configure your proxy to preserve the public Host header, terminate TLS, and forward to `127.0.0.1:10000`. Do not expose the direct HTTP listener publicly. Run one replica and one Python process; do not add Gunicorn workers or horizontal autoscaling.

Chromium uses Playwright's default launch sandbox setting. Container isolation is not a replacement for a browser sandbox. For a hardened production host, validate sandbox-enabled Chromium with the host's seccomp/user-namespace configuration, restrict outbound access to required public sites and the database, and patch browser dependencies regularly. `network_guard.py` rejects private destinations as defense in depth; a host egress firewall is still required to fully address DNS rebinding. Do not point these adapters at arbitrary untrusted sites.

## Bring your local data

The new local mode can use your existing `runtime` directory. **Stop the old app and back it up first.** Keep the matching `encryption.key` with that SQLite database.

To import into the cloud, do this **before the first cloud start**, while the destination database is empty:

1. Back up the old `runtime` directory while the local app is stopped.
2. Put the destination `DATABASE_URL` and `SCHEMESAATHI_ENCRYPTION_KEY` in your local `.env`. Leave `SCHEMESAATHI_MODE=local` for this command.
3. Run `.venv\Scripts\python.exe scripts\migrate_local.py "C:\path\to\runtime-backup"` (macOS/Linux: `.venv/bin/python scripts/migrate_local.py /path/to/runtime-backup`).
4. The import runs in a transaction, re-encrypts records and documents using the cloud key, and refuses a nonempty destination. Browser cookies and screenshots are not imported. Keep the backup until the cloud data is verified.

## Backups and upgrades

Back up PostgreSQL using your provider's supported backup/export tooling, and back up `SCHEMESAATHI_ENCRYPTION_KEY` separately. Restore into a **separate empty database**, use the same key, start a separate single-owner installation, and verify the profile and a document before relying on the backup.

Close application browsers before deploying. Application state is durable, but browser sessions are not. Interrupted jobs are recovered as interrupted; a recorded submission attempt is never automatically repeated. The database lock prevents concurrent official browser sessions across overlapping app processes, but this is not an exactly-once delivery guarantee for an external government site. The official acknowledgement remains the source of truth.

Owner sessions are held in memory for 12 hours and are revoked on logout/restart. Rotating `SCHEMESAATHI_OWNER_KEY` and restarting revokes existing sessions. Update pinned Python/browser dependencies and rebuild/test regularly. There is no automatic file retention deletion; the owner must delete documents when no longer required and apply a database backup retention policy.

## Troubleshooting

| Symptom | Check |
|---|---|
| App takes a minute to open | Free Render service may be waking from sleep |
| Startup rejects configuration | Check owner key length, encryption key format, TLS database URL, and public HTTPS origin |
| Invalid host / write rejected | Use the configured HTTPS URL; update `SCHEMESAATHI_PUBLIC_URL` for a custom domain |
| Cannot decrypt / InvalidToken | Restore the original encryption key; do not generate a replacement for an existing database |
| Database connection error | Use direct Neon URL, retain TLS parameters, check free quota and network access |
| Browser already in use | Close the current application browser; during deployment, wait for the old instance to stop |
| Browser closes or service restarts | Inspect Render memory/exit logs; free memory may be insufficient for the portal |
| Live browser says it is running | Controls intentionally pause while automation runs; wait for a verification pause |
| Login expires after a deploy | Sign in again; saved application data is in PostgreSQL |
| Send OTP does not finish | Use the live portal view to see its validation. Do not repeatedly request OTPs; check site availability and cloud-IP restrictions |
| Policy review / unknown window | Complete policy review against official sources; this is independent of uploaded documents |

## Acceptance before real production use

Require passing CI (including PostgreSQL and Docker build), an HTTPS staging deployment, a successful persistence/restart and backup/restore exercise, memory/load measurement on the intended portals, and an authorized live OTP/application walkthrough. Add per-user identities and row-level isolation before serving unrelated citizens. This release implements a single owner's private workspace; sharing its owner key would share all data.
