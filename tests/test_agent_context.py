"""Layered conversation context: budgets, rendering, the store's layers, the stream's messages, the
summary segments and the usage report.  Model calls are replaced; the store runs on a disposable
database."""
import asyncio
import json
from uuid import uuid4

import pytest

from app import agent_context as layers
from test_agent_chat_stream import stream_fixture  # noqa: F401  (the SSE fixture of the sibling module)

TURN = "11111111-1111-4111-8111-{:012d}"
MINE = "owner-credential-" + "a" * 40
THEIRS = "other-credential-" + "b" * 40


# --- the knob and what it sets ----------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [(None, 64), ("", 64), ("abc", 64), ("64", 64), ("32", 32),
                                           ("8", 16), ("0", 16), ("500", 128), ("-5", 16), (" 96 ", 96)])
def test_the_budget_knob_is_clamped_and_unreadable_values_are_the_default(raw, expected):
    assert layers.parse_budget_kb(raw) == expected


def test_the_layers_and_guards_follow_one_budget():
    assert layers.HISTORY_BUDGET == layers.BUDGET_KB * 1024
    assert layers.L1_BYTES + layers.L2_BYTES + layers.L3_BYTES <= layers.HISTORY_BUDGET
    assert layers.CONTEXT_GUARD_BYTES == 2 * layers.HISTORY_BUDGET
    assert layers.HISTORY_TOKEN_ALLOWANCE == layers.HISTORY_BUDGET // 4
    # the default is the 64 KB tier: 32 + 24 + 6 KB of history, a 128 KB request guard
    if layers.BUDGET_KB == 64:
        assert (layers.L1_BYTES, layers.L2_BYTES, layers.L3_BYTES) == (32768, 24576, 6144)
        assert layers.CONTEXT_GUARD_BYTES == 131072


# --- rendering and selection -------------------------------------------------------------------------

def _turn(n, user="问题", answer="答复"):
    return layers.Turn(first_sequence=2 * n - 1, last_sequence=2 * n, user=user, assistant=answer)


def test_half_compression_keeps_the_users_words_and_the_opening_of_the_answer():
    long_answer = "结论在前。" + "细节" * 200
    messages = layers.half_messages(_turn(1, "苹果和英伟达的营收，只看 10-Q，不要新闻", long_answer))
    assert messages[0] == {"role": "user", "content": "苹果和英伟达的营收，只看 10-Q，不要新闻"}
    assert messages[1]["role"] == "assistant" and messages[1]["content"].startswith("结论在前。")
    assert messages[1]["content"].endswith("…(略)") and len(messages[1]["content"]) <= layers.ASSISTANT_KEEP_CHARS + 6
    assert layers.half_messages(_turn(1, "短", ""))[1:] == []                      # no answer, no assistant message
    pasted = layers.half_messages(_turn(1, "长" * 5000))[0]["content"]
    assert len(pasted) <= layers.USER_KEEP_CHARS + 6                               # one paste cannot take the layer


def test_the_newest_turns_that_fit_are_kept_and_the_older_rest_is_the_overflow():
    turns = [_turn(n, user=f"第{n}个问题" + "x" * 80, answer="y" * 80) for n in range(1, 9)]
    size = layers.half_size(turns[0])
    kept, overflow = layers.select_half(turns, budget=size * 3 + 5)
    assert [t.first_sequence for t in kept] == [t.first_sequence for t in turns[-3:]]
    assert overflow == turns[:5]
    assert layers.select_half(turns, budget=0) == ([], turns)
    assert layers.select_half([], budget=1000) == ([], [])


def test_the_newest_summary_segments_fit_and_stay_in_order():
    segments = [{"seq": i, "content": f"第{i}段" + "摘" * 30} for i in range(1, 9)]
    chosen = layers.select_segments(segments, budget=10_000)
    assert [s["seq"] for s in chosen] == [4, 5, 6, 7, 8]                           # at most five, the newest
    one = len(segments[0]["content"].encode()) + 8
    assert [s["seq"] for s in layers.select_segments(segments, budget=one * 2 + 1)] == [7, 8]
    assert layers.select_segments(segments, budget=1) == []


def test_the_summary_is_one_system_note_that_says_it_is_not_evidence():
    assert layers.summary_message([]) is None
    note = layers.summary_message([{"content": "用户关注苹果的营收"}, {"content": "之后改看英伟达"}])
    assert note["role"] == "system" and note["content"].startswith(layers.MEMORY_MARKER)
    assert "not evidence" in note["content"] and "not something to cite" in note["content"]
    assert "1. 用户关注苹果的营收" in note["content"] and "2. 之后改看英伟达" in note["content"]
    assert layers.earlier_summary([{"role": "user", "content": "你好"}, note]) == "1. 用户关注苹果的营收\n2. 之后改看英伟达"
    assert layers.earlier_summary([{"role": "system", "content": "Other system text"}]) == ""


def test_summary_comes_first_then_the_half_compressed_turns():
    messages = layers.prefix_messages([{"content": "摘要"}], [_turn(1, "甲"), _turn(2, "乙")])
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user", "assistant"]
    assert messages[1]["content"] == "甲" and messages[3]["content"] == "乙"
    assert layers.prefix_messages([], []) == []


def test_the_usage_report_adds_up_and_says_when_it_is_compressing():
    fixed = {"system_bytes": 2000, "tools_bytes": 5000}
    empty = layers.usage_report(**fixed, summary_bytes=0, summary_segments=0, half_bytes=0, half_turns=0,
                                recent_bytes=0, recent_turns=0, hidden_turns=0)
    assert empty["remaining_bytes"] == layers.HISTORY_BUDGET and empty["compressing"] is False
    assert empty["capacity_bytes"] == 7000 + layers.HISTORY_BUDGET and empty["used_bytes"] == 7000
    assert 85 <= empty["remaining_percent"] <= 95
    busy = layers.usage_report(**fixed, summary_bytes=3000, summary_segments=2, half_bytes=10_000, half_turns=7,
                               recent_bytes=20_000, recent_turns=4, hidden_turns=0)
    parts = {p["key"]: p for p in busy["parts"]}
    assert [p["key"] for p in busy["parts"]] == ["system", "tools", "summary", "compressed", "recent", "free"]
    assert parts["summary"]["segments"] == 2 and parts["compressed"]["turns"] == 7 and parts["recent"]["turns"] == 4
    assert sum(p["bytes"] for p in busy["parts"]) == busy["capacity_bytes"]
    assert busy["compressing"] is True and parts["recent"]["tokens"] == layers.estimate_tokens(20_000)
    full = layers.usage_report(**fixed, summary_bytes=0, summary_segments=0, half_bytes=0, half_turns=0,
                               recent_bytes=layers.HISTORY_BUDGET + 500, recent_turns=9, hidden_turns=0)
    assert full["remaining_bytes"] == 0 and full["remaining_percent"] == 0


# --- the store's layers (disposable database) -------------------------------------------------------

def _run_with_store(exercise):
    """One event loop per test; the store's pooled connections are dropped before it ends."""
    from app import agent_chat_store as store
    from app.agent_chat_models import (AgentConversation, AgentConversationSummary, AgentConversationTitle,
                                       AgentMessage)
    from app.database import Base, engine
    Base.metadata.create_all(engine, tables=[AgentConversation.__table__, AgentMessage.__table__,
                                              AgentConversationTitle.__table__, AgentConversationSummary.__table__])

    async def wrapper():
        try:
            return await exercise(store)
        finally:
            await store.engine.dispose()

    return asyncio.run(wrapper())


async def _say(store, cid, n, user, answer, *, with_tool=False, status="complete"):
    turn = TURN.format(n)
    await store.append_message(cid, turn, "user", user)
    if with_tool:
        await store.append_message(cid, turn, "assistant", "", tool_calls={"calls": [{"id": f"c{n}"}]})
        await store.append_message(cid, turn, "tool", json.dumps({"ok": True, "data": ["证据" * 50]}), tool_call_id=f"c{n}")
    await store.append_message(cid, turn, "assistant", answer, status=status)


def test_a_conversation_that_fits_layer_one_is_sent_exactly_as_before(disposable_database):
    async def exercise(store):
        cid = await store.create_conversation(MINE)
        for n in range(1, 4):
            await _say(store, cid, n, f"第{n}个问题", f"第{n}个答复", with_tool=n == 2)
        context = await store.conversation_context(cid, MINE)
        before = await store.history(cid, MINE, limit_bytes=layers.L1_BYTES)
        return context, before

    context, before = _run_with_store(exercise)
    assert context["l1_rows"] == before and len(before) == 3 * 2 + 2              # one turn has a tool batch
    assert context["half_turns"] == [] and context["overflow"] == [] and context["segments"] == []
    assert layers.prefix_messages(context["segments"], context["half_turns"]) == []


def test_older_turns_become_half_compressed_text_and_failed_turns_are_not_in_any_layer(disposable_database):
    async def exercise(store):
        cid = await store.create_conversation(MINE)
        for n in range(1, 7):
            await _say(store, cid, n, f"第{n}个问题 苹果", f"答复{n}" + "。很长的答复" * 60, with_tool=True)
        await _say(store, cid, 7, "这一轮失败了", "", status="failed")
        return await store.conversation_context(cid, MINE, l1_bytes=2500, l2_bytes=2500)

    context = _run_with_store(exercise)
    raw_users = [r["content"] for r in context["l1_rows"] if r["role"] == "user"]
    half = context["half_turns"]
    assert raw_users and raw_users[-1] == "第6个问题 苹果"
    assert half and all(t.assistant for t in half)
    assert [t.user for t in half][-1] == f"第{6 - len(raw_users)}个问题 苹果"       # the turn just before layer 1
    assert half[0].first_sequence < half[-1].first_sequence                        # oldest first
    texts = " ".join(t.user + t.assistant for t in half) + " ".join(r["content"] for r in context["l1_rows"])
    assert "这一轮失败了" not in texts
    assert "证据证据" not in " ".join(t.assistant for t in half)                   # tool results are left out of layer 2
    assert context["overflow"] == [] or context["overflow"][-1].last_sequence < half[0].first_sequence


def test_what_fits_neither_layer_is_the_overflow_until_a_segment_covers_it(disposable_database):
    async def exercise(store):
        cid = await store.create_conversation(MINE)
        for n in range(1, 9):
            await _say(store, cid, n, f"第{n}个问题 英伟达" + "问" * 40, "答" * 60)
        first = await store.conversation_context(cid, MINE, l1_bytes=900, l2_bytes=900)
        covered = first["overflow"][-1].last_sequence
        added = await store.append_summary(cid, first["overflow"][0].first_sequence, covered, "用户先问了英伟达的几个问题")
        again = await store.append_summary(cid, 1, covered, "重复")                  # does not move forward
        second = await store.conversation_context(cid, MINE, l1_bytes=900, l2_bytes=900)
        return first, added, again, second, covered

    first, added, again, second, covered = _run_with_store(exercise)
    assert first["overflow"] and first["segments"] == []
    assert added is True and again is False
    assert second["overflow"] == [] and second["segments"][-1]["content"] == "用户先问了英伟达的几个问题"
    assert second["summary_upto"] == covered
    assert all(t.first_sequence > covered for t in second["half_turns"])            # covered turns are not sent twice


def test_a_stranger_gets_no_context_and_cannot_append_to_a_foreign_range(disposable_database):
    async def exercise(store):
        cid = await store.create_conversation(MINE)
        await _say(store, cid, 1, "问题", "答复")
        return await store.conversation_context(cid, THEIRS), await store.conversation_context("not-a-uuid", MINE)

    assert _run_with_store(exercise) == (None, None)


# --- the stream: what the model is sent, and the guards ------------------------------------------------

def test_the_model_gets_the_note_then_the_older_turns_then_the_recent_ones(stream_fixture, monkeypatch):
    import app.agent_chat_stream as stream
    send, classification, _, _, _, requests, _ = stream_fixture
    classification["intent"] = "general"

    async def context(*_a, **_k):
        return {"l1_rows": [{"role": "user", "content": "近处的问题", "status": "complete", "turn_id": "t2"},
                            {"role": "assistant", "content": "近处的答复", "status": "complete", "turn_id": "t2"}],
                "l1_turns": 1, "l1_bytes": 90,
                "half_turns": [layers.Turn(1, 2, "很早的问题 苹果", "很早的答复")],
                "overflow": [], "segments": [{"seq": 1, "content": "更早：用户只看 10-Q"}]}

    monkeypatch.setattr(stream, "conversation_context", context)
    asyncio.run(send("Explain prime numbers"))
    # C1/C12: stages receive the same layered history as a labelled JSON field.
    payloads=[json.loads(messages[-1]['content']) for messages,_schemas,_base in requests if messages[-1]['content'].startswith('{')]
    payload=next(value for value in payloads if 'history' in value and '近处的问题' in value['history'])
    sent=json.loads(payload['history']);contents=[m['content'] for m in sent]
    note=next(c for c in contents if c.startswith(layers.MEMORY_MARKER))
    assert '更早：用户只看 10-Q' in note
    assert contents.index(note)<contents.index('很早的问题 苹果')<contents.index('近处的问题')
    assert next(m for m in sent if m['content']==note)['role']=='system'



def test_the_system_prompt_says_the_memory_note_is_not_evidence():
    import app.agent_chat_stream as stream
    assert 'Earlier conversation (memory' in stream.SYSTEM and "never cited" in stream.SYSTEM


def test_the_request_guard_is_the_budget_not_a_fixed_forty_kilobytes(stream_fixture, monkeypatch):
    import app.agent_chat_stream as stream
    send, classification, _, _, _, _, _ = stream_fixture
    classification["intent"] = "general"

    async def big(*_a, **_k):
        # 90 KB of earlier conversation: over the old 40 KB guard, inside the 128 KB one
        rows = [{"role": "user", "content": "问" * 3000, "status": "complete", "turn_id": f"t{n}"} for n in range(10)]
        return {"l1_rows": rows}

    monkeypatch.setattr(stream, "conversation_context", big)
    wire = asyncio.run(send("Explain prime numbers"))
    assert "chat_context_too_large" not in wire and "event: done" in wire
    monkeypatch.setattr(layers, "CONTEXT_GUARD_BYTES", 40_000)                    # the old guard
    refused = asyncio.run(send("Explain prime numbers"))
    assert "event: error" in refused and "chat_context_too_large" in refused and "event: done" not in refused


# --- the usage endpoint --------------------------------------------------------------------------------

def test_the_usage_endpoint_reports_the_parts_and_refuses_strangers(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.agent_chat_stream as stream

    seen = []

    async def context(conversation_id, credential, **_k):
        seen.append((conversation_id, credential))
        if credential != "o" * 48:
            return None
        return {"l1_rows": [], "l1_turns": 3, "l1_bytes": 4000,
                "half_turns": [layers.Turn(1, 2, "问" * 50, "答" * 50)], "overflow": [],
                "segments": [{"seq": 1, "content": "摘要" * 20}]}

    monkeypatch.setattr(stream, "conversation_context", context)
    app = FastAPI()
    app.include_router(stream.router)
    cid = str(uuid4())
    with TestClient(app) as client:
        mine = client.get(f"/agent-chat/conversations/{cid}/context", headers={"X-Chat-Session": "o" * 48})
        stranger = client.get(f"/agent-chat/conversations/{cid}/context", headers={"X-Chat-Session": "z" * 48})
        anonymous = client.get(f"/agent-chat/conversations/{cid}/context")
        fresh = client.get("/agent-chat/context")
    assert mine.status_code == 200
    body = mine.json()
    parts = {p["key"]: p for p in body["parts"]}
    assert parts["system"]["bytes"] > 1000 and parts["tools"]["bytes"] > 1000
    assert parts["recent"]["bytes"] == 4000 and parts["recent"]["turns"] == 3
    assert parts["compressed"]["turns"] == 1 and parts["summary"]["segments"] == 1 and parts["summary"]["bytes"] > 0
    assert body["compressing"] is True and sum(p["bytes"] for p in body["parts"]) == body["capacity_bytes"]
    assert stranger.status_code == 404 and anonymous.status_code == 404
    empty = fresh.json()
    assert fresh.status_code == 200 and empty["compressing"] is False
    assert {p["key"]: p["bytes"] for p in empty["parts"]}["recent"] == 0


def test_a_guest_may_read_the_usage_of_their_own_conversation_in_public_mode(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.agent_chat_stream as stream
    from app.agent_chat_middleware import create_agent_chat_access_middleware

    async def context(*_a, **_k):
        return {"l1_rows": [], "l1_turns": 0, "l1_bytes": 0, "half_turns": [], "overflow": [], "segments": []}

    monkeypatch.setattr(stream, "conversation_context", context)
    app = FastAPI()
    app.include_router(stream.router)
    app.middleware("http")(create_agent_chat_access_middleware(True, "operator-key"))
    cid = str(uuid4())
    with TestClient(app) as client:
        ok = client.get(f"/agent-chat/conversations/{cid}/context", headers={"X-Chat-Session": "g" * 48})
        no_session = client.get(f"/agent-chat/conversations/{cid}/context")
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store"
    assert no_session.status_code == 401 and no_session.json()["detail"] == "chat_session_required"
