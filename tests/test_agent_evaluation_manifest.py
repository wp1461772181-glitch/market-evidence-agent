import json
from pathlib import Path

def test_checked_in_acceptance_corpus_contains_all_33_required_scenarios():
    path=Path(__file__).resolve().parents[1]/'scripts/evaluation/cases/agent-routing-v3.jsonl'
    cases=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(cases)>=50 and len({c['id'] for c in cases})==len(cases)
    assert {c['spec_case'] for c in cases if c['spec_case'] is not None}==set(range(1,34))
    assert all(c['setup']['identity'] in {'owner','guest'} and c['utterance'] and c['expect'] for c in cases)

def test_controlled_tool_probe_retains_native_thinking_protocol():
    import runpy
    root=Path(__file__).resolve().parents[1]/'scripts/evaluation'
    fixture=runpy.run_path(str(root/'routing_fixtures.py'))['Fixture']
    cases=[json.loads(line) for line in (root/'cases/agent-routing-v3.jsonl').read_text().splitlines()]
    for case in cases:
        if case['setup']['scenario'] not in {'limit','no_progress','repair'}:continue
        message=fixture(case).forced_decision([{'function':{'name':'web_research'}}])
        assert isinstance(message.get('reasoning_content'),str) and message['tool_calls']

def test_injected_probe_does_not_consume_real_model_token_budget(monkeypatch):
    import asyncio,runpy
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/evaluation/routing_acceptance.py'))
    selected={'reasoning_content':'','content':'controlled'}
    async def response(*_,**__):return runner['controlled_response'](selected)
    monkeypatch.setattr(direct,'decision_request',response)
    turn=direct.Turn('probe',AgentRuntimeContext('custom','test','synthetic'),lambda _:asyncio.sleep(0),[],{})
    async def exercise():
        for _ in range(8):await turn.model([{'role':'user','content':'probe'}],[])
    asyncio.run(exercise())
    assert turn.audit['token_usage']==0 and turn.audit['usage_mode']=='actual'

def test_v5_case_policy_changes_are_explicit_and_healthy_control_terminates():
    import runpy
    root=Path(__file__).resolve().parents[1]/'scripts/evaluation'
    old=[json.loads(line) for line in (root/'cases/agent-routing-v3-confirmed.jsonl').read_text().splitlines()]
    new=[json.loads(line) for line in (root/'cases/agent-routing-v5.jsonl').read_text().splitlines()]
    assert len(old)==len(new)==50
    # R21: multi-company scope.  R24: answer wording.  R22: public web research is now attempted
    # once instead of retried three times (one slow search used to silence a whole turn), so the
    # case asserts that a failed search is reported as tool_failures rather than as empty evidence.
    assert {a['id'] for a,b in zip(old,new) if a!=b}=={'R21','R22','R24'}
    fixture=runpy.run_path(str(root/'routing_fixtures.py'))['Fixture'](new[20])
    schemas=[{'function':{'name':'query_stock_prices'}}]
    calls=[fixture.forced_decision(schemas) for _ in range(9)]
    assert all(c.get('tool_calls') for c in calls[:8])
    assert 'tool_calls' not in calls[8] and '[8]' in calls[8]['content']
