"""User-visible invariants of the direct workflow; no legacy planner callbacks."""
import asyncio
import json
from datetime import UTC,datetime
import pytest
from app.agent_workflow.graph import deterministic_confidence_gate
from app.agent_workflow.direct import run_direct_turn
from app.agent_workflow.state import AgentRuntimeContext

def test_gate_rejects_cross_company_and_period():
    result={'ok':True,'data':[{'symbol':'NVDA','excerpt':'NVDA net income FY2020','published_at':'2020-01-01'}]}
    assert deterministic_confidence_gate('AAPL FY2025 revenue',result,verified_symbols=['AAPL'])[0] is False

async def _emit(_): pass

def test_unknown_tools_stop_within_hard_budget():
    calls=[]
    async def decide(*_):
        calls.append(1)
        await asyncio.sleep(0)
        return {'tool_calls':[{'id':'x','function':{'name':'missing','arguments':'{}'}}]}
    context=AgentRuntimeContext('custom','test','test',classifier=lambda _: 'D',decider=decide)
    result=asyncio.run(asyncio.wait_for(run_direct_turn('read job status',context,emit=_emit),1))
    assert len(calls)<=12
    assert result['reason'] in {'no_progress','provider_protocol_invalid','tool_failures'}

def test_unregistered_citation_is_never_final_success():
    context=AgentRuntimeContext('custom','test','test',classifier=lambda _: 'D',decider=lambda *_:{'content':'Revenue 999 [99]'})
    result=asyncio.run(run_direct_turn('revenue',context,emit=_emit))
    assert '[99]' not in result['answer']
    assert result['reason'] in {'invalid_citation','zero_evidence'}

def test_operational_result_is_returned_as_success_to_the_next_native_decision():
    seen=[]
    async def decide(messages,_):
        seen.append(messages)
        if len(seen)==1:return {'tool_calls':[{'id':'status','function':{'name':'query_system_status','arguments':'{"kind":"monitor"}'}}]}
        result=json.loads(next(m['content'] for m in messages if m.get('tool_call_id')=='status'))
        assert result['ok'] and result['data'][0]['status']=='idle'
        return {'content':'监控当前空闲。'}
    context=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=decide,executor=lambda *_:{'ok':True,'data':[{'status':'idle'}]})
    result=asyncio.run(run_direct_turn('看看监控状态',context,emit=_emit))
    assert result['status']=='succeeded' and len(seen)==2

def test_partial_evidence_survives_successful_call_budget(monkeypatch):
    import app.agent_workflow.direct as direct
    monkeypatch.setattr(direct,'MAX_DISTINCT_CALLS',2)
    count=0
    async def decide(*_):
        nonlocal count
        count+=1
        return {'tool_calls':[{'id':str(count),'function':{'name':'query_stock_prices','arguments':json.dumps({'symbol':('AMD','AAPL','MU')[count-1]})}}]}
    context=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=decide,executor=lambda n,a:{'ok':True,'data':[{'id':a['symbol'],'symbol':a['symbol'],'close':100}]},answerer=lambda *_:'已完成：2 项价格来源 [1] [2]；未完成：剩余请求。')
    result=asyncio.run(run_direct_turn('八件互不相关的查询',context,emit=_emit))
    assert result['reason']=='too_many_steps' and result['status']=='partially_succeeded'
    assert len(result['citations'])==2 and '部分完成' in result['answer'] and '拆成' in result['answer']

def test_numeric_claim_must_be_in_the_cited_evidence():
    decisions=iter([{'tool_calls':[{'id':'price','function':{'name':'query_stock_prices','arguments':'{"symbol":"AMD"}'}}]},{'content':'AMD 收盘价为 999 美元 [1]。'}])
    context=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=lambda *_:next(decisions),executor=lambda *_:{'ok':True,'data':[{'id':'p','symbol':'AMD','close':100}]})
    result=asyncio.run(run_direct_turn('AMD 收盘价',context,emit=_emit))
    assert result['reason']=='unsupported_numeric_claim' and '999' not in result['answer']

def test_unrelated_write_verb_does_not_authorize_an_import(monkeypatch):
    import app.agent_workflow.direct as direct
    monkeypatch.setattr(direct,'owner_credential_matches',lambda _:True)
    identity='11111111-1111-4111-8111-111111111111'
    decisions=iter([{'tool_calls':[{'id':'bad','function':{'name':'import','arguments':json.dumps({'symbol':'AMD','source_id':identity})}}]},{'content':'没有导入。'}])
    context=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=lambda *_:next(decisions))
    result=asyncio.run(run_direct_turn('请分析这两者的关系 '+identity,context,emit=_emit))
    assert result['pending_request'] is None

def test_price_citation_cannot_authorize_an_unrelated_executive_event():
    choices=iter([{'tool_calls':[{'id':'price','function':{'name':'query_stock_prices','arguments':'{"symbol":"AAPL"}'}}]},{'content':'苹果 CEO 已辞职。[1]'}])
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'A',decider=lambda *_:next(choices),executor=lambda name,args:{'ok':True,'data':[] if name=='search_knowledge' else [{'id':'p','symbol':'AAPL','close':100}]})
    result=asyncio.run(run_direct_turn('苹果今天的股价',ctx,emit=_emit))
    assert result['status']=='failed' and result['reason']=='unsupported_claim'
    assert 'CEO' not in result['answer']

def test_rejected_success_body_is_not_in_sse_or_persistable_tool_events():
    events=[]
    async def emit(e):events.append(e)
    choices=iter([{'tool_calls':[{'id':'read','function':{'name':'query_stock_prices','arguments':'{"symbol":"AAPL"}'}}]},{'content':'没有可核验资料。'}])
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:{'category':'A','symbol':'AAPL'},decider=lambda *_:next(choices),executor=lambda *_:{'ok':True,'data':[{'id':'wrong','symbol':'MSFT','excerpt':'PRIVATE_REJECTED_TEXT'}]})
    result=asyncio.run(run_direct_turn('AAPL news',ctx,emit=emit))
    assert not result['citations']
    assert 'PRIVATE_REJECTED_TEXT' not in json.dumps(events)

def test_rejected_closing_text_keeps_budget_partial_results(monkeypatch):
    import app.agent_workflow.direct as direct
    monkeypatch.setattr(direct,'MAX_DISTINCT_CALLS',1)
    context=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',
        decider=lambda *_:{'tool_calls':[{'id':'p','function':{'name':'query_stock_prices','arguments':'{"symbol":"AMD"}'}}]},
        executor=lambda *_:{'ok':True,'data':[{'id':'p','symbol':'AMD','close':100}]},answerer=lambda *_:'AMD CEO 辞职了 [1]。')
    result=asyncio.run(run_direct_turn('查 AMD 价格及其他目标',context,emit=_emit))
    assert result['reason']=='too_many_steps' and result['status']=='partially_succeeded'
    assert '100' in result['answer'] and '[1]' in result['answer'] and 'CEO' not in result['answer']
