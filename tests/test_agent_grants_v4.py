import asyncio,json
import pytest
from app.agent_workflow import direct,tools,consent
from app.agent_workflow.state import AgentRuntimeContext

def call(name,args):return {'tool_calls':[{'id':name,'function':{'name':name,'arguments':json.dumps(args)}}]}

def run(monkeypatch,question,*,grants=(),steps=(),owner=True):
    monkeypatch.setattr(direct,'owner_credential_matches',lambda _:owner)
    monkeypatch.setattr(consent,'owner_credential_matches',lambda _:owner)
    from app.database import Base,engine
    Base.metadata.create_all(engine)
    steps=iter(steps);exposed=[];writes=[]
    async def decide(messages,schemas):
        exposed.append({s['function']['name'] for s in schemas})
        return next(steps,{'content':'没有可核验资料。'})
    async def execute(name,args):
        if name=='add_to_research_queue':writes.append((name,args));return {'ok':True,'data':[]}
        return {'ok':True,'data':[]}
    ctx=AgentRuntimeContext('custom','test','dummy',identity_credential='test',classifier=lambda _:{'category':'D','requested_actions':list(grants)},decider=decide,executor=execute)
    async def emit(_):pass
    result=asyncio.run(direct.run_direct_turn(question,ctx,emit=emit))
    return result,exposed,writes

def test_ungranted_native_write_rejected_even_when_model_ignores_schema(monkeypatch):
    result,exposed,writes=run(monkeypatch,'看看AMD',steps=[call('add_to_research_queue',{'symbol':'AMD'})])
    assert not writes and 'add_to_research_queue' not in exposed[0]
    assert result['audit']['write_outcome']=='refused:write_tool_not_granted'
    assert 'request_action' in result['answer'] and '未执行' in result['answer']

def test_only_classified_server_write_is_registered_and_proposed(monkeypatch):
    result,exposed,writes=run(monkeypatch,'把AMD纳入我的研究清单',grants=['add_to_research_queue'],steps=[call('add_to_research_queue',{'symbol':'AMD'})])
    assert not writes and result['pending_request']['reason']=='write_confirmation'
    assert 'add_to_research_queue' in exposed[0] and 'fetch_sec' not in exposed[0]
    assert result['audit']['write_outcome']=='proposed'

def test_negation_is_only_a_veto_not_positive_verb_authorization(monkeypatch):
    result,_,writes=run(monkeypatch,'先别加入AMD，只比较',grants=['add_to_research_queue'],steps=[call('add_to_research_queue',{'symbol':'AMD'})])
    assert not writes and 'refused:' in result['audit']['write_outcome']

def test_unknown_requested_actions_are_discarded():
    from app.agent_workflow.routing import Routing
    value=Routing.model_validate({'category':'D','days_back':None,'requested_actions':['fetch_sec','invented_write','web_research']})
    assert value.requested_actions==['fetch_sec']

def test_guest_has_no_server_writes_or_request_action_but_local_queue_stays(monkeypatch):
    _,exposed,_=run(monkeypatch,'把AMD加入队列',grants=['add_to_research_queue','fetch_sec'],owner=False)
    assert 'request_action' not in exposed[0] and 'fetch_sec' not in exposed[0]
    assert 'add_to_research_queue' in exposed[0]

def test_no_card_write_result_includes_reason_not_zero_evidence(monkeypatch):
    result,_,_=run(monkeypatch,'拉取INTC财报',grants=['fetch_sec'])
    assert result['audit']['write_outcome'].startswith('refused:')
    assert '未执行' in result['answer'] and '下一步' in result['answer']

def test_failed_proposal_preserves_specific_next_step(monkeypatch):
    from app.database import Base,engine
    Base.metadata.create_all(engine)
    result,_,_=run(monkeypatch,'拉取INTC财报',grants=['fetch_sec'],steps=[call('fetch_sec',{'symbol':'INTC'})])
    assert result['audit']['write_outcome']=='refused:scan_official_first'
    assert 'scan_official_first' in result['answer'] and '扫描' in result['answer']

