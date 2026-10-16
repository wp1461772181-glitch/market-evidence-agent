import json
import pytest
from scripts.evaluation.routing_fixtures import load_issuer_records

@pytest.mark.parametrize('dictionary',[False,True])
def test_official_issuer_fixture_preserves_list_or_raw_sec_response(tmp_path,dictionary):
    rows=[{'title':'INTEL CORP','ticker':'INTC'},{'title':'MICRON TECHNOLOGY INC','ticker':'MU'}]
    path=tmp_path/'issuers.json'
    path.write_text(json.dumps(dict(enumerate(rows)) if dictionary else rows))
    assert load_issuer_records(path)==rows

def test_invalid_issuer_fixture_fails_before_any_model_request(tmp_path):
    path=tmp_path/'issuers.json';path.write_text('[]')
    with pytest.raises(ValueError,match='invalid_official_issuer_fixture'):load_issuer_records(path)
