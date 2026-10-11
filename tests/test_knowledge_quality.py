import pytest
from app.knowledge.quality import evidence_state,parse_sufficiency

@pytest.mark.parametrize(('result','state'),[
 ({'ok':False,'data':None,'error_code':'knowledge_unavailable'},'unavailable'),
 ({'ok':True,'data':[]},'empty'),
 ({'ok':True,'data':[{'excerpt':'evidence'}]},'present'),
])
def test_evidence_quality_distinguishes_unavailable_empty_and_present(result,state):
 assert evidence_state(result)==state

def test_sufficiency_requires_a_well_formed_boolean_verdict():
 assert parse_sufficiency('{"sufficient":true,"reason":"direct_fact"}')[0] is True
 assert parse_sufficiency('{"sufficient":false,"reason":"missing_period"}')[0] is False
 for value in (None,'not json','{"sufficient":"yes"}','{"sufficient":false,"reason":[]}'):
  with pytest.raises(ValueError):parse_sufficiency(value)
