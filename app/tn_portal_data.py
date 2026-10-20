"""Citizen-supplied fields and fixed official destinations. No LLM routing."""
EXTRA_FIELDS = {
    'agri_department': ('Agriculture department (exact portal option)', 'துறை'),
    'agri_scheme': ('Agricultural subsidy scheme (exact portal option)', 'வேளாண் திட்டம்'),
    'agri_input_type': ('Agricultural input type (exact portal option)', 'இடுபொருள் வகை'),
    'agri_component': ('Agricultural component (exact portal option)', 'இனம்'),
    'agri_social_status': ('Agricultural social status (exact portal option)', 'சமூக நிலை'),
    'land_district': ('Land district', 'நிலம் உள்ள மாவட்டம்'),
    'land_taluk': ('Land taluk', 'நிலம் உள்ள வட்டம்'),
    'land_village': ('Land village', 'நிலம் உள்ள கிராமம்'),
    'land_survey_number': ('Survey / subdivision numbers', 'சர்வே / உட்பிரிவு எண்கள்'),
    'cultivable_crop': ('Cultivable crop (exact portal option)', 'சாகுபடி பயிர்'),
    'ration_card_number': ('Ration / smart card number', 'குடும்ப அட்டை எண்'),
    'worker_board': ('Welfare board (exact portal option)', 'நலவாரியம்'),
    'occupation': ('Nature of work (exact portal option)', 'தொழில்'),
    'worker_registration_number': ('Approved worker registration / username', 'பதிவு எண்'),
    'worker_claim_name': ('Welfare claim link text (exact portal name)', 'உதவித் திட்டத்தின் பெயர்'),
    'nominee_name': ('Nominee full name', 'நியமனதாரர் பெயர்'),
    'nominee_relationship': ('Nominee relationship', 'நியமனதாரர் உறவு'),
    'marital_status': ('Marital status (exact portal option)', 'திருமண நிலை'),
}
EXTRA_FIELDS = {key: {'en': en, 'ta': ta, 'type': 'text', 'private': True} for key, (en, ta) in EXTRA_FIELDS.items()}
EXTRA_FIELDS.update({
    'land_ownership': {'en': 'Land ownership', 'ta': 'நில உரிமை', 'type': 'select', 'options': ['Owner','Cultivator'], 'private': True},
    'land_area_acres': {'en': 'Land area (acres)', 'ta': 'நிலப்பரப்பு ஏக்கர்', 'type': 'number', 'min': 0.001, 'private': True},
    'land_address_same': {'en': 'Residence and land location are the same?', 'ta': 'இருப்பிடமும் நில முகவரியும் ஒன்றா?', 'type': 'boolean', 'private': True},
})
DOCUMENTS = [
    {'id': 'occupation_certificate', 'name': 'Certified occupation / employment proof', 'name_ta': 'பணிச்சான்றிதழ்'},
    {'id': 'identity_proof', 'name': 'Identity proof (welfare board)', 'name_ta': 'அடையாளச் சான்றிதழ்'},
    {'id': 'ration_card', 'name': 'Ration / smart card', 'name_ta': 'குடும்ப அட்டை'},
    {'id': 'nominee_proof', 'name': 'Nominee relationship proof', 'name_ta': 'வாரிசு உறவுச் சான்று'},
]
COMMON = ['full_name','aadhaar_number','phone','gender','district','bank_account','ifsc']
PORTALS = {
 'agrisnet': {
  'id':'tn-agrisnet-request', 'name':'AGRISNET / Uzhavan subsidy request',
  'adapter':'tn-agrisnet-v1',
  'portal_url':'https://www.tnagrisnet.tn.gov.in/KaviaDP/scheme_register/uzhavan',
  'allowed_hosts':['www.tnagrisnet.tn.gov.in','tnagrisnet.tn.gov.in'],
  'required': COMMON + ['relation_name','block','village','agri_department','agri_scheme','agri_input_type','agri_component','agri_social_status','land_ownership','land_address_same','land_district','land_taluk','land_village','land_survey_number','land_area_acres','cultivable_crop'],
  'documents':['beneficiary_photo'],
  'scope':'Advance registration for a selected agricultural subsidy. Department allocation and approval are separate.',
  'validation':'Public field list checked. Interactive portal was unreachable here; live submission is unverified.'},
 'worker-registration': {
  'id':'tn-worker-registration', 'name':'TN Welfare Board — new worker registration',
  'adapter':'tn-worker-registration-v1',
  'portal_url':'https://tnuwwb.tn.gov.in/applications/register',
  'allowed_hosts':['www.tnuwwb.tn.gov.in','tnuwwb.tn.gov.in'],
  'tracking_url':'https://tnuwwb.tn.gov.in/applications/status',
  'required': COMMON + ['ration_card_number','date_of_birth','relation_name','address','pincode','worker_board','occupation','nominee_name','nominee_relationship','marital_status'],
  'documents':['occupation_certificate','identity_proof','ration_card','bank_passbook','aadhaar_document','nominee_proof'],
  'scope':'Membership registration only. An officer must approve membership before registered-member benefit claims.',
  'validation':'Live registration entry inspected. OTP-protected forms and official receipt are unverified.'},
 'worker-claim': {
  'id':'tn-worker-claim', 'name':'TN Welfare Board — registered-member claim',
  'adapter':'tn-worker-claim-v1',
  'portal_url':'https://tnuwwb.tn.gov.in/users/login',
  'allowed_hosts':['www.tnuwwb.tn.gov.in','tnuwwb.tn.gov.in'],
  'required':['worker_registration_number','worker_claim_name','full_name','phone'],
  'documents':[],
  'scope':'A chosen benefit claim for an already approved member. Supply the exact claim title and its supporting documents.',
  'validation':'Live password/CAPTCHA login inspected. Authenticated claim forms and official receipt are unverified.'},
}
BY_ADAPTER = {v['adapter']: v for v in PORTALS.values()}
