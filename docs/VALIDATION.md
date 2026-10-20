# Cloud edition 1.1.0 validation — 20 October 2026

Tests run in the source-preparation environment (Python 3.12, Linux):

- Existing API, deterministic rules and Windows encoding tests: **63 passed**, including 2 subtests.
- Cloud authentication/configuration, remote OTP handoff, practice browser E2E and TN registration-origin regressions: **21 passed**, with 2 PostgreSQL tests skipped because no PostgreSQL server was available at that point.
- After the final browser UI changes: cloud security and both remote-browser tests **6 passed**. This includes a real Chromium sign-in, frontend load and live browser UI interaction.
- After adding transactional cloud document replacement: API/cloud tests passed; three PostgreSQL integration tests require `TEST_DATABASE_URL` and are collected but skipped locally. The CI workflow provisions PostgreSQL 16 to run them.
- React TypeScript check and Vite production build passed. Built frontend assets are included.
- `pip check` passed. Python compilation passed.

The UI test initially used a string-evaluation assertion blocked by the application's Content Security Policy. The test was changed to a locator assertion; the application's restrictive policy was retained. The corrected test passed.

The browser tests use fictional data and intercepted/local portal fixtures. No real OTP was requested, no government application was submitted, and no genuine receipt was generated. Live receipt formats, portal changes and cloud-IP acceptance remain deployment-specific validation requirements.

Not executed here: Docker build (Docker unavailable), a real PostgreSQL persistence/locking test (server unavailable), Render deployment, native Windows runtime, production memory/load tests, backup/restore exercise, live government OTP/application flow, or independent security audit. The three PostgreSQL tests cover encrypted persistence across store instances, the cross-process browser lock, and transactional document replacement. GitHub Actions also builds the Docker image; its actual result must be checked after pushing the repository.

This release is a single-owner deployable pilot with production-oriented protections. It is not certified as an audited, multi-user production service. Use the acceptance steps in DEPLOY.md before real production use.
