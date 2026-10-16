import asyncio,json
import pytest
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

async def emit(_):pass
def call(name,**args):return {'tool_calls':[{'id':name,'function':{'name':name,'arguments':json.dumps(args)}}]}

@pytest.mark.parametrize('replacement,reason',[
    ({'content':'AMD 收盘价为 100 美元 [1]。'},None),
    ({'content':'AMD 收盘价为 888 美元 [1]。'},'unsupported_numeric_claim'),
    (call('initialize_prices',symbol='AMD'),'unsupported_numeric_claim'),
    ({'content':'AMD 收盘价为 100 美元 [99]。'},'invalid_citation'),
    ({'content':'AMD CEO 已辞职 [1]。'},'unsupported_claim'),
])
def test_numeric_repair_is_single_tool_free_and_still_guarded(replacement,reason):
    steps=iter([call('query_stock_prices',symbol='AMD'),{'content':'AMD 收盘价为 999 美元 [1]。'},replacement])
    schemas=[];executed=[]
    def decide(messages,tools):schemas.append(tools);return next(steps)
    def execute(name,args):executed.append(name);return {'ok':True,'data':[{'id':'p','symbol':'AMD','close':100}]}
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=decide,executor=execute)
    result=asyncio.run(direct.run_direct_turn('AMD 收盘价',ctx,emit=emit))
    assert (result['status']=='succeeded')==(reason is None)
    assert schemas[-1]==[] and len(schemas)==3
    assert result['audit']['answer_repair_count']==1
    assert executed==['query_stock_prices'] and '999' not in result['answer']
    assert result['reason']==reason and '888' not in result['answer']

def test_numeric_repair_respects_token_budget(monkeypatch):
    calls=[]
    ctx=AgentRuntimeContext('custom','test','dummy',decider=lambda *_:calls.append(1))
    turn=direct.Turn('AMD 收盘价',ctx,emit,[],{})
    turn.route='A';turn.citations=[{'ordinal':1,'quote':'100','source_type':'saved_price'}]
    turn.observations=[{'accepted_evidence':True}];turn.audit['token_usage']=turn.token_limit
    result=asyncio.run(turn.finish_answer('AMD 收盘价为 999 美元[1]'))
    assert result['reason']=='unsupported_numeric_claim' and not calls
    assert not result['audit'].get('answer_repair_count')

def test_time_refinement_preserves_news_metric_scope_and_blocks_price_adapter():
    steps=iter([call('query_stock_prices',symbol='MU'),{'content':'没有可核验资料。'}]);executed=[]
    ctx=AgentRuntimeContext('custom','test','dummy',
        resolver=lambda _:{'relationship':'refinement','canonical_question':'Micron 最近15天新闻'},
        classifier=lambda _:{'category':'A','company':'Micron','symbol':'MU','metrics':['price'],'days_back':15},
        decider=lambda *_:next(steps),executor=lambda n,a:executed.append(n) or {'ok':True,'data':[]})
    result=asyncio.run(direct.run_direct_turn('我要最近的行情，也就是半个月之内的',ctx,emit=emit,
        messages=[{'role':'user','content':'Micron 最近新闻'}],state={'previous_scope':{'company':'Micron','symbol':'MU','metrics':[]}}))
    assert 'query_stock_prices' not in executed and result['previous_scope']['metrics']==[]
    assert result['audit']['preserved_task_scope'] is True

def test_time_refinement_of_a_price_task_still_allows_price_reads():
    ctx=AgentRuntimeContext('custom','test','dummy')
    turn=direct.Turn('近15天',ctx,emit,[],{});turn.refinement=True;turn.scope={'symbol':'MU','metrics':['price']}
    turn.ground_arguments('query_stock_prices',{'symbol':'MU'})
