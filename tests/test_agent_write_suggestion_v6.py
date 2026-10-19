"""An ungranted write call never executes and never becomes a card; the refusal speaks plain words."""
import asyncio,json
from app.agent_workflow import direct,consent
from app.agent_workflow.state import AgentRuntimeContext

def call(name,args):return {'tool_calls':[{'id':name,'function':{'name':name,'arguments':json.dumps(args)}}]}

def run(monkeypatch,question,*,steps=(),owner=True):
    monkeypatch.setattr(direct,'owner_credential_matches',lambda _:owner)
    monkeypatch.setattr(consent,'owner_credential_matches',lambda _:owner)
    from app.database import Base,engine
    Base.metadata.create_all(engine)
    steps=iter(steps);writes=[]
    async def decide(messages,schemas):return next(steps,{'content':'好的。'})
    async def execute(name,args):
        if name in {'add_to_research_queue','analyze'}:writes.append(name)
        return {'ok':True,'data':[]}
    ctx=AgentRuntimeContext('custom','test','dummy',identity_credential='test',conversation_id='c6',
        classifier=lambda _:{'category':'D','requested_actions':[]},decider=decide,executor=execute)
    async def emit(_):pass
    return asyncio.run(direct.run_direct_turn(question,ctx,emit=emit)),writes

def test_an_ungranted_write_call_is_refused_in_plain_words(monkeypatch):
    result,writes=run(monkeypatch,'看看 AMD',steps=[call('add_to_research_queue',{'symbol':'AMD'})])
    assert not writes and result['pending_request'] is None
    assert result['audit']['write_outcome']=='refused:write_tool_not_granted'
    assert '操作未执行：这项写入不是你在这句话里明确要求的' in result['answer']

def test_an_ungranted_write_the_user_negated_is_reported_as_negated(monkeypatch):
    result,writes=run(monkeypatch,'先别加入 AMD，只看看',steps=[call('add_to_research_queue',{'symbol':'AMD'})])
    assert not writes and result['pending_request'] is None
    assert result['audit']['write_outcome']=='refused:write_negated'

def test_a_guest_write_call_is_still_refused(monkeypatch):
    result,writes=run(monkeypatch,'帮我分析这份材料',owner=False,steps=[call('analyze',{'symbol':'AMD','source_id':'11111111-1111-4111-8111-111111111111'})])
    assert not writes and result['pending_request'] is None
    assert result['audit']['write_outcome']=='refused:owner_login_required'

def test_an_agent_suggestion_card_names_the_operation_not_the_tool(monkeypatch):
    monkeypatch.setattr(direct,'owner_credential_matches',lambda _:True)
    monkeypatch.setattr(consent,'owner_credential_matches',lambda _:True)
    from app.database import Base,engine
    Base.metadata.create_all(engine)
    async def emit(_):pass
    turn=direct.Turn('看看 AMD',AgentRuntimeContext('custom','test','dummy',identity_credential='test',conversation_id='c6b'),emit,[],{})
    result=asyncio.run(turn.request_action({'tool':'initialize_prices','arguments':{'symbol':'AMD','days':30},'reason':'需要历史行情'}))
    assert result['ok'] and turn.pending['display']['title']=='这是 Agent 的建议，不是你的要求：初始化历史行情'
