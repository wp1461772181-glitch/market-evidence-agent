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


def test_sidecars_do_not_change_the_size_of_a_message():
    row = {"role": "assistant", "content": "done", "turn_id": "t", "status": "complete",
           "tool_calls": {"calls": [], "citations": [{"quote": "x" * 20_000}],
                          "audit": {"data": "x" * 20_000}, "trace": "x" * 20_000}}
    expected = layers.row_bytes({**row, "tool_calls": {"calls": []}})
    assert layers.row_bytes(row) == expected


def test_gate_b_counts_user_and_final_content_but_not_tool_protocol_rows():
    row = {"role": "assistant", "content": "done", "turn_id": "t", "status": "complete",
           "tool_calls": {"calls": []}}
    base = layers.row_bytes(row)
    assert layers.row_bytes({**row, "content": "done longer"}) > base
    assert layers.row_bytes({**row, "tool_calls": {"calls": [{"id": "c", "function": {"name": "lookup"}}]}}) == 0
    assert layers.row_bytes({"role": "tool", "content": "x" * 40000}) == 0


def test_turn_projection_and_catalogue_only_use_answer_markers():
    group = [{"role":"user","content":"q","turn_id":"tid","sequence":1,"status":"complete"},
             {"role":"assistant","content":"","turn_id":"tid","sequence":2,"status":"complete",
              "tool_calls":{"calls":[{"id":"c"}]}},
             {"role":"tool","content":"TOOL_BODY_PRIVATE","turn_id":"tid","sequence":3,"status":"complete"},
             {"role":"assistant","content":"answer [1] [3]","turn_id":"tid","sequence":4,"status":"complete",
              "tool_calls":{"citations":[{"ordinal":1,"title":"A","source_type":"filing","quote":"SECRET"},
                                          {"ordinal":2,"title":"B","source_type":"filing","quote":"SECRET"},
                                          {"ordinal":3,"title":"C","source_type":"filing"}]}}]
    assert layers.project_turn_history(group) == [{"role":"user","content":"q"},{"role":"assistant","content":"answer [1] [3]"}]
    entry = layers.turn_catalogue_entry(group)
    assert [x["ordinal"] for x in entry["sources"]] == [1,3]
    rendered = layers.render_citation_catalogue([entry])["content"]
    assert "SECRET" not in rendered and "http" not in rendered and "TOOL_BODY_PRIVATE" not in rendered


def test_catalogue_is_excluded_from_conversation_but_rendered_as_a_system_locator():
    note = layers.render_citation_catalogue([{"turn_id":"t","opening":"answer","sources":[],"answers_back":1}])
    assert layers.is_catalogue_message(note)
    assert layers.conversation_messages([{"role":"user","content":"question"}, note]) == [{"role":"user","content":"question"}]


def test_l1_rows_keep_complete_citation_and_audit_sidecars():
    rows=[{"role":"user","content":"q","turn_id":"t","sequence":1,"status":"complete"},
          {"role":"assistant","content":"answer","turn_id":"t","sequence":2,"status":"complete",
           "tool_calls":{"citations":[{"ordinal":1,"quote":"snapshot"}],"audit":{"terminal_status":"succeeded"},"tool_trace":[{"name":"read"}]}}]
    result=layers.layer_rows(rows,[],l1_bytes=4096,l2_bytes=4096,l3_bytes=4096)
    stored=result["l1_rows"][-1]["tool_calls"]
    assert stored["citations"][0]["quote"]=="snapshot" and stored["audit"]["terminal_status"]=="succeeded"


def _citation_group(n, answer, citations, tool_body=""):
    tid=f"turn-{n}"
    group=[{"role":"user","content":f"question {n}","turn_id":tid,"sequence":3*n-2,"status":"complete"},
           {"role":"tool","content":tool_body,"turn_id":tid,"sequence":3*n-1,"status":"complete"},
           {"role":"assistant","content":answer,"turn_id":tid,"sequence":3*n,"status":"complete",
            "tool_calls":{"citations":citations}}]
    return group


def test_heavy_tool_batch_does_not_push_turns_out_of_layer_one():
    rows=[row for n in range(1,4) for row in _citation_group(n,"answer",[],"x"*40000)]
    result=layers.layer_rows(rows,[],l1_bytes=4096,l2_bytes=4096,l3_bytes=4096)
    assert result["l1_turns"]==3


def test_visible_answers_set_answers_back_even_without_citations():
    a={"ordinal":1,"title":"source","source_type":"filing","quote":"snapshot"}
    rows=[*_citation_group(1,"old [1]",[a]), *_citation_group(2,"middle",[]), *_citation_group(3,"new [1]",[a])]
    result=layers.layer_rows(rows,[],l1_bytes=4096,l2_bytes=4096,l3_bytes=4096)
    assert [entry["answers_back"] for entry in result["catalogue"]]==[1,3]


def test_window_account_matches_emitted_history_and_catalogue_within_item_margin():
    group=_citation_group(1,"answer [1]",[{"ordinal":1,"title":"filing","source_type":"official_filing","quote":"private quote"}])
    result=layers.layer_rows(group,[],l1_bytes=4096,l2_bytes=4096,l3_bytes=0)
    sent=sum(layers.json_bytes(m) for m in layers.project_turn_history(group))
    sent+=layers.json_bytes(layers.render_citation_catalogue(result["catalogue"]))
    assert sent == result["l1_bytes"]


def test_summary_covered_citation_does_not_bill_an_unrendered_catalogue_header():
    cited = {"ordinal": 1, "title": "filing", "source_type": "official_filing", "quote": "snapshot"}
    rows = [*_citation_group(1, "old [1]", [cited]), *_citation_group(2, "x" * 2000, [])]
    segments = [{"seq": 1, "from_sequence": 1, "upto_sequence": 2, "content": "older summary"}]
    result = layers.layer_rows(rows, segments, l1_bytes=1000, l2_bytes=4096)
    assert not result["catalogue"] and result["half_turns"]
    assert result["l1_bytes"] == sum(layers.json_bytes(row) for row in result["l1_rows"])


def test_half_compressed_turn_keeps_late_marker_and_catalogue_costs_budget():
    source={"ordinal":3,"title":"filing","source_type":"official_filing","quote":"snapshot"}
    group=_citation_group(1,"x"*180+" [3]",[source])
    half=layers.layer_rows(group,[],l1_bytes=0,l2_bytes=4096,l3_bytes=0)
    assert len(half["half_turns"])==1 and half["catalogue"][0]["sources"][0]["ordinal"]==3
    projected=sum(layers.json_bytes(m) for m in layers.project_turn_history(group))
    l1=layers.layer_rows(group,[],l1_bytes=projected,l2_bytes=4096,l3_bytes=0)
    assert l1["l1_turns"]==0 and len(l1["half_turns"])==1


# --- the knob and what it sets ----------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [(None, 64), ("", 64), ("abc", 64), ("64", 64), ("32", 32),
                                           ("8", 16), ("0", 16), ("500", 128), ("-5", 16), (" 96 ", 96)])
def test_the_budget_knob_is_clamped_and_unreadable_values_are_the_default(raw, expected):
    assert layers.parse_budget_kb(raw) == expected


def test_the_layers_and_guards_follow_one_budget():
    assert layers.HISTORY_BUDGET == layers.BUDGET_KB * 1024
    assert layers.L1_BYTES + layers.L2_BYTES + layers.L3_BYTES <= layers.HISTORY_BUDGET
    assert layers.CONTEXT_GUARD_BYTES == 2 * layers.HISTORY_BUDGET
    # the three layers spend exactly the budget, newest first
    assert layers.L1_BYTES + layers.L2_BYTES + layers.L3_BYTES == layers.HISTORY_BUDGET
    assert layers.L1_BYTES > layers.L2_BYTES > layers.L3_BYTES > 0
    # the budget comes from the window, and the knob can only lower it
    assert layers.BUDGET_KB <= layers.derive_history_budget(layers.WINDOW_TOKENS) // 1024
    assert layers.startup_self_check()["budget_kb"] == layers.BUDGET_KB


def test_a_smaller_window_tightens_the_budget_by_itself(monkeypatch):
    big, small = layers.derive_history_budget(128_000), layers.derive_history_budget(16_000)
    assert big > small >= layers.BUDGET_KB_MIN * 1024
    assert layers.parse_budget_kb("64", ceiling=small // 1024) == small // 1024
    assert layers.parse_budget_kb("64", ceiling=big // 1024) == 64


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


def test_the_summary_is_one_note_that_says_who_wrote_it_and_that_it_is_not_evidence():
    assert layers.summary_message([]) is None
    note = layers.summary_message([{"content": "用户关注苹果的营收"}, {"content": "之后改看英伟达"}])
    assert note["role"] == "user" and note["content"].startswith(layers.MEMORY_MARKER)
    assert "not by the user" in note["content"]
    assert "not evidence" in note["content"] and "not something to cite" in note["content"]
    assert "1. 用户关注苹果的营收" in note["content"] and "2. 之后改看英伟达" in note["content"]
    # the note is memory, not part of the conversation a later stage may quote
    assert layers.is_memory_message(note) and not layers.is_memory_message({"role": "user", "content": "你好"})
    assert layers.conversation_messages([{"role": "user", "content": "你好"}, note]) == [{"role": "user", "content": "你好"}]


def test_the_note_goes_after_the_question_and_the_head_is_only_the_turns():
    messages = layers.history_messages([_turn(1, "甲"), _turn(2, "乙")])
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "甲" and messages[2]["content"] == "乙"
    assert layers.history_messages([]) == []
    # the whole request: system, layer 2, layer 1, the question, then the note.  Everything before
    # the question is a byte-identical prefix from one turn to the next.
    note = layers.summary_message([{"content": "摘要"}])
    request = [{"role": "system", "content": "system"}] + messages + [{"role": "user", "content": "本轮"}] + [note]
    assert [m["role"] for m in request][-2:] == ["user", "user"]
    assert request[-2]["content"] == "本轮" and request[-1] is note


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
    assert layers.history_messages(context["half_turns"]) == []


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
    assert contents.index('很早的问题 苹果')<contents.index('近处的问题')<contents.index(note)
    assert next(m for m in sent if m['content']==note)['role']=='user'



def test_the_system_prompt_says_the_memory_note_is_not_evidence():
    import app.agent_chat_stream as stream
    assert 'Earlier conversation (memory' in stream.SYSTEM and "never cited" in stream.SYSTEM


def test_the_request_guard_is_the_budget_not_a_fixed_forty_kilobytes(stream_fixture, monkeypatch):
    import app.agent_chat_stream as stream
    send, classification, _, _, _, _, _ = stream_fixture
    classification["intent"] = "general"

    async def big(*_a, **_k):
        # 90 KB of earlier conversation: over the old 40 KB guard, inside the 128 KB one
        rows = []
        for n in range(10):
            tid = f"t{n}"
            rows.extend([{"role": "user", "content": "问" * 1500, "status": "complete", "turn_id": tid},
                         {"role": "assistant", "content": "答" * 1500, "status": "complete", "turn_id": tid}])
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
