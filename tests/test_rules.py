import json
from copy import deepcopy
from datetime import date
from pathlib import Path
import pytest
from app.models import Rule, clean_facts
from app.rules import evaluate_rule, assess, validate_rule, window_status

CATALOGUE = json.loads((Path(__file__).parents[1]/'app/data/catalogue.json').read_text(encoding='utf-8'))
DEMO = next(s for s in CATALOGUE['schemes'] if s['is_demo'])
FACTS = dict(state='Tamil Nadu', age=20, annual_household_income_inr=180000, higher_education_enrolled=True)

@pytest.mark.parametrize('income,expected',[(0,'MATCH'),(249999,'MATCH'),(250000,'MATCH'),(250001,'NO_MATCH'),(None,'NEEDS_INFORMATION')])
def test_income_boundary(income,expected):
    r=assess(DEMO,{**FACTS,'annual_household_income_inr':income},['study_certificate'])
    assert r['status']==expected

@pytest.mark.parametrize('age,expected',[(17,'NO_MATCH'),(18,'MATCH'),(30,'MATCH'),(31,'NO_MATCH')])
def test_age_range(age,expected):
    assert assess(DEMO,{**FACTS,'age':age},[])['status']==expected

@pytest.mark.parametrize('op,states,expected',[
 ('ALL',[True,None],'UNKNOWN'),('ALL',[False,None],'FAIL'),('ALL',[True,True],'PASS'),
 ('ANY',[True,None],'PASS'),('ANY',[False,None],'UNKNOWN'),('ANY',[False,False],'FAIL'),
 ('NOT',[True],'FAIL'),('NOT',[False],'PASS'),('NOT',[None],'UNKNOWN')])
def test_three_valued_logic(op,states,expected):
    children=[Rule(id=str(i),label='test',field=str(i),op='eq',value=True) for i in range(len(states))]
    rule=Rule(id='group',label='test',op=op,children=children)
    assert evaluate_rule(rule,{str(i):v for i,v in enumerate(states)})['state']==expected

def test_policy_and_readiness_are_independent():
    scheme=deepcopy(DEMO)
    scheme['coverage_complete']=False
    result=assess(scheme,FACTS,[])
    assert result['preliminary_state']=='PASS'
    assert result['status']=='REVIEW_REQUIRED'
    assert result['document_status']=='INCOMPLETE'
    scheme['coverage_complete']=True
    scheme['window_end']='2025-01-01'
    result=assess(scheme,FACTS,[],date(2026,9,29))
    assert result['status']=='MATCH' and result['window_status']=='CLOSED'

def test_unknown_window_never_becomes_open():
    assert window_status({"window_status":"OPEN"})=='UNKNOWN'

def test_expired_review_and_source_change_block_match():
    for change in [{'review_valid_until':'2000-01-01'},{'policy_changed':True}]:
        assert assess({**DEMO,**change},FACTS,[])['status']=='REVIEW_REQUIRED'

def test_sc_st_relaxation():
    scheme=deepcopy(next(s for s in CATALOGUE['schemes'] if s['id']=='nmmss'))
    scheme.update(coverage_complete=True,review_status='REVIEWED',review_valid_until='2099-12-31')
    assert assess(scheme,{'annual_household_income_inr':350000,'class_7_percentage':50,'category':'SC'},[])['status']=='MATCH'
    assert assess(scheme,{'annual_household_income_inr':350000,'class_7_percentage':50,'category':'General'},[])['status']=='NO_MATCH'

def test_irrelevant_or_branch_does_not_ask_more():
    scheme=next(s for s in CATALOGUE['schemes'] if s['id']=='pudhumai-penn')
    facts={'gender':'female','school_type':'government','studied_6_to_12_continuously':True,'higher_education_enrolled':True}
    assert 'school_medium' not in assess(scheme,facts,[])['missing_fields']

@pytest.mark.parametrize('facts',[{'age':True},{'age':-1},{'age':19.2},{'higher_education_enrolled':'yes'},{'annual_household_income_inr':float('nan')},{'annual_household_income_inr':float('inf')},{'school_type':'guessed'},{'random_field':12}])
def test_invalid_profile_facts_rejected(facts):
    with pytest.raises(ValueError):clean_facts(facts)

def test_unknown_source_rule_rejected():
    rule=Rule(id='rule',label='Bad rule',op='eq',field='gender',value='female',source_id='invented',source_section='section')
    with pytest.raises(ValueError):validate_rule(rule,{'known'})

def test_unknown_does_not_mean_false():
    r=Rule(id='r',label='Education',op='eq',field='higher_education_enrolled',value=True)
    assert evaluate_rule(r,{})['state']=='UNKNOWN'
    assert evaluate_rule(r,{'higher_education_enrolled':False})['state']=='FAIL'

def test_draft_policy_allows_preparation_but_blocks_submission():
    draft = {**DEMO, 'coverage_complete': False, 'review_status': 'DRAFT',
             'window_status': 'UNKNOWN', 'window_start': None, 'window_end': None}
    result = assess(draft, {**FACTS, 'full_name': 'Fictional Student'}, ['study_certificate'])
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['can_prepare'] is True
    assert result['can_submit'] is False
    assert result['preparation_blockers'] == []
    assert len(result['submission_blockers']) == 2

@pytest.mark.parametrize('facts,documents,changes', [
    ({**FACTS, 'age': 17}, ['study_certificate'], {}),
    ({**FACTS, 'higher_education_enrolled': None}, ['study_certificate'], {}),
    (FACTS, [], {}),
    (FACTS, ['study_certificate'], {'window_end': '2000-01-01'}),
])
def test_preparation_cannot_ignore_failed_missing_or_closed_conditions(facts, documents, changes):
    scheme = {**DEMO, 'coverage_complete': False, **changes}
    result = assess(scheme, {**facts, 'full_name': 'Fictional Student'}, documents)
    assert not result['can_prepare'] and not result['can_submit']
    assert result['preparation_blockers']
