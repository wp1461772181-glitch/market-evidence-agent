"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import asyncio
import json
from app import agent_context as layers
import logging
import pytest
from app import agent_context_summary as summary

KEY = "sk-test-secret-key-0123456789"


SOURCE = "用户想看苹果 AAPL 的营收，只看 10-Q。助手找到了两篇新闻，标题是 Apple beats revenue expectations。"


def test_a_grounded_segment_is_accepted_even_inside_a_code_fence():
    raw = '```json\n{"summary": "用户想看苹果的营收，只看 10-Q；找到过一篇关于营收超预期的新闻。"}\n```'
    assert summary.validate_summary(raw, SOURCE).startswith("用户想看苹果")


@pytest.mark.parametrize("raw, reason", [
    ("not json at all", "not_json"),
    (None, "not_text"),
    ('["summary"]', "not_json"),
    ('{"summary": "ok", "extra": 1}', "schema"),
    ('{"summary": 5}', "schema"),
    ('{"summary": "   "}', "empty"),
    (json.dumps({"summary": "用户关注苹果。" * 80}, ensure_ascii=False), "too_long"),
    ('{"summary": "用户还问过英伟达的数据中心业务。"}', "ungrounded_company"),
    ('{"summary": "苹果营收为 940 亿美元。"}', "ungrounded_figure"),
])
def test_a_segment_that_adds_a_company_a_figure_or_the_wrong_shape_is_rejected(raw, reason):
    with pytest.raises(summary.SummaryRejected) as caught:
        summary.validate_summary(raw, SOURCE)
    assert str(caught.value) == reason


def test_one_call_takes_the_oldest_turns_up_to_its_input_budget():
    turns = [layers.Turn(2 * n - 1, 2 * n, f"问题{n}" + "问" * 400, "答" * 400) for n in range(1, 40)]
    batch = summary.pending_batch(turns)
    assert batch == turns[:len(batch)] and 1 <= len(batch) < len(turns)
    assert sum(layers.half_size(t) for t in batch) <= summary.MAX_INPUT_BYTES + layers.half_size(batch[-1])
    giant = [layers.Turn(1, 2, "问" * 5000, "答" * 5000)]
    assert summary.pending_batch(giant) == giant                                   # one turn is always taken


def _patch_store(monkeypatch, context, appended):
    import app.agent_chat_store as store

    async def conversation_context(conversation_id, credential, **_k):
        return context

    async def append_summary(conversation_id, from_sequence, upto_sequence, content):
        appended.append((conversation_id, from_sequence, upto_sequence, content))
        return True

    monkeypatch.setattr(store, "conversation_context", conversation_context)
    monkeypatch.setattr(store, "append_summary", append_summary)


def _overflow(n=8):
    return [layers.Turn(2 * i - 1, 2 * i, f"第{i}个问题：苹果 AAPL 的营收" + "问" * 200, "答" * 200) for i in range(1, n + 1)]


def _patch_model(monkeypatch, reply, calls):
    import app.agent_chat_provider as provider

    async def decision_request(provider_name, model, api_key, messages, schemas, base_url=None):
        calls.append({"provider": provider_name, "model": model, "messages": messages, "schemas": schemas})
        return {"usage": {"total_tokens": 10}}, {"content": reply}

    monkeypatch.setattr(provider, "decision_request", decision_request)


def test_the_segment_is_appended_for_exactly_the_turns_it_summarized(monkeypatch):
    appended, calls = [], []
    context = {"overflow": _overflow(), "segments": [{"seq": 1, "content": "更早的一段"}]}
    _patch_store(monkeypatch, context, appended)
    _patch_model(monkeypatch, json.dumps({"summary": "用户连续询问苹果 AAPL 的营收。"}, ensure_ascii=False), calls)
    batch = summary.pending_batch(context["overflow"])
    done = asyncio.run(summary.summarize_pending("cid", "credential", provider="deepseek", model="m",
                                                 api_key=KEY, base_url=None))
    assert done is True
    assert appended == [("cid", batch[0].first_sequence, batch[-1].last_sequence, "用户连续询问苹果 AAPL 的营收。")]
    sent = json.loads(calls[0]["messages"][1]["content"])
    assert sent["earlier_segments"] == ["更早的一段"] and len(sent["turns"]) == len(batch)
    assert calls[0]["schemas"] == [] and calls[0]["messages"][0]["content"] == summary.SUMMARY_PROMPT
    assert "untrusted" in summary.SUMMARY_PROMPT


def test_nothing_is_asked_when_too_little_is_waiting(monkeypatch):
    appended, calls = [], []
    _patch_store(monkeypatch, {"overflow": [layers.Turn(1, 2, "你好", "你好")], "segments": []}, appended)
    _patch_model(monkeypatch, "{}", calls)
    assert asyncio.run(summary.summarize_pending("cid", "c", provider="p", model="m", api_key=KEY, base_url=None)) is False
    _patch_store(monkeypatch, {"overflow": [], "segments": []}, appended)
    assert asyncio.run(summary.summarize_pending("cid", "c", provider="p", model="m", api_key=KEY, base_url=None)) is False
    _patch_store(monkeypatch, None, appended)
    assert asyncio.run(summary.summarize_pending("cid", "c", provider="p", model="m", api_key=KEY, base_url=None)) is False
    assert calls == [] and appended == []


@pytest.mark.parametrize("reply", ['{"summary": "用户还问过英伟达。"}', "I cannot do that", '{"summary": ""}'])
def test_a_rejected_or_failed_segment_changes_nothing_and_never_raises_to_the_turn(monkeypatch, caplog, reply):
    appended, calls = [], []
    _patch_store(monkeypatch, {"overflow": _overflow(), "segments": []}, appended)
    _patch_model(monkeypatch, reply, calls)
    with caplog.at_level(logging.INFO):
        asyncio.run(summary._guarded("cid", "credential", provider="deepseek", model="m", api_key=KEY, base_url=None))
    assert appended == []
    assert KEY not in caplog.text and "credential" not in caplog.text


def test_a_provider_failure_is_swallowed_and_the_key_is_never_logged(monkeypatch, caplog):
    import app.agent_chat_provider as provider
    from fastapi import HTTPException

    async def boom(*_a, **_k):
        raise HTTPException(401, "provider_invalid_key")

    appended = []
    _patch_store(monkeypatch, {"overflow": _overflow(), "segments": []}, appended)
    monkeypatch.setattr(provider, "decision_request", boom)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(summary._guarded("cid", "credential", provider="deepseek", model="m", api_key=KEY, base_url=None))
    assert appended == [] and KEY not in caplog.text


def test_one_summary_task_per_conversation_and_scheduling_never_raises(monkeypatch):
    started = []

    async def slow(conversation_id, credential, **kwargs):
        started.append(conversation_id)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(summary, "_guarded", slow)

    async def exercise():
        summary.schedule_summary("c1", "cred", provider="p", model="m", api_key=KEY)
        summary.schedule_summary("c1", "cred", provider="p", model="m", api_key=KEY)    # already running
        summary.schedule_summary("c2", "cred", provider="p", model="m", api_key=KEY)
        await asyncio.sleep(0.15)
        summary.schedule_summary("c1", "cred", provider="p", model="m", api_key=KEY)    # finished: may run again
        await asyncio.sleep(0.15)

    asyncio.run(exercise())
    assert started == ["c1", "c2", "c1"] and summary._RUNNING == {}
    summary.schedule_summary("c3", "cred", provider="p", model="m", api_key=KEY)          # no running loop: no raise
    assert "c3" not in summary._RUNNING


def test_the_whole_cycle_on_a_real_conversation_and_no_key_in_the_database(disposable_database, monkeypatch):
    from app import agent_chat_store as store
    from app.agent_chat_models import (AgentConversation, AgentConversationSummary, AgentConversationTitle,
                                       AgentMessage)
    from app.database import Base, engine
    from sqlalchemy import text
    Base.metadata.create_all(engine, tables=[AgentConversation.__table__, AgentMessage.__table__,
                                              AgentConversationTitle.__table__, AgentConversationSummary.__table__])
    calls = []
    _patch_model(monkeypatch, json.dumps({"summary": "用户连续询问苹果 AAPL 的营收，只看 10-Q。"}, ensure_ascii=False), calls)

    async def exercise():
        try:
            cid = await store.create_conversation("owner-credential-" + "c" * 40)
            for n in range(1, 13):
                turn = f"22222222-2222-4222-8222-{n:012d}"
                await store.append_message(cid, turn, "user", f"第{n}个问题：苹果 AAPL 的营收和 10-Q" + "问" * 120)
                await store.append_message(cid, turn, "assistant", "答复" + "答" * 200)
            before = await store.conversation_context(cid, "owner-credential-" + "c" * 40, l1_bytes=1500, l2_bytes=1500)
            done = await summary.summarize_pending(cid, "owner-credential-" + "c" * 40, provider="deepseek",
                                                   model="m", api_key=KEY, base_url=None,
                                                   l1_bytes=1500, l2_bytes=1500)
            after = await store.conversation_context(cid, "owner-credential-" + "c" * 40, l1_bytes=1500, l2_bytes=1500)
            return before, done, after
        finally:
            await store.engine.dispose()

    before, done, after = asyncio.run(exercise())
    assert before["overflow"] and before["segments"] == []
    assert done is True and after["segments"][-1]["content"].startswith("用户连续询问苹果")
    assert len(after["overflow"]) < len(before["overflow"])
    assert all(t.first_sequence > after["summary_upto"] for t in after["half_turns"] + after["overflow"])
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        stored = " ".join(str(value) for table in ("agent_messages", "agent_conversation_summaries")
                          for row in db.execute(text(f"SELECT * FROM {table}")).all() for value in row)
    assert KEY not in stored and "sk-test" not in stored
