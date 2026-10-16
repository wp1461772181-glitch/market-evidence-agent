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


def test_classifier_request_has_no_empty_tool_protocol_and_enough_thinking_budget(monkeypatch):
    captured = {}
    async def fake_post(provider_name, key, body, base_url):
        captured.update(body)
        return {}, {"content": '{"intent":"general","confidence":0.9}'}
    monkeypatch.setattr(provider, "_post", fake_post)
    asyncio.run(provider.decision_request("deepseek", "deepseek-flash", "test-key", [{"role":"user","content":"hi"}], []))
    assert "tools" not in captured and "tool_choice" not in captured
    assert captured["max_tokens"] >= 4096


def test_usage_on_last_choice_frame_is_collected(monkeypatch):
    chunks=[b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}],"usage":{"total_tokens":42}}\n\n',b'data: [DONE]\n\n']
    transport(monkeypatch,httpx.Response(200,headers={"content-type":"text/event-stream"},stream=Chunks(chunks)))
    async def run():
        collected={}
        async for _ in provider.stream_answer("deepseek","deepseek-flash","test-key",[],collected): pass
        return collected
    assert asyncio.run(run())["_usage"]["total_tokens"]==42


# --- a reasoning model is asked to think little: a turn is several calls one after the other ---------

def _capture_bodies(monkeypatch):
    """Both kinds of call (a JSON decision, a streamed answer) answered by a stub; the bodies sent are kept."""
    seen = []

    async def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Chunks([b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n',
                                                 b'data: [DONE]\n\n']))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(provider.httpx, "AsyncClient", lambda **kw: ORIGINAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    return seen


def test_openrouter_requests_ask_for_low_reasoning_effort_in_stages_and_answers(monkeypatch):
    seen = _capture_bodies(monkeypatch)
    model = "nvidia/nemotron-3-ultra-550b-a55b:free"

    async def run():
        await provider.decision_request("openrouter", model, "k", [{"role": "user", "content": "x"}], [])
        async for _ in provider.stream_answer("openrouter", model, "k", [{"role": "user", "content": "x"}]):
            pass

    asyncio.run(run())
    assert [bool(body.get("stream")) for body in seen] == [False, True]
    assert all(body.get("reasoning") == {"effort": provider.REASONING_EFFORT} for body in seen)
    assert provider.REASONING_EFFORT == "low"


def test_provider_specific_effort_and_default_setting(monkeypatch):
    assert provider.reasoning_options("deepseek") == {"thinking":{"type":"enabled"},"reasoning_effort":"low"}
    assert provider.reasoning_options("custom") == {}
    assert provider.reasoning_options("openrouter") == {"reasoning": {"effort": provider.REASONING_EFFORT}}
    monkeypatch.setattr(provider, "REASONING_EFFORT", "default")
    assert provider.reasoning_options("openrouter") == {}
    monkeypatch.setattr(provider, "REASONING_EFFORT", "high")
    assert provider.reasoning_options("openrouter") == {"reasoning": {"effort": "high"}}
    monkeypatch.setattr(provider, "REASONING_EFFORT", "turbo")                    # unknown values send nothing
    assert provider.reasoning_options("openrouter") == {}


def test_decision_request_uses_each_providers_native_effort_format(monkeypatch):
    bodies = []

    async def fake_post(name, key, body, base_url=None):
        bodies.append((name, body))
        return {}, {"content": "{}"}

    monkeypatch.setattr(provider, "_post", fake_post)

    async def run():
        for name in ("openrouter", "deepseek", "minimax"):
            await provider.decision_request(name, "m", "k", [{"role": "user", "content": "x"}], [])

    asyncio.run(run())
    by_name = dict(bodies)
    assert by_name["openrouter"]["reasoning"] == {"effort": provider.REASONING_EFFORT}
    assert "reasoning" not in by_name["deepseek"] and "reasoning" not in by_name["minimax"]
    assert by_name["minimax"]["reasoning_split"] is True                          # the existing special case stays

def test_deepseek_flash_honors_configured_effort_for_native_and_stream_requests(monkeypatch):
    seen=_capture_bodies(monkeypatch)
    monkeypatch.setattr(provider,'REASONING_EFFORT','low')
    async def exercise():
        await provider.decision_request('deepseek','deepseek-flash','synthetic',[],[])
        async for _ in provider.stream_answer('deepseek','deepseek-flash','synthetic',[]):pass
    asyncio.run(exercise())
    assert all(body.get('thinking')=={'type':'enabled'} and body.get('reasoning_effort')=='low' for body in seen)


def test_turn_reuses_transport_without_sharing_request_authorization(monkeypatch):
    created=[];headers=[];real_client=httpx.AsyncClient
    def respond(request):
        headers.append(request.headers['authorization'])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'ok'}}]})
    def client(**options):
        value=real_client(**{**options,'transport':httpx.MockTransport(respond)})
        created.append(value);return value
    monkeypatch.setattr(provider.httpx,'AsyncClient',client)
    async def exercise():
        async with provider.decision_session():
            for key in ('first','second','third'):
                await provider.decision_request('deepseek','test',key,[],[])
    asyncio.run(exercise())
    assert len(created)==1 and created[0].is_closed
    assert headers==['Bearer first','Bearer second','Bearer third']


def test_concurrent_turn_transport_scopes_close_independently(monkeypatch):
    created=[];real_client=httpx.AsyncClient
    def client(**options):
        value=real_client(**{**options,'transport':httpx.MockTransport(lambda _:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}]}))})
        created.append(value);return value
    monkeypatch.setattr(provider.httpx,'AsyncClient',client)
    async def turn(fail=False):
        try:
            async with provider.decision_session():
                await provider.decision_request('deepseek','test','synthetic',[],[])
                await asyncio.sleep(0)
                if fail:raise RuntimeError('synthetic')
                await provider.decision_request('deepseek','test','synthetic',[],[])
        except RuntimeError:pass
    async def exercise():await asyncio.gather(turn(),turn(True))
    asyncio.run(exercise())
    assert len(created)==2 and all(c.is_closed for c in created)
