# Three TN portal workflows — version 1.0.10

Open **TN applications** in SchemeSaathi. Horticulture remains available under
**TN garden kits** too. No Groq key is required for these browser workflows.

## Use

1. Choose the website and action. Supply the real profile details requested on
   that screen; save the known scheme and location names. Answer the residence/land same-location question explicitly.
2. Save application details, upload the listed documents, and authorize sharing
   them with the displayed official host.
3. Leave **Submit once automatically** selected to submit a complete supported
   form, or uncheck it to review before the final click.
4. Enter OTP/CAPTCHA in the official browser window. On the observed welfare
   registration and member-login forms, the app clicks Verify/Login after the
   entered code is stable for two polling intervals. It never solves CAPTCHA or
   generates OTP. An unchanged rejected code is not automatically retried.
5. Keep SchemeSaathi and its browser open. Automatic watching lasts 15 minutes;
   Resume restarts it if needed. Closing the browser or cancelling stops watching.
6. A detected reference gives **Receipt verified**. **Receipt not verified**
   means a submission was attempted but is uncertain: check the official portal.
   Do not make a second request. This app will not automatically resubmit it.

## Welfare registration OTP handling (1.0.9)

A correct mobile number may become read-only after the entry step. The app
preserves it instead of attempting to type into it again. A different locked
number pauses the preparation for correction in the official browser.

For the authorized registration, it acknowledges the exact observed family-member
mobile confirmation and waits for the OTP input. It supports bilingual button
text and plain links. Enter your OTP yourself; the existing watcher clicks
Verify OTP once the entered code is stable, then resumes after verification.

A click without an OTP screen is not treated as delivery. Resume does not repeat
an uncertain Send OTP request; inspect portal validation and use Send OTP/Resend
manually if needed. The known popup handler remains active in that browser.
Unknown dialogs are dismissed and a fixed diagnostic is logged, without storing
raw dialog messages, mobile numbers or codes. No authentication screenshots are
retained; the screenshot viewer explains that the official browser is required.

## Registration origin and verified-login continuation (1.0.10)

The public registration page was inspected on both official hosts on 19 October
2026. The www page stays on www, while its inline OTP handler uses an absolute
non-www URL. SchemeSaathi now opens `https://tnuwwb.tn.gov.in/applications/register`
to keep the page and its OTP service on the same origin. Browser security remains
enabled. A user-session network trace was not available, so this confirmed URL
mismatch is a likely explanation, not proof of every reported OTP failure.

Only unverified legacy entry pages are migrated. An already-visible OTP or an
application form is preserved. If a prior Send OTP attempt was recorded, Resume
will not silently resend it. When no code arrived, use Send OTP once in the
corrected official browser, then enter the code there.

A specific observed alert requests the saved ration-card number when the mobile
belongs to another family member. The app acknowledges that request, waits for
the ration field, fills it from the unchanged profile, and continues once. This
explicit additional-information response is distinct from an unanswered OTP
request; repeated or unknown alerts do not trigger an automatic retry loop.
The same continuation works after a manual Send OTP recovery click.

The observed page validates on OTP input and reveals a separate login Submit
button after acceptance. The watcher follows that scoped control, waits through
visible OTP errors, and does not click unrelated Submit buttons. Diagnostics
retain only request-seen, cross-origin and failure flags plus an HTTP status;
raw URLs, request/response bodies, phone numbers and OTPs are excluded.

## Scope and actual validation

| Website / action | Implemented | Live validation boundary |
| --- | --- | --- |
| TN Horticulture kit | Dedicated observed controls, cascading location options, photo, one free kit, one submit attempt | Public form inspected; real receipt format unverified |
| AGRISNET / Uzhavan | Exact bilingual label matching, department/scheme/input/component, residence and land sections, farmer photo, bank details, advancement and submission | Official public field list checked; interactive browser returned HTTP 502 connection refused. No live full application test |
| Welfare board registration | Observed mobile/ration/OTP entry, automatic code verification handoff, supported profile/doc fields, multi-step advancement and one submit attempt | Entry form inspected; OTP-protected membership form is not inspected or certified |
| Welfare board member claim | Observed username/password/CAPTCHA entry, encrypted optional password, automatic login handoff, exact chosen claim link, supported fields/uploads/submission | Login inspected; dashboard and actual claim forms are not inspected or certified |

These are three websites and four separate actions. The welfare board action
must be chosen explicitly: new membership registration is not a welfare claim.
Officer approval is needed before registered-member claims. A registration
receipt does not prove approval. Horticulture collection and agricultural subsidy
allocation also remain department processes.

**This release is not a claim that three live end-to-end integrations have been
verified.** The two new adapters are conservative runtime field-matching workflows.
Offline tests use representative forms, including simulated authentication,
not authenticated copies of the government's protected forms. Unknown fields,
custom controls, new declarations, unavailable dropdown options, or changed
navigation can require assistance. A genuine applicant session is needed to
validate and extend those portal-specific steps.

## Missing-field fixes in 1.0.7

Generic Bootstrap dropdown aria labels no longer override visible field labels.
Residence district/block/village and land district/taluk/village have separate
identities. Old ambiguous mappings do not override recognized portal labels.
Radio options beside controls are matched, and the app rechecks earlier values
that a portal lookup may clear. These changes are tested against local fixtures
reproducing screenshot-observed layout patterns, not the live portal source.

If a saved dropdown choice does not match, **Applications** lists options read
from that form. Select the intended option and press **Resume**. Parent selections
load dependent scheme/location options on Resume. Each choice stays with this
application; it does not overwrite your common profile. Unsupported fields still
pause rather than inventing a value. Portal choices must still match on the next
browser pass; stale options cannot force a submission.

After installing, save the new same-location answer. Cancel only an old
**unsubmitted** preparation and start again to use the updated profile. Never
restart an application with a submission attempt or receipt.

## Missing information and manual actions

- The app lists exact unresolved labels. Complete the field in the official
  browser, or map it to an existing confirmed profile/document field, then Resume.
- No missing fact is invented, and no default is substituted for an unavailable
  scheme, land location, radio choice or benefit.
- Complete any declaration personally. A newly encountered declaration is not
  silently accepted. Such a portal does not yet meet the OTP/CAPTCHA-only goal.
- If saved profile/documents change during preparation, cancel the unsubmitted
  job and start again. This prevents mixed old/new data across pages.
- A claim link must exactly match **Welfare claim link text** and stay on the
  approved welfare-board host. Registration does not launch a later claim by itself.

## Privacy and recovery

Saved credentials use the existing encrypted local store, separate from the
citizen profile. They never enter AI prompts or API read responses. OTP/CAPTCHA
values are not saved. Authentication screenshots are not retained. A maximum
of five distinct user-entered codes are submitted during one waiting session;
additional attempts require the user's own action or Resume.

Each final click is recorded before dispatch. A timeout, crash or missing
reference after that point enters receipt-only recovery. Restarting the app
requires Resume; it cannot silently submit a previous uncertain job again.

## Completing live validation

Use a consenting, eligible applicant with genuine documents and an intended
application. Confirm the actual post-login labels, document size/format limits,
claim selection and acknowledgement against the relevant official portal.
Never submit invented applicants as a test. An unreachable portal or a closed
application period is an external blocker; reinstalling this app does not fix it.

## Duplicate portal options in 1.0.8

Portal choices now save the submitted option ID as well as the visible label.
Identical labels with different IDs are shown with an ID prefix. The chosen
value stays visible in Applications after saving and after reloading the app.
Click Resume to fill the saved selections and load any dependent options.

The filler checks the live label, ID, form path and parent selection IDs before
using a saved choice. A changed scheme or location invalidates dependent choices;
the app pauses for a current selection rather than reusing an old child ID.
Existing text-only choices work only when they match a single current option.
After upgrading an old preparation, Resume once to refresh its option inventory.
No cancellation or re-entry is needed if the profile and documents are unchanged.
