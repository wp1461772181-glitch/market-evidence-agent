"""A provider hiccup costs one retry of that call, not the turn."""
import asyncio
import pytest
from fastapi import HTTPException
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext
from app.agent_chat_provider import _merge_delta

async def emit(_):pass

def test_a_transient_provider_failure_is_retried_once(monkeypatch):
    calls=[]
    async def flaky(*_a,**_k):
        calls.append(1)
        if len(calls)==1:raise HTTPException(502,'provider_protocol_too_large')
        return {'usage':{'total_tokens':5}},{'content':'好的'}
    monkeypatch.setattr(direct,'decision_request',flaky)
    turn=direct.Turn('q',AgentRuntimeContext('custom','test','dummy'),emit,[],{})
    message=asyncio.run(turn.model([{'role':'user','content':'q'}],[],protocol_retries=0))
    assert message['content']=='好的' and calls==[1,1] and turn.audit['provider_retries']==1

def test_a_persistent_provider_failure_still_raises(monkeypatch):
    async def down(*_a,**_k):raise HTTPException(504,'provider_timeout')
    monkeypatch.setattr(direct,'decision_request',down)
    turn=direct.Turn('q',AgentRuntimeContext('custom','test','dummy'),emit,[],{})
    with pytest.raises(HTTPException):asyncio.run(turn.model([{'role':'user','content':'q'}],[],protocol_retries=0))

def test_a_rejected_key_is_not_retried(monkeypatch):
    calls=[]
    async def rejected(*_a,**_k):calls.append(1);raise HTTPException(401,'provider_invalid_key')
    monkeypatch.setattr(direct,'decision_request',rejected)
    turn=direct.Turn('q',AgentRuntimeContext('custom','test','dummy'),emit,[],{})
    with pytest.raises(HTTPException):asyncio.run(turn.model([{'role':'user','content':'q'}],[],protocol_retries=0))
    assert calls==[1]

def test_a_long_chinese_reasoning_is_accepted_up_to_the_memory_guard():
    message={'role':'assistant','content':''};calls={}
    _merge_delta(message,calls,{'reasoning_content':'想'*100_000})
    assert len(message['reasoning_content'])==100_000
    with pytest.raises(HTTPException):_merge_delta(message,calls,{'reasoning_content':'想'*600_000})

def test_a_failed_repair_records_the_provider_reason():
    from app.agent_workflow import direct as d
    async def boom(*_a,**_k):raise HTTPException(504,'provider_timeout')
    turn=d.Turn('AMD 收盘价',AgentRuntimeContext('custom','test','dummy'),emit,[],{})
    turn.route='A';turn.citations=[{'ordinal':1,'quote':'100','source_type':'saved_price'}]
    turn.observations=[{'accepted_evidence':True}]
    turn.model=boom
    result=asyncio.run(turn.finish_answer('AMD 收盘价为999美元[1]'))
    assert result['audit']['answer_repair_error']=='repair_unavailable:HTTPException:provider_timeout'

def test_a_large_tool_result_keeps_the_rows_that_fit():
    from app.agent_chat_stream import bounded_tool_result
    # Rows sized so the kept rows fill the limit to within the result's own extra keys.
    rows=[{'id':str(i),'excerpt':'x'*6144} for i in range(11)]
    bounded=bounded_tool_result({'ok':True,'data':rows,'source':'saved'})
    assert bounded['ok'] and bounded['data'] and bounded['truncated']
