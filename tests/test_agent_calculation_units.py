"""Unit conversion is arithmetic on a cited value, not a new figure."""
import asyncio,json
import pytest
from fastapi import HTTPException
from app.agent_workflow.calculation import calculate
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

SOURCE=[{'ordinal':1,'source_type':'calculation','quote':'{"value": "50115000000.00"}'}]

def test_a_value_may_be_converted_to_billions_with_a_power_of_ten():
    result=calculate({'expression':'a/1000000000','inputs':[{'name':'a','value':'50115000000','source_ordinal':1}]},SOURCE)
    assert result['value']=='50.12'

def test_an_arbitrary_constant_is_still_refused():
    with pytest.raises(ValueError):
        calculate({'expression':'a*7','inputs':[{'name':'a','value':'50115000000','source_ordinal':1}]},SOURCE)

def test_a_provider_failure_mid_loop_answers_from_the_evidence_gathered():
    steps=iter([{'tool_calls':[{'id':'1','function':{'name':'query_stock_prices','arguments':json.dumps({'symbol':'AMD'})}}]}])
    def decide(messages,tools):
        try:return next(steps)
        except StopIteration:raise HTTPException(502,'provider_protocol_too_large')
    async def emit(_):pass
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=decide,answerer=lambda messages:'AMD 已保存收盘价 100 美元 [1]。',
        executor=lambda *_:{'ok':True,'data':[{'id':'p','symbol':'AMD','close':100}]})
    result=asyncio.run(direct.run_direct_turn('AMD 收盘价',ctx,emit=emit))
    assert result['status']=='succeeded' and result['answer'].startswith('AMD 已保存收盘价 100 美元 [1]')
