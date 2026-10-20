# TN application automation (1.0.7)

See [TN_AUTOMATION.md](TN_AUTOMATION.md) for the three-website scope and exact live
validation limits. `app/tn_portals.py` adds AGRISNET request, welfare registration
and registered-member claim workflows. These are conservative runtime label
adapters, not certified authenticated portal integrations. `app/tn_portal_data.py`
contains fixed hosts, required facts and document types. `BrowserRunner` observes
verification completion and resumes TN jobs automatically for up to 15 minutes.

The generic scholarship engine below retains its existing policy gates.

# Portal support and extension guide

## TN Horticulture kit adapter (1.0.5)

`app/horticulture.py` implements a separate, explicitly chosen citizen request at https://tnhorticulture.tn.gov.in/kit/. It uses selectors observed live on 18 October 2026, including the official misspelling `benificiary_name`. It checks complete saved details and a JPG/PNG photograph, matches bilingual dropdown labels, waits for dependent block/village options, and checks one free kit quantity. The form has no separate account-registration step visible. District selection can disable kit choices; this is respected.

The local user authorizes disclosure before any filling/uploading and may choose one-time submission, or review the filled browser first. Unknown visible fields or declarations stop the adapter. Profile/photo changes require new preparation. A persisted submit attempt prevents automatic retry or a second local kit request across all three choices. The tracking link uses the portal's separate public tracking form; tracking is not automatically authenticated or queried.

This route checks request completeness, not eligibility. It neither changes scholarship review flags nor assigns invented application dates. Department approval and in-person collection are external steps. The receipt detector conservatively requires English success text plus an application/acknowledgement reference; the live receipt format remains unverified. An unrecognized/Tamil-only response is retained as SUBMISSION_UNVERIFIED for manual checking. Do not expand the success parser without examining a genuine, redacted response.

`tests/test_horticulture_e2e.py` serves the observed control structure from an entirely local fixture at an intercepted URL. These tests cover the real application API/UI, delayed cascading options, encrypted photograph upload, single submission, duplicates, missing receipt, changed profile, new unknown controls and disabled kit options. They do not constitute a real government receipt.

## Other portals

The included browser engine is operational and tested against a realistic local three-step portal. Real UMIS and NSP entry routes are included but are **not authenticated, portal-certified integrations**. Do not confuse a configured URL with a tested connector.

## Generic scholarship runner behavior

1. For preparation, require passing known conditions, a saved full name, all configured documents and no recorded closed window. Draft policy and an unknown window allow preparation, but never establish eligibility or submission permission.
2. Open a separate persistent browser profile on an allowlisted portal.
3. Inspect visible native input/select/textarea controls, labels, names and IDs in approved frames.
4. Match exact normalized aliases or user-saved label mappings; never derive values from page instructions.
5. Fill confirmed values, exact dropdown options and mapped file inputs. Third-party names and ambiguous income fields remain unresolved.
6. Stop on login, OTP, CAPTCHA, declarations, custom dropdowns, unsupported fields or failed advancement.
7. Advance recognized Next/Continue controls. At the final button, recheck profile/document fingerprints, reviewed eligibility, the current application window and documents. Pause as SUBMISSION_BLOCKED if verification is incomplete. Submit once only when those checks and per-job consent all pass.
8. Require success text and an acknowledgement identifier. Without both, preserve SUBMISSION_UNVERIFIED and do not retry submission.

## Adding a verified adapter

- Establish the legitimate citizen application route, authentication requirements, permitted automation and any institutional-only steps from the official service.
- Add exact hosts to the scheme's `allowed_hosts` in the seed data, with a migration for existing workspaces. Do not allow all domains or arbitrary URLs. A portal redirect to a new host must be reviewed explicitly.
- Extend `app/browser.py` with portal-scoped selectors if exact label matching is insufficient. Keep the selector-to-profile mapping deterministic. Never infer a missing answer.
- Add controlled custom-widget logic (for example role=combobox plus exact option match), portal file-size checks, required-field validation and multi-step checkpoints.
- Add a portal-specific acknowledgement parser and validate it on a representative receipt, including failure, timeout and already-applied pages. Do not classify a populated form as submitted.
- Write an offline fixture representing the observed page structure and add cases to `tests/test_browser_e2e.py`. Then test the live route with a consenting applicant and record the validation date, stage and scope. Do not make a real application simply to create a test receipt.

Mappings can be made directly in Applications when an unknown label is displayed. Choose an actual known field/document; do not map a declaration to an unrelated yes/no value. Password, OTP, CAPTCHA and declaration labels cannot be mapped automatically. If a submitted record already exists, the application history must remain intact.

## Common handoffs

| Status | Required next step |
|---|---|
| LOGIN_REQUIRED / VERIFICATION_REQUIRED | Complete the step in the visible browser; TN jobs auto-resume while watching, otherwise Resume |
| NEEDS_INFORMATION | Confirm missing facts, map the exact label or complete the browser field |
| PROFILE_CHANGED | Cancel the unsubmitted preparation and start again with current facts/documents |
| UNSUPPORTED_FORM | Navigate to the actual form in that browser, then Resume; otherwise implement an adapter |
| NOT_READY | Resolve the listed preparation blockers: saved profile, missing documents, failed condition or closed window |
| SUBMISSION_BLOCKED | Form preparation reached the final step; verify the policy and dates shown, then Recheck submission readiness |
| READY_FOR_SUBMISSION | Review the visible form and choose Submit application |
| SUBMISSION_UNVERIFIED | Inspect the official account; Recheck receipt only; no automatic retry |
| SUBMITTED | Receipt verified by the parser; this is not proof the authority approved the benefit |

Cloud mode uses headless Chromium with the authenticated live browser handoff described in DEPLOY.md. Local mode can use a visible browser; Windows prefers installed Edge and otherwise falls back to bundled Chromium. Complex CAPTCHA gestures or hardware login may require local desktop mode.
