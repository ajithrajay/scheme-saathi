import html

STYLE = """<style>body{font:17px system-ui;background:#edf3f9;color:#173246;margin:0;padding:32px}main{max-width:680px;margin:auto;background:white;padding:32px;border-radius:18px;border:1px solid #cdd9e3}label{display:block;margin:18px 0 6px}input,select{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #a6b8c7;border-radius:7px}button{background:#075e61;color:white;border:0;border-radius:8px;padding:13px 22px;font:inherit;margin-top:24px}aside{background:#fff2d3;border:1px solid #e2c574;padding:12px;border-radius:8px}h1{font-size:28px}.step{color:#4e6e80}dl{line-height:2}code{font-size:20px}</style>"""

def page(content):
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>SchemeSaathi practice portal</title>' + STYLE + '</head><body><main><aside>LOCAL PRACTICE PORTAL · No government application or payment</aside>' + content + '</main></body></html>'

def profile_form(job):
    return page(f'''<p class="step">Step 1 of 3 · Student details</p><h1>Practice Education Grant</h1><form action="/demo/details" method="post"><input type="hidden" name="job" value="{html.escape(job)}"><label for="full_name">Full name</label><input id="full_name" name="full_name" required><label for="state">State</label><select id="state" name="state" required><option value="">Select state</option><option>Tamil Nadu</option><option>Karnataka</option></select><label for="age">Age</label><input id="age" name="age" type="number" min="18" max="30" required><label for="annual_household_income_inr">Annual household income (₹)</label><input id="annual_household_income_inr" name="annual_household_income_inr" type="number" min="0" max="250000" required><button type="submit">Next</button></form>''')

def upload_form(job):
    return page(f'''<p class="step">Step 2 of 3 · Documents</p><h1>Add your study certificate</h1><form action="/demo/document" method="post" enctype="multipart/form-data"><input type="hidden" name="job" value="{html.escape(job)}"><label for="study_certificate">Study certificate</label><input type="file" id="study_certificate" name="study_certificate" accept=".pdf,.png,.jpg,.txt" required><button type="submit">Save and next</button></form>''')

def review_form(job, details):
    rows = ''.join(f'<dt>{html.escape(k.replace("_", " "))}</dt><dd>{html.escape(str(v))}</dd>' for k, v in details.items() if k != 'job')
    return page(f'''<p class="step">Step 3 of 3 · Review</p><h1>Review practice application</h1><dl>{rows}</dl><p>Your test document has been received.</p><form action="/demo/submit" method="post"><input type="hidden" name="job" value="{html.escape(job)}"><button type="submit">Submit application</button></form>''')

def acknowledgement(receipt):
    return page(f'<h1>Application submitted successfully</h1><p>Practice acknowledgement number:</p><code id="ack-code">{html.escape(receipt)}</code><p>This receipt is fictional and exists only in SchemeSaathi.</p>')
