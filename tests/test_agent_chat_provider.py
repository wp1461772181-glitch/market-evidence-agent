import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

import app.agent_chat_provider as provider
ORIGINAL_CLIENT=httpx.AsyncClient


class Chunks(httpx.AsyncByteStream):
    def __init__(self, data): self.data=data
    async def __aiter__(self):
        for chunk in self.data: yield chunk


def transport(monkeypatch, response):
    async def handler(request): return response
    monkeypatch.setattr(provider.httpx,"AsyncClient",lambda **kw: ORIGINAL_CLIENT(transport=httpx.MockTransport(handler),**kw))


def collect(chunks, monkeypatch):
    transport(monkeypatch,httpx.Response(200,headers={"content-type":"text/event-stream"},stream=Chunks(chunks)))
    async def run():
        reasoning=[]; text=[]
        async for item in provider.stream_answer("deepseek","deepseek-chat","test-key",[],reasoning): text.append(item)
        return "".join(text),"".join(reasoning)
    return asyncio.run(run())


def test_final_stream_collects_hidden_reasoning_and_requires_stop(monkeypatch):
    chunks=[b'data: {"choices":[{"delta":{"reasoning_content":"private"},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n',b'data: [DONE]\n\n']
    assert collect(chunks,monkeypatch)==("answer","private")


def test_final_stream_collects_openrouter_reasoning_details(monkeypatch):
    chunks=[b'data: {"choices":[{"delta":{"reasoning_details":[{"type":"reasoning.encrypted","data":"synthetic"}]},"finish_reason":"stop"}]}\n\n',b'data: [DONE]\n\n']
    transport(monkeypatch,httpx.Response(200,headers={"content-type":"text/event-stream"},stream=Chunks(chunks)))
    async def run():
        collected={}
        async for _ in provider.stream_answer("openrouter","test/model","test-key",[],collected): pass
        return collected
    assert asyncio.run(run())=={"reasoning_details":[{"type":"reasoning.encrypted","data":"synthetic"}]}


@pytest.mark.parametrize("event",[
    b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n',
    b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n',
    b'data: not-json\n\n',
    b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: [DONE]\n\n',
])
def test_malformed_or_nonterminal_stream_is_rejected(monkeypatch,event):
    with pytest.raises(HTTPException): collect([event],monkeypatch)


def test_stream_rejects_redirect(monkeypatch):
    transport(monkeypatch,httpx.Response(302,headers={"location":"https://elsewhere.invalid"}))
    async def run():
        async for _ in provider.stream_answer("deepseek","deepseek-chat","test-key",[]): pass
    with pytest.raises(HTTPException) as error: asyncio.run(run())
    assert error.value.detail=="provider_redirect_rejected"


def test_nonstream_maps_redirect_and_friendly_bad_request(monkeypatch):
    transport(monkeypatch,httpx.Response(302))
    with pytest.raises(HTTPException) as error: asyncio.run(provider._post("deepseek","test-key",{}))
    assert error.value.detail=="provider_redirect_rejected"
    transport(monkeypatch,httpx.Response(422,json={"error":"invalid"}))
    with pytest.raises(HTTPException) as error: asyncio.run(provider._post("deepseek","test-key",{}))
    assert error.value.detail=="provider_model_unavailable_or_invalid_request"
