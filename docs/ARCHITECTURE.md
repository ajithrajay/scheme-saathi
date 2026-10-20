# Architecture and contracts

## Runtime

React/TypeScript assets and the API share a FastAPI process. Local `run.py` binds to loopback and prints an owner key. Cloud `serve.py` binds to `0.0.0.0:$PORT` behind HTTPS. Both use a password-style owner-key login, random revocable 12-hour sessions, HttpOnly/SameSite cookies and CSRF checks; cloud cookies are Secure. No public signup or tenant isolation is implemented.

Local `Store` uses SQLite WAL and an encryption key in `runtime`; `PostgresStore` stores Fernet-encrypted JSON and document bytes in PostgreSQL using an externally managed stable key. A single private owner workspace and a separate fictional practice workspace are supported. Public downloaded source snapshots and browser profiles on the filesystem are disposable; durable source versions and application events are in the database. Cloud startup refuses ephemeral-only storage.

Playwright contexts live in one process. PostgreSQL deployments additionally hold a direct session advisory lock for the entire active browser, preventing another process from opening one concurrently. Before portal write requests, the runner checks that lock connection is alive. Do not use a transaction pooler or scale the service horizontally. Close browsers before deployment. The `/healthz` endpoint checks the database without exposing private content.

The live handoff API returns transient JPEG frames to the authenticated owner, with no-store caching. Controls accept only bounded clicks, text, selected keys and scrolls, during login/verification pauses on approved portal hosts. It does not accept URLs, arbitrary JavaScript or selectors. Browser-runner locks serialize human actions against the verification watcher. Authentication screens are not retained as screenshot records. Session cookies are lost when a cloud browser closes or its process restarts.

See DEPLOY.md for hosting limits, security boundaries, backup/restore and remaining production validation gates.

## Agent workflow

| Component | Inputs | Output and authority |
|---|---|---|
| Profile agent | User text plus allowed field schema | Validated proposed facts; user confirms writes |
| Discovery node | Catalogue IDs and query | All candidate schemes plus cited lexical/optional semantic passages |
| Rule engine | Confirmed profile and source-linked rule version | Three-valued condition tree; screening state; missing fields |
| Application planner agent | Evaluation output, evidence, language | Suggested steps and known source IDs; no policy mutation |
| Evidence verifier agent | Proposed steps, evidence, rule results | Accept/withhold advice; deterministic checks also inspect IDs and versions |
| Policy update agent | Changed official passages | Draft change summary; cannot activate a rule version |
| Browser runner | Confirmed facts, selected scheme, mapped files, consent | Bounded actions, human handoff or verified receipt |

The LangGraph sequence is discovery → eligibility → planner → verifier. Each completed node commits an encrypted checkpoint and an event. On restart an incomplete assessment is marked INTERRUPTED and can resume without redoing completed nodes. Source versions and the original profile snapshot are retained with the assessment. Running a fresh assessment regenerates planning advice after changed input or rule approval. Scheme cards use the separate `/api/readiness` endpoint to evaluate current saved profile, documents and rules immediately; they do not depend on an old assessment snapshot.

Profile and policy agents have separate entry points. Discovery and arithmetic are normal functions; no extra paid model call is made merely to choose catalogue rows. When no key is configured, deterministic planning templates remain available and mode labels make the absence of AI explicit. AI-suggested advice is never used as an eligibility verdict.

## Rule semantics

Leaves support eq, in, lt/lte/gt/gte and inclusive between. Groups support nonempty ALL, ANY, and one-child NOT. Missing facts yield UNKNOWN; confirmed false remains false. ALL fails if any child fails; ANY passes if any child passes. Only unresolved branches produce missing-fact requests. Numeric comparisons use Decimal. Intake validation rejects invalid booleans, negative/out-of-range numeric values, NaN/Infinity and unrecognized keys.

A known failed condition produces NO_MATCH, missing facts produce NEEDS_INFORMATION, and passing known conditions produce REVIEW_REQUIRED when policy coverage is incomplete, approval has expired or a source changed. The interface labels REVIEW_REQUIRED as a preliminary match and displays policy verification separately. Application dates and file availability are also separate outputs. File availability is not authenticity or government validation. Cutoff-age, benefit incompatibility, renewal and income-period rules must be modelled explicitly for each new scheme; do not reuse present-day age where a policy asks for age at a cutoff date.

Preparation requires passing known conditions, a saved name, the configured documents and no recorded closed window. Provisional policy or unknown dates do not block preparation. Final submission requires a current reviewed MATCH, an open window, the configured documents and submission consent. Both actions re-evaluate readiness independently; enabling preparation never approves a policy or grants submission consent.

## Source lifecycle

Seed paraphrase → permitted official download → content hash/raw snapshot → diff → policy-change flag → optional draft change analysis → human-reviewed rule version. Application checks always re-evaluate current policy immediately before browser work and submission. Review metadata stores source hashes and a note. It does not imply a government authority certified the policy.

Network fetching accepts only existing allowlisted government source hosts, checks public DNS addresses, limits redirects and response sizes, and rejects private targets. PDFs use pypdf text extraction; scanned documents require human review. All source content is treated as untrusted data in model prompts. No source text can grant tools or issue browser actions.

## Browser state machine

QUEUED → RUNNING → PORTAL_UNAVAILABLE / NOT_READY / LOGIN_REQUIRED / VERIFICATION_REQUIRED / NEEDS_INFORMATION / UNSUPPORTED_FORM / SUBMISSION_BLOCKED / READY_FOR_SUBMISSION.

PORTAL_UNAVAILABLE records a sanitized navigation error code and optional HTTP status. A failed entry navigation closes its browser context so an explicit retry starts a fresh navigation. No automatic retry loop runs against a failing service. Failure after a submission attempt remains SUBMISSION_UNVERIFIED; it cannot reset the form or resubmit. The separate `app.portal_check` command visits a fixed set of public official entry pages in an isolated browser and exports form structure without reading the app's profile or runtime database.

SUBMISSION_BLOCKED means the runner reached the final step but current policy or date verification prevents submission. The job records the specific blockers. Resume rechecks readiness and cannot bypass them, including when automatic submission was selected.

With consent and current readiness: RUNNING → SUBMITTING → SUBMITTED or SUBMISSION_UNVERIFIED. Submission intent is persisted before the irreversible click. Recovery from an attempted submission only looks for a receipt; it never clicks Submit again. Duplicate starts return the same local application. Cancelled, unsubmitted preparations can be restarted as new jobs.

Browser operations use one async task and lock per application, with at most three open browser contexts, twelve automated steps per run, request timeouts and resumable pauses. Browser profiles are dedicated directories, separate from the user's normal browser. Cookies stay in those profiles. Closing a profile window may require logging in again. Manual OTP/CAPTCHA completion happens in the visible application browser.

## Production expansion requirements

For a real community pilot, complete and independently validate the scheme catalogue and each portal adapter first. Multi-user hosting requires real identity/tenant isolation, external key management, database migrations, a durable worker queue, backup/restore, access logging, deletion/export policy, signed review roles, and documented service permissions. Scale testing, Tamil comprehension testing, scheme coverage benchmarks and paid-account limits must be measured. Redis, PostgreSQL, Neo4j, MCP and vector services are not claimed as deployed components here.
