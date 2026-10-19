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

def test_reasoning_of_a_few_dozen_kilobytes_is_accepted():
    message={'role':'assistant','content':''};calls={}
    _merge_delta(message,calls,{'reasoning_content':'想'*9000})
    assert len(message['reasoning_content'])==9000
    with pytest.raises(HTTPException):_merge_delta(message,calls,{'reasoning_content':'想'*30000})

def test_a_failed_repair_records_the_provider_reason():
    from app.agent_workflow import direct as d
    async def boom(*_a,**_k):raise HTTPException(504,'provider_timeout')
    turn=d.Turn('AMD 收盘价',AgentRuntimeContext('custom','test','dummy'),emit,[],{})
    turn.route='A';turn.citations=[{'ordinal':1,'quote':'100','source_type':'saved_price'}]
    turn.observations=[{'accepted_evidence':True}]
    turn.model=boom
    result=asyncio.run(turn.finish_answer('AMD 收盘价为999美元[1]'))
    assert result['audit']['answer_repair_error']=='repair_unavailable:HTTPException:provider_timeout'
