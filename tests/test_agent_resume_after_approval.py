"""After the user approves a card, the turn finishes what the user originally asked for."""
import asyncio,json,uuid
import pytest
from app.database import Base,engine
from app.agent_workflow.models import AgentConsent
from app.agent_workflow import direct,consent,tools
from app.agent_workflow.state import AgentRuntimeContext

QUESTION='抓取 MSFT 最新 10-K 然后告诉我营收'
FIGURE={'source_id':str(uuid.uuid4()),'symbol':'MSFT','form':'10-K','source_type':'reported_figure','title':'MSFT 10-K · 营业收入 FY2026',
        'filed_at':'2026-07-29','excerpt':'营业收入（Revenue）FY2026：331,839,000,000 USD。'}

@pytest.fixture
def owned(monkeypatch,disposable_database):
    Base.metadata.create_all(engine,tables=[AgentConsent.__table__])
    for module in (direct,consent,tools):monkeypatch.setattr(module,'owner_credential_matches',lambda c:c=='owner')
    return 'owner'

def call(name,**args):return {'tool_calls':[{'id':uuid.uuid4().hex,'function':{'name':name,'arguments':json.dumps(args)}}]}

def approve(monkeypatch,*,outcome,steps,operation=None):
    conversation='resume-'+uuid.uuid4().hex[:8]
    operation=operation or {'name':'fetch_sec','arguments':{'symbol':'MSFT','form':'10-K'}}
    card=consent.propose('owner',conversation,[operation],{'title':'确认登记并拉取 SEC 官方原文'},question=QUESTION)
    monkeypatch.setattr(tools,'execute_costly',lambda op,key,context:dict(outcome))
    async def offline(*_a,**_k):raise RuntimeError('no provider in tests')
    monkeypatch.setattr(direct,'decision_request',offline)
    steps=iter(steps);decided=[];reads=[]
    async def decide(messages,schemas):decided.append(messages);return next(steps,{'content':'没有可核验资料。'})
    async def execute(name,args):reads.append(name);return {'ok':True,'data':[FIGURE] if name=='query_financials' else []}
    ctx=AgentRuntimeContext('custom','test','dummy',owner_hash='o',conversation_id=conversation,identity_credential='owner',
        classifier=lambda _:pytest.fail('a confirmation is not reclassified'),decider=decide,executor=execute)
    async def emit(_):pass
    state={'pending_request':card,'requested_pending_id':card['id'],'previous_category':'D',
           'previous_scope':{'symbol':'MSFT','requested_actions':['fetch_sec'],'days_back':None}}
    token=card['choices'][0]['value']
    result=asyncio.run(direct.run_direct_turn(token,ctx,emit=emit,state=state))
    return result,decided,reads

def test_a_synchronous_write_continues_into_the_original_request(owned,monkeypatch):
    result,decided,reads=approve(monkeypatch,outcome={'ok':True,'status':'completed','title':'MSFT 10-K','symbol':'MSFT'},
        steps=[call('query_financials',symbol='MSFT',metrics=['revenue']),{'content':'MSFT FY2026 营业收入为 331,839,000,000 美元 [1]。'}])
    assert reads==['query_financials'] and result['status']=='succeeded'
    assert '已完成你确认的操作' in result['answer'] and '331,839,000,000' in result['answer']
    assert any('用户已批准并完成了这些操作' in m['content'] for m in decided[0] if m['role']=='system')
    assert any(m['role']=='user' and m['content']==QUESTION for m in decided[0])

def test_the_continuation_never_proposes_the_executed_write_again(owned,monkeypatch):
    result,decided,_=approve(monkeypatch,outcome={'ok':True,'status':'completed','title':'MSFT 10-K','symbol':'MSFT'},
        steps=[call('fetch_sec',symbol='MSFT',form='10-K'),{'content':'已经拉取完成。'}])
    assert result['pending_request'] is None and len(decided)==2
    assert '操作未执行' not in result['answer']

def test_the_continuation_grants_no_new_write(owned,monkeypatch):
    result,_,_=approve(monkeypatch,outcome={'ok':True,'status':'completed','title':'MSFT 10-K','symbol':'MSFT'},
        steps=[call('import',symbol='MSFT',source_id=FIGURE['source_id']),call('request_action',tool='index',arguments={},reason='建索引'),{'content':'已经拉取完成。'}])
    assert result['pending_request'] is None

def test_a_queued_job_keeps_the_execution_report(owned,monkeypatch):
    result,decided,_=approve(monkeypatch,outcome={'ok':True,'status':'queued','job_id':'job-1','action':'analyze'},steps=[],
        operation={'name':'analyze','arguments':{'symbol':'MSFT','source_id':FIGURE['source_id'],'source_type':'official_filing'}})
    assert not decided or all('用户已批准并完成了这些操作' not in m['content'] for m in decided[0] if m['role']=='system')
    assert '好了吗' in result['answer']
