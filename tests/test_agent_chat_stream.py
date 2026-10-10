import asyncio, json
from starlette.requests import Request
from app.agent_chat_stream import StreamRequest, stream_chat


def run_stream(monkeypatch, decision):
    import app.agent_chat_stream as module
    seen=[]
    async def verify(*args): return True
    async def create(*args): return "12345678-1234-5678-1234-567812345678"
    async def append(*args, **kwargs): seen.append((args,kwargs))
    async def history(*args): return []
    async def finish(*args): pass
    async def decide(*args): return ({}, decision)
    async def tool(*args): return {"ok":True,"data":[{"close":250}],"source":"fixture","as_of":"2026-10-09"}
    async def answer(*args):
        yield "First "
        await asyncio.sleep(0)
        yield "increment"
    monkeypatch.setattr(module,"verify_conversation",verify)
    monkeypatch.setattr(module,"create_conversation",create)
    monkeypatch.setattr(module,"append_message",append)
    monkeypatch.setattr(module,"owned_messages",history)
    monkeypatch.setattr(module,"history",history)
    monkeypatch.setattr(module,"finish_conversation",finish)
    monkeypatch.setattr(module,"decision_request",decide)
    monkeypatch.setattr(module,"execute_tool",tool)
    monkeypatch.setattr(module,"stream_answer",answer)
    payload=StreamRequest(provider="deepseek",model="deepseek-chat",api_key="abcdefgh",message="show AAPL",conversation_id=None)
    req=Request({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]})
    response=asyncio.run(module.stream_chat(payload,req,"x"*40))
    async def collect(): return [part async for part in response.body_iterator]
    chunks=asyncio.run(collect())
    return "".join(chunks),seen


def test_tool_event_precedes_incremental_final_answer_and_reasoning_is_persisted(monkeypatch):
    decision={"tool_calls":[{"id":"call-1","type":"function","function":{"name":"query_stock_prices","arguments":"{\"symbol\":\"AAPL\"}"}}],"reasoning_content":"private reasoning","reasoning_details":[{"type":"reasoning.text","text":"private"}]}
    wire,stored=run_stream(monkeypatch,decision)
    assert wire.index("event: tool_start") < wire.index("event: tool_result") < wire.index('event: delta')
    assert wire.count('event: delta') == 2 and "private reasoning" not in wire
    protocol=next(kwargs["tool_calls"] for args,kwargs in stored if kwargs.get("tool_calls"))
    assert protocol["reasoning_content"] == "private reasoning"
    assert "event: done" in wire and "event: error" not in wire


def test_no_tool_answer_still_emits_separate_incremental_deltas(monkeypatch):
    wire,_=run_stream(monkeypatch,{"content":"ordinary answer"})
    assert wire.count("event: delta") == 2
    assert "First " in wire and "increment" in wire and "event: done" in wire


def test_bounded_tool_output_stays_valid_json():
    from app.agent_chat_stream import bounded_tool_result
    result=bounded_tool_result({"ok":True,"data":[{"text":"x"*20000}],"source":"fixture","as_of":None})
    assert result["truncated"] is True
    assert len(json.dumps(result,ensure_ascii=False)) < 8000


def test_cancelled_stream_persists_terminal_state_and_releases_capacity(monkeypatch):
    import app.agent_chat_stream as module
    import app.agent_chat_api as legacy
    from starlette.requests import Request
    from uuid import uuid4
    seen=[]; cid=str(uuid4())
    async def yes(*args): return True
    async def append(*args, **kwargs): seen.append((args,kwargs))
    async def no_history(*args): return []
    async def finish(*args): seen.append(((),{"finished":args[1]}))
    async def blocked(*args): await asyncio.Event().wait()
    monkeypatch.setattr(module,"verify_conversation",yes)
    monkeypatch.setattr(module,"append_message",append)
    monkeypatch.setattr(module,"history",no_history)
    monkeypatch.setattr(module,"finish_conversation",finish)
    monkeypatch.setattr(module,"decision_request",blocked)
    before=legacy.REQUEST_SLOTS._value
    payload=StreamRequest(provider="deepseek",model="deepseek-chat",api_key="abcdefgh",message="hello",conversation_id=cid)
    req=Request({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]})
    async def exercise():
        response=await module.stream_chat(payload,req,"x"*40)
        iterator=response.body_iterator.__aiter__()
        await iterator.__anext__() # conversation
        await iterator.__anext__() # selecting status
        pending=asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        pending.cancel()
        try: await pending
        except asyncio.CancelledError: pass
        return module.LOCKS.get(cid) is not None and module.LOCKS[cid].locked()
    locked=asyncio.run(exercise())
    assert not locked and legacy.REQUEST_SLOTS._value == before
    assert any(kwargs.get("status")=="cancelled" for _,kwargs in seen)
    assert any(kwargs.get("finished")=="cancelled" for _,kwargs in seen)


def test_disconnect_before_body_iteration_releases_capacity(monkeypatch):
    import app.agent_chat_stream as module
    from starlette.requests import Request
    from uuid import uuid4
    async def yes(*args): return True
    async def nothing(*args,**kwargs): pass
    monkeypatch.setattr(module,"verify_conversation",yes)
    monkeypatch.setattr(module,"append_message",nothing)
    cid=str(uuid4()); before=module.REQUEST_SLOTS._value
    payload=StreamRequest(provider="deepseek",model="deepseek-chat",api_key="abcdefgh",message="hello",conversation_id=cid)
    req=Request({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]})
    async def exercise():
        response=await module.stream_chat(payload,req,"x"*40)
        async def receive(): await asyncio.Event().wait()
        async def send(message):
            if message["type"]=="http.response.start": raise OSError("client disconnected")
        try: await response({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]},receive,send)
        except BaseException: pass
    asyncio.run(exercise())
    assert module.REQUEST_SLOTS._value==before
    assert cid not in module.LOCKS
