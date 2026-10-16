import asyncio
import json
import socket
import httpx
import pytest
from app import agent_chat_api as api

def test_http_model_endpoint_requires_exact_operator_allowlist(monkeypatch):
    url='http://127.0.0.1:11434/v1'
    monkeypatch.delenv('AGENT_LOCAL_MODEL_BASE_URLS',raising=False)
    with pytest.raises(ValueError):api._validated_base_url(url)
    monkeypatch.setenv('AGENT_LOCAL_MODEL_BASE_URLS',json.dumps([url]))
    assert api._validated_base_url(url+'/chat/completions')==url
    for other in ('http://127.0.0.1:11435/v1','http://localhost:11434/v1','http://127.0.0.1:11434/admin'):
        with pytest.raises(ValueError):api._validated_base_url(other)

def test_local_transport_has_only_model_routes_and_public_transport_stays_private_safe(monkeypatch):
    base='http://127.0.0.1:11434/v1'
    monkeypatch.setenv('AGENT_LOCAL_MODEL_BASE_URLS',json.dumps([base]))
    seen=[]
    transport=api.LocalModelTransport(base,inner=httpx.MockTransport(lambda r:seen.append(str(r.url)) or httpx.Response(200,json={})))
    async def exercise():
        loop=asyncio.get_running_loop()
        async def resolve(*_,**kw):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',11434))]
        monkeypatch.setattr(loop,'getaddrinfo',resolve)
        await transport.handle_async_request(httpx.Request('POST',base+'/chat/completions'))
        for url in (base+'/admin',base+'/models?url=http://169.254.169.254','http://127.0.0.1:11435/v1/models'):
            with pytest.raises(httpx.ConnectError):await transport.handle_async_request(httpx.Request('GET',url))
        with pytest.raises(httpx.ConnectError):
            await api.PinnedPublicTransport(inner=httpx.MockTransport(lambda _:pytest.fail('public fetch reached private'))).handle_async_request(httpx.Request('GET','https://127.0.0.1/page'))
    asyncio.run(exercise())
    assert seen==[base+'/chat/completions']

def test_safe_key_configuration_question_reaches_model_without_secret_capability():
    from app.agent_workflow.direct import run_direct_turn
    from app.agent_workflow.state import AgentRuntimeContext
    calls=[]
    async def emit(_):pass
    ctx=AgentRuntimeContext('custom','test','not-a-real-key',classifier=lambda _:calls.append(1) or 'D',decider=lambda *_:{'content':'在 Agent 页的模型设置中填写自己的 API Key。'})
    result=asyncio.run(run_direct_turn('API Key 在哪里填？',ctx,emit=emit))
    assert calls==[1] and '模型设置' in result['answer']
