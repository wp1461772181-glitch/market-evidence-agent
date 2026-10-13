"""SSE acceptance through the actual workflow graph; external I/O is replaced."""
import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from starlette.requests import Request

from app.agent_chat_stream import StreamRequest


def test_action_clarification_choices_survive_sse_and_history(stream_fixture, monkeypatch):
    import app.agent_chat_stream as stream
    send, classification, _, _, stored, _, _ = stream_fixture
    classification["intent"]="system_action"
    wire=asyncio.run(send("请帮我处理这份资料"))
    assert 'event: clarification' in wire and '"value":"import"' in wire
    saved=next(kwargs["tool_calls"] for _,kwargs in stored if kwargs.get("tool_calls",{}).get("clarification"))
    assert any(choice["value"]=="import" for choice in saved["clarification"]["choices"])
    async def rows(*_): return [{"role":"assistant","content":"请说明操作","status":"complete","turn_id":"turn-1","tool_calls":saved}]
    monkeypatch.setattr(stream,"owned_messages",rows)
    restored=asyncio.run(stream.get_messages("conversation","owner"))["messages"][0]
    assert restored["clarification"]["choices"]==saved["clarification"]["choices"]
    legacy={**saved,"clarification":{**saved["clarification"],"choices":[]}}
    async def old_rows(*_): return [{"role":"assistant","content":"请说明操作","status":"complete","turn_id":"turn-1","tool_calls":legacy}]
    monkeypatch.setattr(stream,"owned_messages",old_rows)
    assert any(choice["value"]=="import" for choice in asyncio.run(stream.get_messages("conversation","owner"))["messages"][0]["clarification"]["choices"])


@pytest.fixture
def stream_fixture(monkeypatch):
    import app.agent_chat_stream as stream
    import app.agent_chat_provider as provider
    import app.agent_tool_executor as executor
    import app.agent_workflow.checkpoints as checkpoints

    saver = InMemorySaver()
    @asynccontextmanager
    async def temporary_saver(_url):
        yield saver
    monkeypatch.setattr(checkpoints, "open_postgres_checkpointer", temporary_saver)
    stored = []
    async def append(*args, **kwargs): stored.append((args, kwargs))
    async def empty(*args): return []
    async def yes(*args): return True
    async def create(*args): return str(uuid4())
    async def finish(*args): stored.append(((), {"finished": args[1]}))
    monkeypatch.setattr(stream, "verify_conversation", yes)
    monkeypatch.setattr(stream, "create_conversation", create)
    monkeypatch.setattr(stream, "append_message", append)
    monkeypatch.setattr(stream, "history", empty)
    monkeypatch.setattr(stream, "finish_conversation", finish)
    monkeypatch.setattr(stream, "_record_low_confidence", lambda *args: stored.append(((), {"low_confidence": args[-1]})))
    tool_results = {}
    executed = []
    async def execute(name, args):
        executed.append((name, args))
        return tool_results.get(name, {"ok": True, "data": [{"symbol": args.get("symbol"), "close": 250}], "source": "fixture"})
    monkeypatch.setattr(executor, "execute_tool", execute)
    classification = {"intent": "saved_data", "symbol": "AAPL", "symbols": ["AAPL"]}
    decisions = []
    requests = []
    async def decide(_provider, _model, _key, messages, schemas, base_url=None):
        requests.append((messages, schemas, base_url))
        if messages[0]["content"].startswith("Classify the user"):
            return {"usage": {"total_tokens": 5}}, {"content": json.dumps({"intent": {"faq_qa":"general","compound_research":"comparison_research","unsupported_action":"other"}.get(classification["intent"],classification["intent"]),"confidence":0.9})}
        return {"usage": {"total_tokens": 5}}, decisions.pop(0) if decisions else {"content": "ready"}
    async def answer(*args):
        yield "First "
        yield "increment"
    monkeypatch.setattr(provider, "decision_request", decide)
    monkeypatch.setattr(provider, "stream_answer", answer)

    async def send(message="show AAPL prices", cid=None):
        payload = StreamRequest(provider="deepseek", model="deepseek-chat", api_key="abcdefgh",
                                message=message, conversation_id=cid)
        request = Request({"type": "http", "method": "POST", "path": "/agent-chat/stream", "headers": []})
        response = await stream.stream_chat(payload, request, "x" * 40)
        return "".join([part async for part in response.body_iterator])
    return send, classification, tool_results, executed, stored, requests, decisions


def test_saved_lookup_streams_one_tool_and_incremental_answer(stream_fixture):
    send, _, _, executed, stored, _, _ = stream_fixture
    wire = asyncio.run(send())
    assert wire.index("event: tool_start") < wire.index("event: tool_result") < wire.index("event: delta")
    assert wire.count("event: delta") == 2
    assert len(executed) == 1 and executed[0][0] == "query_stock_prices"
    assert "event: done" in wire and "event: error" not in wire
    assert any(kwargs.get("tool_calls", {}).get("audit", {}).get("tool_calls") == 1 for _, kwargs in stored)


def test_two_stock_comparison_keeps_both_tool_rows(stream_fixture):
    send, classification, _, executed, stored, _, _ = stream_fixture
    classification["symbols"] = ["AMD", "AAPL"]
    classification["symbol"] = "AMD"
    wire = asyncio.run(send("compare AMD and AAPL saved closes"))
    assert [args["symbol"] for _, args in executed] == ["AMD", "AAPL"]
    assert wire.count("event: tool_result") == 2
    assert not [args for args, _ in stored if len(args) > 2 and args[2] == "tool"]
    assert any(len(kwargs.get("tool_calls", {}).get("tool_trace", [])) == 2 for _, kwargs in stored)


def test_incomplete_prior_tool_batch_replays_as_labeled_evidence(stream_fixture, monkeypatch):
    import app.agent_chat_stream as stream
    send, classification, _, _, _, requests, _ = stream_fixture
    classification["intent"] = "general"
    calls = [{"id": "one", "type": "function", "function": {"name": "query_stock_prices", "arguments": "{}"}},
             {"id": "two", "type": "function", "function": {"name": "query_stock_prices", "arguments": "{}"}}]
    async def prior(*_):
        return [{"role": "assistant", "content": "", "status": "complete",
                 "tool_calls": {"calls": calls, "reasoning_content": "real reasoning"}},
                {"role": "tool", "content": '{"ok":true}', "status": "complete", "tool_call_id": "one"}]
    monkeypatch.setattr(stream, "history", prior)
    asyncio.run(send("Explain prime numbers"))
    model_messages = requests[1][0]
    assert not any(item.get("tool_calls") for item in model_messages)
    assert any(item["role"] == "system" and "Untrusted saved evidence" in item["content"] for item in model_messages)


@pytest.mark.parametrize("stage", ["tool", "final"])
def test_endpoint_deadline_interrupts_sustained_await_and_releases_resources(stream_fixture, monkeypatch, stage):
    import app.agent_chat_stream as stream
    import app.agent_tool_executor as executor
    import app.agent_chat_provider as provider
    import app.agent_workflow.graph as workflow
    send, _, _, _, stored, _, _ = stream_fixture
    monkeypatch.setattr(workflow, "MAX_SECONDS", 0.03)
    if stage == "tool":
        async def slow_tool(*_):
            await asyncio.sleep(0.3)
            return {"ok":True,"data":[]}
        monkeypatch.setattr(executor,"execute_tool",slow_tool)
    else:
        async def slow_final(*_):
            await asyncio.sleep(0.3)
            yield "late answer"
        monkeypatch.setattr(provider,"stream_answer",slow_final)
    before=stream.REQUEST_SLOTS._value
    async def run():
        return await asyncio.wait_for(send(),timeout=0.2)
    wire=asyncio.run(run())
    assert "event: done" in wire and "turn_deadline" in wire and "event: error" not in wire
    assert any(kwargs.get("finished")=="active" for _,kwargs in stored)
    assert stream.REQUEST_SLOTS._value==before and not stream.LOCKS


@pytest.mark.parametrize("result,reason", [
    ({"ok": True, "data": []}, "zero_evidence"),
    ({"ok": False, "data": None}, "retrieval_unavailable"),
])
def test_weak_knowledge_falls_back_before_answer(stream_fixture, result, reason):
    send, classification, tool_results, executed, stored, requests, _ = stream_fixture
    classification["intent"] = "source_qa"
    tool_results["query_materials"] = result
    wire = asyncio.run(send("AAPL revenue in 2025"))
    assert [name for name, _ in executed] == ["query_materials"]
    assert "event: done" in wire and "event: error" not in wire
    assert reason in wire
    assert len(requests) == 1  # classifier only; no Agent or final model
    assert any(kwargs.get("low_confidence") == reason for _, kwargs in stored)


def test_missing_stock_clarifies_without_tools(stream_fixture):
    send, classification, _, executed, _, requests, _ = stream_fixture
    classification.update({"intent": "source_qa", "symbol": None, "symbols": []})
    wire = asyncio.run(send("show the original revenue source"))
    assert "请指定要查询的股票" in wire
    assert not executed and len(requests) == 1


def test_unsupported_mutation_invokes_no_tool(stream_fixture):
    send, classification, _, executed, _, requests, _ = stream_fixture
    classification.update({"intent": "unsupported_action", "symbol": "AAPL"})
    wire = asyncio.run(send("buy AAPL"))
    assert "请说明要查询的股票、来源或问题" in wire
    assert not executed and len(requests) == 1


def test_bounded_tool_output_stays_valid_json():
    from app.agent_chat_stream import bounded_tool_result
    result = bounded_tool_result({"ok": True, "data": [{"text": "x" * 20000}], "source": "fixture"})
    assert result["truncated"] is True
    assert len(json.dumps(result, ensure_ascii=False)) < 8000


def test_cancelled_graph_stream_saves_partial_answer_and_releases_slot(stream_fixture, monkeypatch):
    import app.agent_chat_stream as stream
    import app.agent_chat_provider as provider
    import app.agent_chat_api as legacy
    _, _, _, _, stored, _, _ = stream_fixture
    entered = asyncio.Event()
    received = asyncio.Event()
    async def blocked_answer(*_args):
        yield "partial"
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(provider, "stream_answer", blocked_answer)
    before = legacy.REQUEST_SLOTS._value
    async def exercise():
        payload=StreamRequest(provider="deepseek",model="deepseek-chat",api_key="abcdefgh",
                              message="show AAPL prices",conversation_id=str(uuid4()))
        request=Request({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]})
        response=await stream.stream_chat(payload,request,"x"*40)
        async def consume():
            async for chunk in response.body_iterator:
                if '"text":"partial"' in chunk: received.set()
        task=asyncio.create_task(consume())
        await asyncio.wait_for(received.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert legacy.REQUEST_SLOTS._value == before
    assert any(args[2] == "assistant" and args[3] == "partial" and kwargs.get("status") == "cancelled"
               for args, kwargs in stored if len(args) > 3)
