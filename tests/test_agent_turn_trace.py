"""The graph a turn leaves behind: order, fan-out and join, failure, and redaction.

The trace is user-visible history, so these tests also pin what it must never carry: the
provider key, a card's control value, or a step that never closed.
"""
import asyncio,copy,json,uuid
import pytest
from fastapi import HTTPException
from app.agent_chat_store import owner_hash
from app.agent_workflow.direct import run_direct_turn
from app.agent_workflow.state import AgentRuntimeContext
from app.agent_workflow.trace import MAX_NODES,TurnTrace

@pytest.fixture(autouse=True)
def official_entity_metadata(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'AAPL','title':'APPLE INC'},{'ticker':'MU','title':'MICRON TECHNOLOGY INC'},{'ticker':'MSFT','title':'MICROSOFT CORP'}])
def call(name,**args):return {'tool_calls':[{'id':uuid.uuid4().hex,'function':{'name':name,'arguments':json.dumps(args)}}]}
def resolver(payload):
    """The same deterministic decision the other turn tests inject: a new question, no referents."""
    question=payload.get('question') or payload.get('message') or ''
    return {'relationship':'new','canonical_question':question,'referents':[],'question':question,'choices':[]}
# The reported figure tests/test_agent_financials.py uses, because it survives the evidence check.
ROW={'source_id':'00000000-0000-0000-0000-000000000001','symbol':'MSFT','form':'10-K','source_type':'reported_figure','title':'MSFT 10-K','filed_at':'2026-09-30','metric':'revenue','excerpt':'营业收入（Revenue）FY2026：331,839,000,000 USD。','url':'https://sec.example/msft'}

async def turn(question,*,route='B',scope=None,steps=(),answers=None,decider=None,api_key='dummy'):
    calls=[];queue=iter(steps);values=copy.deepcopy(answers or {})
    async def execute(name,args):
        calls.append((name,args))
        value=values.get(name,{'ok':True,'data':[]})
        if callable(value):value=value(args)
        if isinstance(value,Exception):raise value
        return copy.deepcopy(value)
    async def decide(*_):
        if decider is not None:return await decider()
        return next(queue,{'content':'已完成，来源见 [1]。'})
    context=AgentRuntimeContext('custom','test',api_key,owner_hash=owner_hash('guest'),conversation_id='c',
        classifier=lambda q:{'category':route,'requested_actions':[],**{k:v for k,v in (scope or {}).items() if k!='answer'}},
        decider=decide,executor=execute,resolver=resolver,
        answerer=lambda *_:(scope or {}).get('answer') or '已完成：已返回证据；未完成：剩余请求。')
    async def emit(_):pass
    result=await run_direct_turn(question,context,emit=emit,state={'requested_pending_id':None},messages=None)
    return result,calls

def run(*args,**kwargs):return asyncio.run(turn(*args,**kwargs))

def kinds(trace):return [n['kind'] for n in trace]

def test_tools_fan_out_from_the_step_that_called_them_and_join_the_next():
    events=[]
    async def emit(event):events.append(event)
    async def build():
        trace=TurnTrace(emit)
        await trace.mark('input',output={'question':'q'})
        resolve=await trace.begin('resolve')
        await trace.end(resolve)
        for key in ('a','b'):
            await trace.relay({'event':'tool_start','call_id':key,'name':'web_research'})
            await trace.relay({'event':'tool_result','call_id':key,'name':'web_research','result':{'ok':True,'data':[1]}})
        decide=await trace.begin('decide')
        await trace.end(decide,output={'answer':True})
        # A proposal only reports its result: it still becomes a node, hung off the step that asked.
        await trace.relay({'event':'tool_result','call_id':'ghost','name':'propose','result':{'ok':True,'data':[1]}})
        await trace.finish({'status':'succeeded'})
        return trace
    trace=asyncio.run(build());nodes=trace.nodes()
    by_kind={n['kind']:n for n in nodes}
    a,b=[n for n in nodes if n['kind']=='tool'][:2]
    assert a['after']==[by_kind['resolve']['id']]==b['after']
    assert by_kind['decide']['after']==[a['id'],b['id']]
    ghost=[n for n in nodes if n['kind']=='tool'][-1]
    assert ghost['name']=='propose' and ghost['after']==[by_kind['decide']['id']]
    sent=[[e for e in events if e.get('id')==n['id']] for n in nodes]
    assert all(len(seen)==2 for seen in sent)


def test_a_research_turn_is_drawn_from_question_to_answer():
    steps=[{'content':'MSFT FY2026 营业收入为 331,839,000,000 美元 [1]。'}]
    r,_=run('MSFT 营收',route='A',scope={'symbol':'MSFT','metrics':['营收']},steps=steps,
        answers={'query_financials':{'ok':True,'data':[ROW]}})
    trace=r['trace'];sequence=kinds(trace)
    order=['input','resolve','classify','tool','decide','verify','output']
    positions=[sequence.index(k) for k in order]
    assert positions==sorted(positions)
    assert not any(n['status']=='running' for n in trace)
    assert trace[-1]['kind']=='output' and trace[-1]['output']['status']=='succeeded'


def test_a_card_turn_ends_on_an_ask_node():
    r,_=run('Micron 最近新闻',route='D',scope={'company':'Micron'},
        steps=[call('ask_user',question='哪家公司？',options=['Apple','Micron'])])
    trace=r['trace']
    assert trace[-1]['kind']=='ask'
    assert trace[-1]['output']['question']=='哪家公司？'
    assert not any(n['status']=='running' for n in trace)


def test_a_failed_step_is_marked_and_the_trace_still_closes():
    async def refuse():raise HTTPException(401,'provider_auth_failed')
    r,_=run('Micron 最近新闻',route='D',scope={'company':'Micron'},decider=refuse)
    trace=r['trace'];decided=[n for n in trace if n['kind']=='decide'][-1]
    assert decided['status']=='failed' and decided['output']=={'error':'provider_auth_failed'}
    assert trace[-1]['kind']=='output' and trace[-1]['output']['status']=='failed'
    assert not any(n['status']=='running' for n in trace)


def test_the_trace_carries_no_key_and_no_card_value():
    secret='sk-test-PRIVATE-123'
    r,_=run('查一下 Micron 的新闻，key 是 '+secret,route='B',api_key=secret)
    trace=r['trace']
    assert secret not in json.dumps(trace,ensure_ascii=False)
    assert all(len(json.dumps(n['output'],ensure_ascii=False))<=400 for n in trace if n['output'] is not None)
    card,_=run('Micron 最近新闻',route='D',scope={'company':'Micron'},
        steps=[call('ask_user',question='哪家公司？',options=['Apple','Micron'])])
    value=card['pending_request']['choices'][0]['value']
    reply,_=run(value,route='B',scope={'answer':'苹果的商业模式包括硬件与服务。'})
    assert reply['trace'][0]['kind']=='input'
    assert not reply['trace'][0]['output']['question'].startswith('reply:')


def test_turns_without_tools_draw_a_single_answer_step():
    r,c=run('苹果运营模式',route='B',scope={'answer':'苹果的商业模式包括硬件与服务。'})
    assert kinds(r['trace'])==['input','resolve','classify','general','output']
    assert not c


def test_the_trace_is_bounded():
    async def emit(_):pass
    async def many(trace):
        for _ in range(70):await trace.begin('tool',name='query_stock_prices')
    trace=TurnTrace(emit)
    asyncio.run(many(trace))
    asyncio.run(trace.finish({'status':'succeeded'}))
    nodes=trace.nodes()
    assert len(nodes)==MAX_NODES and nodes[-1]['kind']=='output'
    assert nodes[-1]['ms'] is not None
