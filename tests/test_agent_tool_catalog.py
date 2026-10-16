from app.agent_tool_catalog import definitions,schemas,validate,registered_tools
import pytest

def test_catalogue_contains_executable_handlers_with_typed_permission_metadata():
    from app.agent_tools import TOOL_REGISTRY
    policies=definitions()
    assert set(TOOL_REGISTRY)<=set(policies)
    assert {'ingest_web_source','add_to_research_queue','remove_from_research_queue','scan_official'}<=set(policies)
    for policy in policies.values():
        assert policy.args_schema and policy.description and policy.errors and policy.actionable_error
        assert policy.confirmation==(policy.level=='costly')
        assert not(policy.guest and policy.level=='costly')
    assert {s['function']['name'] for s in schemas(owner=True)}==set(policies)
    assert 'ingest_web_source' not in {s['function']['name'] for s in schemas(owner=False)}

@pytest.mark.parametrize('name',['change_login_code','read_api_key','delete_database','delete_conversation','configure_model'])
def test_prohibited_tools_cannot_register_or_execute(name):
    assert name not in definitions()
    with pytest.raises(ValueError):validate(name,{})

def test_unregistered_tool_cannot_be_exposed():
    class Tool:name='undeclared'
    assert registered_tools(owner=False,registry={'undeclared':Tool()})==[]

WRITE_ARGUMENTS={
 'fetch_sec':{'symbol':'AMD'},'refresh_sec':{'symbol':'AMD'},
 'analyze':{'symbol':'AMD','source_id':'11111111-1111-4111-8111-111111111111'},
 'import':{'symbol':'AMD','source_id':'11111111-1111-4111-8111-111111111111'},
 'index':{},'forecast':{'symbol':'AMD'},
 'ingest_web_source':{'url':'https://example.org/amd'},
 'initialize_prices':{'symbol':'AMD'},'scan_official':{'symbol':'AMD'},
 'start_historical_replay':{'symbol':'AMD','decision_date':'2025-01-02'},
 'revise_forecast':{'version_id':'11111111-1111-4111-8111-111111111111','source_refs':[{'source_type':'official_filing','source_id':'11111111-1111-4111-8111-111111111111'}]},
 'retry_document_index':{'document_id':'11111111-1111-4111-8111-111111111111'},
 'translate_content':{'kind':'analysis','record_id':'11111111-1111-4111-8111-111111111111'},
 'review_knowledge_candidate':{'candidate_id':'11111111-1111-4111-8111-111111111111','approved':True},
 'import_sources':{'source_type':'sec','source_ids':['11111111-1111-4111-8111-111111111111']},
}

def test_every_costly_tool_has_a_tokenless_security_probe():
    assert set(WRITE_ARGUMENTS)=={p.name for p in definitions().values() if p.level=='costly'}

@pytest.mark.parametrize('name',WRITE_ARGUMENTS)
def test_no_costly_tool_can_bypass_consent_through_public_dispatch(name,monkeypatch):
    import asyncio
    from app.agent_workflow import tools
    from app.agent_workflow.state import AgentRuntimeContext
    monkeypatch.setattr(tools,'owner_credential_matches',lambda _:True)
    context=AgentRuntimeContext('custom','test','dummy',identity_credential='owner',executor=lambda *_:pytest.fail('business I/O without a confirmation'))
    response=asyncio.run(tools.invoke(name,WRITE_ARGUMENTS[name],context))
    assert response['error_code']=='confirmation_required'
