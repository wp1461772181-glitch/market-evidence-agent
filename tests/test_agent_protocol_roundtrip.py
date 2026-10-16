import asyncio,json
from app.agent_workflow.direct import run_direct_turn
from app.agent_workflow.state import AgentRuntimeContext


def test_native_multi_tool_ids_and_reasoning_stay_paired():
    calls=[{'id':'one','type':'function','function':{'name':'query_stock_prices','arguments':'{"symbol":"AMD"}'}},
           {'id':'two','type':'function','function':{'name':'query_stock_prices','arguments':'{"symbol":"AAPL"}'}}]
    seen=[]
    async def decide(messages,schemas):
        seen.append(messages)
        if len(seen)==1:return {},{'content':None,'reasoning_content':'PRIVATE_REASONING','tool_calls':calls}
        assistant=next(m for m in messages if m.get('tool_calls'))
        assert assistant['tool_calls']==calls and assistant['reasoning_content']=='PRIVATE_REASONING'
        assert [m['tool_call_id'] for m in messages if m['role']=='tool']==['one','two']
        return {'content':'两家公司行情已读取 [1] [2]。'}
    async def execute(name,args):return {'ok':True,'data':[{'id':args['symbol'],'symbol':args['symbol'],'close':100,'trading_date':'2026-10-14','fetched_at':'2026-10-15T00:00:00Z'}]}
    async def emit(_):pass
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=decide,executor=execute)
    result=asyncio.run(run_direct_turn('AMD AAPL prices',ctx,emit=emit))
    assert result['status']=='succeeded' and result['audit']['tool_calls']==2
    assert 'PRIVATE_REASONING' not in result['answer']
