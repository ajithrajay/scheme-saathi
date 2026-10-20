"""Bundled fictional data, isolated from the citizen workspace."""
import hashlib
import json
from datetime import date
from pathlib import Path
from .models import clean_facts
from .store import now

ASSETS = Path(__file__).parent / 'data' / 'resume_demo'

def demo_facts():
    facts = json.loads((ASSETS/'profile.json').read_text(encoding='utf-8'))
    # Keep the college persona twenty years old as the demo is reused.
    today=date.today()
    birth=date(today.year-20,2,15)
    if (today.month,today.day)<(2,15):birth=date(today.year-21,2,15)
    facts['date_of_birth']=birth.isoformat()
    financial_year=today.year if today.month>=4 else today.year-1
    facts['income_financial_year']=f'{financial_year}-{str(financial_year+1)[-2:]}'
    return clean_facts(facts)

def document_catalogue():
    return json.loads((ASSETS/'documents.json').read_text(encoding='utf-8'))

def seed_demo(store):
    """Idempotent; fill demo gaps without changing citizen data or edited demo facts."""
    old=store.get('profile','demo',{})
    previous=old.get('facts',{})
    if previous.get('full_name')=='Kavya Demo' and not old.get('is_synthetic'):
        previous={}  # Migrate the earlier built-in minimal fictional persona.
    facts={**demo_facts(),**previous}
    store.put('profile','demo',{'facts':facts,'confirmed_at':old.get('confirmed_at') or now(),
                              'is_synthetic':True,'label':'Fictional resume demonstration'})
    existing={d['document_type']:d for d in store.all('document') if d.get('is_demo')}
    for document in document_catalogue():
        previous_document=existing.get(document['id'])
        if previous_document and previous_document.get('filename')!='FICTIONAL_study_certificate.txt':continue
        content=(ASSETS/'documents'/document['filename']).read_bytes()
        id=previous_document['id'] if previous_document else 'resume-demo-'+document['id']
        store.put_blob(id,store.cipher.encrypt(content))
        store.put('document',id,{'id':id,'document_type':document['id'],'filename':document['filename'],
            'mime_type':document['mime_type'],'size':len(content),'blob':id,'is_demo':True,'is_synthetic':True,
            'uploaded_at':now(),'sha256':hashlib.sha256(content).hexdigest()})
    return {'facts':facts,'is_synthetic':True,'document_count':len(document_catalogue())}

def demo_inputs(store):
    return store.get('profile','demo',{'facts':{}})['facts'], {d['document_type']:d for d in store.all('document') if d.get('is_demo')}

def synthetic_facts(facts):
    return (facts.get('full_name')=='Kavya Demo' or
            str(facts.get('email','')).lower().endswith('@example.invalid') or
            facts.get('aadhaar_number')=='000000000000' or
            any(str(value).startswith('DEMO-') for value in facts.values()))
