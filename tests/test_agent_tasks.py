"""Scheduled tasks, inbox, background key and alerts (personal agent plan §2.3).

* schedule math: daily / weekdays / hourly in the task's time zone; a Mac that slept through slots runs
  the task once on waking (catch-up once), then waits for the next slot after now;
* an unattended run never writes: the classifier's grants are dropped, a direct write call is refused, and
  a write the model suggests lands in the inbox's 待你确认 without a capability; the card is minted only when
  the owner asks for it, and the write runs only when that card is pressed (once);
* a revoked background key leaves the task 未运行：缺少模型; the key is never returned;
* token caps per run and per day; a failed run is retried only when it queued no write suggestion;
* alerts (filings, news leads, price lines) are 简报 entries with a numbered source, never duplicated.
"""
import asyncio
import json
import plistlib
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import agent_task_runner as runner
from app import agent_tasks as tasks
from app.agent_chat_models import (AgentBackgroundModel, AgentInboxItem, AgentResearchQueue, AgentTask)
from app.agent_chat_store import owner_hash
from app.agent_workflow import consent
from app.agent_workflow.models import AgentConsent
from app.database import Base, SessionLocal, engine

OWNER = "owner"
OWNER_HASH = owner_hash(OWNER)
SHANGHAI = ZoneInfo("Asia/Shanghai")
KEY = "sk-test-background-0123456789"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def at(*args, zone=SHANGHAI):
    return datetime(*args, tzinfo=zone).astimezone(UTC)


@pytest.fixture
def owner(monkeypatch, disposable_database):
    # The tables the API creates at startup (app/main.py), so the chat store, recall hooks and alerts all work.
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    with SessionLocal() as db:
        for model in (AgentInboxItem, AgentTask, AgentBackgroundModel, AgentResearchQueue, AgentConsent):
            db.query(model).delete()
        db.commit()
    from app.agent_workflow import direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    return OWNER


def run(coroutine):
    """asyncio.run, then drop the chat store's async connections bound to that loop."""
    from app import agent_chat_store as store

    async def wrapped():
        try:
            return await coroutine
        finally:
            await store.engine.dispose()
    return asyncio.run(wrapped())


def _brief(**fields):
    values = {"name": "研究队列简报", "kind": "brief", "schedule_kind": "weekdays", "schedule_time": "08:00",
              "timezone": "Asia/Shanghai", "instruction": "总结研究队列公司过去一天的新申报和新闻。"}
    values.update(fields)
    return values


def _set(task_id, **values):
    with SessionLocal() as db:
        task = db.get(AgentTask, uuid.UUID(task_id))
        for key, value in values.items():
            setattr(task, key, value)
        db.commit()


def _task(task_id) -> AgentTask:
    with SessionLocal() as db:
        task = db.get(AgentTask, uuid.UUID(task_id))
        db.expunge(task)
        return task


# --- schedule math --------------------------------------------------------------------------------

def test_weekday_schedule_skips_the_weekend_in_the_tasks_time_zone():
    # Friday 2026-10-30, 07:00 Shanghai: today's 08:00 is next; after it, Monday 08:00.
    assert tasks.next_run("weekdays", "08:00", "Asia/Shanghai", at(2026, 10, 30, 7, 0)) == at(2026, 10, 30, 8, 0)
    assert tasks.next_run("weekdays", "08:00", "Asia/Shanghai", at(2026, 10, 30, 8, 0)) == at(2026, 11, 2, 8, 0)
    assert tasks.next_run("daily", "08:00", "Asia/Shanghai", at(2026, 10, 30, 8, 0)) == at(2026, 10, 31, 8, 0)


def test_hourly_schedule_uses_the_minute_and_never_returns_now():
    assert tasks.next_run("hourly", "00:15", "Asia/Shanghai", at(2026, 10, 30, 9, 15)) == at(2026, 10, 30, 10, 15)
    assert tasks.next_run("hourly", "23:15", "Asia/Shanghai", at(2026, 10, 30, 9, 16)) == at(2026, 10, 30, 10, 15)
    # India is UTC+05:30: minute 15 local is minute 45 UTC, and the hour still advances by one.
    assert tasks.next_run("hourly", "00:15", "Asia/Kolkata", datetime(2026, 10, 30, 3, 50, tzinfo=UTC)) == \
        datetime(2026, 10, 30, 4, 45, tzinfo=UTC)


def test_daily_schedule_follows_daylight_saving_in_new_york():
    ny = ZoneInfo("America/New_York")
    # 2026-11-01 is the autumn change: 08:00 local is 12:00 UTC before it (EDT) and 13:00 UTC after (EST).
    assert tasks.next_run("daily", "08:00", "America/New_York", datetime(2026, 10, 30, 13, 0, tzinfo=UTC)) == \
        datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    first = tasks.next_run("daily", "08:00", "America/New_York", datetime(2026, 10, 31, 13, 0, tzinfo=UTC))
    assert first == datetime(2026, 11, 1, 8, 0, tzinfo=ny).astimezone(UTC) == datetime(2026, 11, 1, 13, 0, tzinfo=UTC)
    # A time the spring change skips (02:30 on 2026-03-08) still yields one slot that day.
    spring = tasks.next_run("daily", "02:30", "America/New_York", datetime(2026, 3, 8, 5, 0, tzinfo=UTC))
    assert spring.date() == date(2026, 3, 8)


def test_invalid_schedules_are_refused():
    for kind, clock, zone in (("monthly", "08:00", "UTC"), ("daily", "24:00", "UTC"), ("daily", "8:00", "UTC"),
                              ("daily", "08:00", "Mars/Olympus")):
        with pytest.raises(tasks.TaskError):
            tasks.next_run(kind, clock, zone, datetime(2026, 10, 30, tzinfo=UTC))


def test_slots_missed_in_sleep_run_once_then_wait_for_the_next_slot(owner):
    task = tasks.create_task(OWNER_HASH, {"name": "价格线", "kind": "alerts", "schedule_kind": "weekdays",
                                          "schedule_time": "08:00", "timezone": "Asia/Shanghai", "scope": "symbols",
                                          "symbols": ["NVDA"], "config": {"news": False}},
                             now=at(2026, 10, 30, 7, 0))
    assert task["next_run_at"] == at(2026, 10, 30, 8, 0).isoformat()
    # The Mac slept from Friday 07:00 to Monday 09:30: Friday's and Monday's slots were both missed.
    woke = at(2026, 11, 2, 9, 30)
    row = _task(task["id"])
    assert tasks.is_due(row, woke) and tasks.missed_slots(row, woke) == 2
    assert run(runner.run_due(now=woke, credential=OWNER)) == {task["id"]: "ok"}
    after = _task(task["id"])
    assert after.next_run_at == at(2026, 11, 3, 8, 0) and after.last_run_at == woke
    # The next tick five minutes later runs nothing: the missed slots collapsed into that one run.
    assert run(runner.run_due(now=woke + timedelta(minutes=5), credential=OWNER)) == {}
    assert run(runner.run_due(now=at(2026, 11, 3, 8, 1), credential=OWNER)) == {task["id"]: "ok"}


def test_a_disabled_task_is_never_due(owner):
    task = tasks.create_task(OWNER_HASH, _brief(), now=at(2026, 10, 30, 7, 0))
    tasks.update_task(OWNER_HASH, task["id"], {"enabled": False})
    assert run(runner.run_due(now=at(2026, 10, 30, 9, 0), credential=OWNER)) == {}


# --- unattended turn: no write runs ---------------------------------------------------------------

def _call(name, arguments, call_id):
    return {"tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def _fake_workflow(monkeypatch, *, recognizer, decider, executor, seen):
    """graph.stream_workflow with the real Turn, injected model callbacks and no checkpointer."""
    from app.agent_workflow import direct, graph
    from app.agent_workflow.state import AgentRuntimeContext

    async def stream_workflow(*, question, messages, provider, model, api_key, base_url, owner_hash, conversation_id,
                              identity_credential="", turn_id="", profile_block=None, unattended=False,
                              token_budget=None, action_sink=None, **_):
        seen.append({"unattended": unattended, "token_budget": token_budget, "key": api_key, "model": model})
        context = AgentRuntimeContext(provider, model, api_key, base_url=base_url, owner_hash=owner_hash,
                                      conversation_id=conversation_id, identity_credential=identity_credential,
                                      intent_recognizer=recognizer, decider=decider, executor=executor,
                                      profile_block=profile_block, unattended=unattended, token_budget=token_budget,
                                      action_sink=action_sink)
        events = []

        async def emit(event):
            events.append(event)
        result = await direct.run_direct_turn(question, context, emit=emit)
        for event in events:
            yield event
        yield {"event": "delta", "text": result["answer"]}
        yield {"event": "done", "status": result.get("status"), "reason": result.get("reason"),
               "citations": result.get("citations", []), "pending_request": result.get("pending_request"),
               "final_protocol": {}, "audit": result.get("audit", {})}
    monkeypatch.setattr(graph, "stream_workflow", stream_workflow)


def _with_key():
    tasks.save_background_model(OWNER_HASH, provider="deepseek", model="deepseek-chat", base_url=None, api_key=KEY)


def test_unattended_write_goes_to_the_inbox_and_runs_only_after_the_card_is_pressed(owner, monkeypatch):
    _with_key()
    executed = []

    def executor(name, args):
        executed.append(name)
        return {"ok": True, "data": [{"status": "queued", "job_id": "j1", "job_status": "queued"}]}
    steps = iter([
        # A direct write first (the task's words say 抓取): refused, never converted into a card.
        _call("fetch_sec", {"symbol": "NVDA", "form": "10-Q"}, "c1"),
        # Then the suggestion path: it must land in the inbox, not in a card here.
        _call("request_action", {"tool": "add_to_research_queue", "arguments": {"symbol": "AMD"}, "reason": "AMD 是 NVDA 的主要对手"}, "c2"),
        {"content": "研究队列今天没有可引用的新资料。"},
    ])
    seen = []
    # The classifier is fooled into granting the writes the task's words mention.
    _fake_workflow(monkeypatch, recognizer=lambda _p: {"category": "D", "days_back": None,
                                                       "requested_actions": ["fetch_sec", "add_to_research_queue"]},
                   decider=lambda *_: next(steps), executor=executor, seen=seen)
    task = tasks.create_task(OWNER_HASH, _brief(instruction="每天抓取研究队列的新 10-Q，有新的对手就加入研究队列。", scope="symbols",
                                                symbols=["NVDA"]), now=at(2026, 10, 30, 7, 0))
    status = run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER))
    assert status == {task["id"]: "no_evidence"}
    assert seen[0]["unattended"] is True and seen[0]["key"] == KEY
    assert executed == []
    with SessionLocal() as db:
        assert db.query(AgentConsent).count() == 0  # no capability was minted while nobody was there
    confirm = tasks.list_inbox(OWNER_HASH, section="confirm")
    assert len(confirm) == 1 and confirm[0]["kind"] == "action" and confirm[0]["status"] == "unread"
    assert "这是 Agent 的建议" in confirm[0]["title"]
    assert "operation" not in confirm[0]["payload"]  # the list shows what it is, not the stored operation
    brief = tasks.list_inbox(OWNER_HASH, section="brief")
    assert len(brief) == 1 and brief[0]["sources"] == [] and brief[0]["body"] == runner.NO_EVIDENCE_BODY
    assert _task(task["id"]).last_status == "no_evidence"

    # The owner asks for the card later: a fresh capability in the run's conversation, still nothing executed.
    from app.agent_workflow import graph
    pending = {}

    async def set_pending(owner_hash_, conversation, card):
        pending.update(owner=owner_hash_, conversation=conversation, card=card)
    monkeypatch.setattr(graph, "set_pending_request", set_pending)
    minted = run(tasks.mint_card(OWNER, confirm[0]["id"]))
    card = pending["card"]
    assert minted["conversation_id"] == confirm[0]["conversation_id"] == pending["conversation"]
    assert card["display"]["agent_suggestion"] is True and card["choices"][0]["label"] == "批准并执行"
    assert executed == []
    from app.agent_chat_store import owned_messages
    stored = run(owned_messages(minted["conversation_id"], OWNER))
    assert stored[-2]["role"] == "user" and stored[-2]["content"].startswith(tasks.CARD_REQUEST)
    assert stored[-1]["tool_calls"]["clarification"]["request_id"] == card["id"]

    # The press executes exactly once; a second press replays.
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    context = AgentRuntimeContext("custom", "test", "dummy", identity_credential=OWNER, owner_hash=OWNER_HASH,
                                  conversation_id=minted["conversation_id"], executor=executor)

    async def quiet(_event):
        pass
    for _ in range(2):
        asyncio.run(direct.run_direct_turn(card["choices"][0]["value"], context, emit=quiet,
                                           state={"pending_request": card, "requested_pending_id": card["id"]}))
    assert executed == ["add_to_research_queue"]


def test_unattended_turn_offers_no_write_tool_and_says_so_in_the_prompt(owner):
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    captured = []

    def decider(messages, schemas):
        captured.append((messages, schemas))
        return {"content": "没有新资料。"}
    sink = []
    context = AgentRuntimeContext("custom", "test", "dummy", identity_credential=OWNER, owner_hash=OWNER_HASH,
                                  conversation_id=str(uuid.uuid4()), decider=decider, unattended=True, action_sink=sink,
                                  intent_recognizer=lambda _p: {"category": "D", "days_back": None,
                                                                "requested_actions": ["import", "fetch_sec"]})

    async def quiet(_event):
        pass
    asyncio.run(direct.run_direct_turn("导入并抓取 NVDA 的最新 10-K", context, emit=quiet))
    messages, schemas = captured[0]
    offered = {s["function"]["name"] for s in schemas}
    from app.agent_tool_catalog import definitions
    writes = {n for n, p in definitions().items() if p.level in {"reversible", "costly"}}
    assert not offered & writes and "request_action" in offered
    assert direct.UNATTENDED_NOTE in messages[0]["content"]


def test_ask_user_is_refused_when_nobody_is_there(owner):
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    steps = iter([_call("ask_user", {"question": "哪家公司？", "options": ["苹果", "微软"]}, "c1"), {"content": "无资料。"}])
    context = AgentRuntimeContext("custom", "test", "dummy", identity_credential=OWNER, owner_hash=OWNER_HASH,
                                  conversation_id=str(uuid.uuid4()), decider=lambda *_: next(steps), unattended=True,
                                  action_sink=[], intent_recognizer=lambda _p: {"category": "D", "days_back": None,
                                                                                "requested_actions": []})

    async def quiet(_event):
        pass
    result = asyncio.run(direct.run_direct_turn("简报", context, emit=quiet))
    assert result["status"] != "waiting_for_input" and not result.get("pending_request")


# --- background key -------------------------------------------------------------------------------

def test_revoked_key_leaves_the_task_marked_not_run_missing_model(owner, monkeypatch):
    _with_key()
    with SessionLocal() as db:
        stored = db.get(AgentBackgroundModel, OWNER_HASH).ciphertext
    assert stored and KEY not in stored  # encrypted at rest
    summary = tasks.revoke_background_key(OWNER_HASH)
    assert summary["has_key"] is False and summary["revoked_at"] and tasks.load_background_model(OWNER_HASH) is None
    called = []
    monkeypatch.setattr(runner, "run_unattended_turn", lambda *a, **k: called.append(1))
    task = tasks.create_task(OWNER_HASH, _brief(), now=at(2026, 10, 30, 7, 0))
    assert run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER)) == {task["id"]: "skipped_no_model"}
    listed = tasks.list_tasks(OWNER_HASH)[0]
    assert listed["last_status_label"] == "未运行：缺少模型" and called == []
    assert listed["next_run_at"] == at(2026, 11, 2, 8, 0).isoformat()  # it waits for its next slot
    # Saving without a key after revocation is refused: there is nothing to keep.
    with pytest.raises(tasks.TaskError, match="key_required"):
        tasks.save_background_model(OWNER_HASH, provider="deepseek", model="deepseek-chat", base_url=None, api_key=None)


def test_background_key_is_kept_on_same_model_and_required_on_a_new_one(owner):
    _with_key()
    kept = tasks.save_background_model(OWNER_HASH, provider="deepseek", model="deepseek-chat", base_url=None, api_key=None)
    assert kept["has_key"] and tasks.load_background_model(OWNER_HASH)["api_key"] == KEY
    with pytest.raises(tasks.TaskError, match="key_required"):
        tasks.save_background_model(OWNER_HASH, provider="kimi", model="moonshot-v1", base_url=None, api_key=None)
    with pytest.raises(tasks.TaskError, match="invalid_endpoint"):
        tasks.save_background_model(OWNER_HASH, provider="custom", model="m", base_url="http://10.0.0.1/v1", api_key=KEY)


def test_background_model_api_never_returns_or_echoes_the_key(owner, client):
    headers = {"X-Chat-Session": OWNER}
    assert client.get("/agent-chat/background-model", headers={"X-Chat-Session": "g" * 64}).status_code in {401, 403}
    saved = client.put("/agent-chat/background-model", headers=headers,
                       json={"provider": "deepseek", "model": "deepseek-chat", "api_key": KEY})
    assert saved.status_code == 200 and KEY not in saved.text and saved.json()["has_key"] is True
    fetched = client.get("/agent-chat/background-model", headers=headers)
    assert KEY not in fetched.text and KEY[-4:] not in fetched.text
    bad = client.put("/agent-chat/background-model", headers=headers,
                     json={"provider": "deepseek", "model": "bad model!", "api_key": KEY})
    assert bad.status_code == 422 and KEY not in bad.text
    revoked = client.delete("/agent-chat/background-model/key", headers=headers)
    assert revoked.status_code == 200 and revoked.json()["has_key"] is False


def test_task_and_inbox_api_are_owner_only(owner, client):
    guest = {"X-Chat-Session": "g" * 64}
    for method, path in (("get", "/agent-chat/tasks"), ("get", "/agent-chat/inbox"), ("post", "/agent-chat/tasks")):
        response = getattr(client, method)(path, headers=guest, **({"json": _brief()} if method == "post" else {}))
        assert response.status_code in {401, 403}
    headers = {"X-Chat-Session": OWNER}
    created = client.post("/agent-chat/tasks", headers=headers, json=_brief())
    assert created.status_code == 200, created.text
    task_id = created.json()["task"]["id"]
    assert client.post("/agent-chat/tasks", headers=headers, json=_brief(schedule_time="25:00")).status_code == 422
    assert client.patch(f"/agent-chat/tasks/{task_id}", headers=headers, json={"enabled": False}).json()["task"]["enabled"] is False
    listed = client.get("/agent-chat/tasks", headers=headers).json()
    assert listed["status_labels"]["skipped_no_model"] == "未运行：缺少模型" and len(listed["tasks"]) == 1
    tasks.add_item(OWNER_HASH, section="brief", kind="price", title="NVDA", body="b [1]", sources=[{"ordinal": 1}])
    item = client.get("/agent-chat/inbox?section=brief", headers=headers).json()["items"][0]
    assert client.post(f"/agent-chat/inbox/{item['id']}/status", headers=headers, json={"status": "dismissed"}).status_code == 200
    assert client.get("/agent-chat/inbox", headers=headers).json()["items"] == []
    assert client.delete(f"/agent-chat/tasks/{task_id}", headers=headers).json() == {"deleted": True}


# --- caps and retries -----------------------------------------------------------------------------

def _stub_turn(monkeypatch, results):
    calls = []

    async def fake(credential, question, model, *, token_budget, title):
        calls.append({"budget": token_budget, "question": question, "key": model["api_key"]})
        return {"conversation_id": str(uuid.uuid4()), "actions": [], "citations": [], "audit": {}, "answer": "",
                "status": "complete", "reason": None, **results[len(calls) - 1]}
    monkeypatch.setattr(runner, "run_unattended_turn", fake)
    return calls


def test_brief_with_citations_goes_to_the_inbox_with_numbered_sources(owner, monkeypatch):
    _with_key()
    with SessionLocal() as db:
        db.add(AgentResearchQueue(owner_hash=OWNER_HASH, symbols=["NVDA", "AAPL"]))
        db.commit()
    citations = [{"ordinal": 2, "title": "AAPL 10-K", "url": "https://sec.gov/b", "source_type": "official_filing"},
                 {"ordinal": 1, "title": "NVDA 10-Q", "url": "https://sec.gov/a", "source_type": "official_filing"}]
    calls = _stub_turn(monkeypatch, [{"answer": "NVDA 交了 10-Q [1]；AAPL 交了 10-K [2]。", "citations": citations,
                                      "audit": {"token_usage": 1234}}])
    task = tasks.create_task(OWNER_HASH, _brief(), now=at(2026, 10, 30, 7, 0))
    assert run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER)) == {task["id"]: "ok"}
    assert "研究队列：NVDA、AAPL" in calls[0]["question"] and calls[0]["budget"] == tasks.DEFAULT_RUN_TOKENS
    brief = tasks.list_inbox(OWNER_HASH, section="brief")[0]
    assert [s["ordinal"] for s in brief["sources"]] == [1, 2] and "[1]" in brief["body"]
    assert brief["conversation_id"] and brief["kind"] == "task_brief"
    assert tasks.serialize_task(_task(task["id"]), at(2026, 10, 30, 9, 0))["tokens_today"] == 1234


def test_daily_cap_stops_a_run_and_the_run_cap_shrinks_to_what_is_left_today(owner, monkeypatch):
    _with_key()
    calls = _stub_turn(monkeypatch, [{"audit": {"token_usage": 10}}])
    task = tasks.create_task(OWNER_HASH, _brief(run_token_cap=50_000, daily_token_cap=100_000), now=at(2026, 10, 30, 7, 0))
    _set(task["id"], usage_day=date(2026, 10, 30), usage_tokens=90_000)
    run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER))
    assert calls[0]["budget"] == 10_000
    _set(task["id"], usage_day=date(2026, 11, 2), usage_tokens=100_000, next_run_at=at(2026, 11, 2, 8, 0))
    assert run(runner.run_due(now=at(2026, 11, 2, 8, 0), credential=OWNER)) == {task["id"]: "skipped_daily_cap"}
    assert len(calls) == 1
    # A new local day starts from zero.
    _set(task["id"], next_run_at=at(2026, 11, 3, 8, 0))
    calls_before = len(calls)
    _stub_turn(monkeypatch, [{"audit": {"token_usage": 5}}])
    assert run(runner.run_due(now=at(2026, 11, 3, 8, 0), credential=OWNER))[task["id"]] == "no_evidence"
    assert calls_before == 1


def test_a_failed_run_is_retried_once_but_never_after_it_queued_a_write(owner, monkeypatch):
    _with_key()
    calls = _stub_turn(monkeypatch, [{"status": "failed", "reason": "provider_timeout", "audit": {"token_usage": 100}},
                                     {"answer": "x [1]", "citations": [{"ordinal": 1, "title": "t"}]}])
    task = tasks.create_task(OWNER_HASH, _brief(), now=at(2026, 10, 30, 7, 0))
    assert run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER)) == {task["id"]: "ok"}
    assert len(calls) == 2
    action = {"operation": {"name": "forecast", "arguments": {"symbol": "NVDA"}}, "display": {"title": "建议"}, "question": "q"}
    calls = _stub_turn(monkeypatch, [{"status": "failed", "reason": "provider_timeout", "actions": [action]},
                                     {"answer": "never"}])
    _set(task["id"], next_run_at=at(2026, 11, 2, 8, 0))
    assert run(runner.run_due(now=at(2026, 11, 2, 8, 0), credential=OWNER)) == {task["id"]: "failed"}
    assert len(calls) == 1 and len(tasks.list_inbox(OWNER_HASH, section="confirm")) == 1
    # A refused key is not worth a retry either.
    calls = _stub_turn(monkeypatch, [{"status": "failed", "reason": "provider_invalid_key"}, {"answer": "never"}])
    _set(task["id"], next_run_at=at(2026, 11, 3, 8, 0))
    run(runner.run_due(now=at(2026, 11, 3, 8, 0), credential=OWNER))
    assert len(calls) == 1


def test_per_run_cap_stops_gathering_and_a_hard_multiple_stops_model_calls():
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext

    async def quiet(_event):
        pass
    turn = direct.Turn("q", AgentRuntimeContext("custom", "m", "k", token_budget=1000), quiet, [], {})
    assert turn.token_cap() == 1000
    turn.audit["token_usage"] = 1001
    assert turn.budget() == "turn_budget"
    turn.audit["token_usage"] = 1500
    with pytest.raises(ValueError, match="task_token_cap"):
        asyncio.run(turn.model([{"role": "user", "content": "q"}], []))
    assert direct.Turn("q", AgentRuntimeContext("custom", "m", "k"), quiet, [], {}).token_cap() == direct.SOFT_TURN_TOKENS


# --- alerts ---------------------------------------------------------------------------------------

def _alerts_task(**config):
    return tasks.create_task(OWNER_HASH, {"name": "提醒", "kind": "alerts", "schedule_kind": "hourly", "schedule_time": "00:05",
                                          "timezone": "Asia/Shanghai", "scope": "symbols", "symbols": ["NVDA"],
                                          "config": config}, now=at(2026, 10, 30, 7, 0))


def test_new_filing_and_crossed_price_line_become_briefs_with_a_source_once(owner):
    from app.models import MarketPrice, SecFilingInventory
    now = at(2026, 10, 30, 8, 5)
    with SessionLocal() as db:
        db.add(SecFilingInventory(symbol="NVDA", cik="0001045810", accession_number="0001045810-26-000001", form="10-Q",
                                  filed_at=date(2026, 10, 29), primary_document="nvda.htm", source_url="https://www.sec.gov/nvda.htm",
                                  source="sec_edgar", review_status="pending_review", observed_at=now - timedelta(hours=3),
                                  content_status="not_fetched"))
        db.add(SecFilingInventory(symbol="AAPL", cik="0000320193", accession_number="0000320193-26-000001", form="10-K",
                                  filed_at=date(2026, 10, 29), primary_document="a.htm", source_url="https://www.sec.gov/a.htm",
                                  source="sec_edgar", review_status="pending_review", observed_at=now - timedelta(hours=3),
                                  content_status="not_fetched"))
        for day, close in ((date(2026, 10, 28), 148.0), (date(2026, 10, 29), 152.5)):
            db.add(MarketPrice(symbol="NVDA", trading_date=day, open=close, high=close, low=close, close=close, volume=1,
                               source="yahoo", fetched_at=now))
        db.commit()
    task = _alerts_task(news=False, price_lines=[{"symbol": "NVDA", "above": 150}, {"symbol": "NVDA", "below": 140}])
    assert run(runner.run_due(now=now, credential=None)) == {task["id"]: "ok"}
    items = tasks.list_inbox(OWNER_HASH, section="brief")
    kinds = sorted(i["kind"] for i in items)
    assert kinds == ["filing", "price"]  # AAPL is outside the scope; the 140 line was not crossed
    for item in items:
        assert item["sources"][0]["ordinal"] == 1 and "[1]" in item["body"]
    price = next(i for i in items if i["kind"] == "price")
    assert "↑" in price["title"] and "152.50" in price["body"] and "不是买卖建议" in price["body"]
    # The market refresh hook and the next tick see the same filing and close: nothing is added twice.
    runner.run_alert_hooks("prices", now=now)
    run(runner.run_due(now=now + timedelta(hours=1), credential=None))
    assert len(tasks.list_inbox(OWNER_HASH, section="brief")) == 2


def test_news_leads_are_labelled_unverified_and_deduplicated(owner):
    from app.news_discovery import NewsCandidate, SourceAttempt
    now = at(2026, 10, 30, 8, 5)

    class Source:
        name = "fake"

        def discover(self, *, symbol, discovered_at):
            candidate = NewsCandidate(id="n1", symbol=symbol, headline="NVIDIA unveils chip", source_url="https://news.example/a",
                                      outlet="Example", source_name="fake", source_seen_at=discovered_at, published_at=None,
                                      discovered_at=discovered_at, matched_terms=("NVIDIA",), dedup_key="k1")
            return SourceAttempt(source_name="fake", symbol=symbol, status="success", source_url=None, candidates=(candidate,))
    task = _alerts_task(filings=False)
    runner_status = run(runner.run_due(now=now, credential=None, news_sources=[Source()]))
    assert runner_status == {task["id"]: "ok"}
    run(runner.run_due(now=now + timedelta(hours=1), credential=None, news_sources=[Source()]))
    items = tasks.list_inbox(OWNER_HASH, section="brief")
    assert len(items) == 1 and "未核实" in items[0]["body"] and items[0]["sources"][0]["url"] == "https://news.example/a"


def test_alert_hook_never_raises(monkeypatch):
    monkeypatch.setattr(runner, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    runner.run_alert_hooks("filings")


# --- launchd --------------------------------------------------------------------------------------

def test_launchagent_plist_runs_every_five_minutes_and_at_load():
    result = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "install_agent_tasks_launchagent.py"), "--print"],
                            cwd=PROJECT_ROOT, text=True, capture_output=True, check=True)
    payload = plistlib.loads(result.stdout.encode())
    assert payload["Label"] == "com.market-evidence-agent.agent-tasks"
    assert payload["StartInterval"] == 300 and payload["RunAtLoad"] is True
    assert payload["ProgramArguments"][-1] == str(PROJECT_ROOT / "scripts" / "run_agent_tasks.py")
    assert "EnvironmentVariables" not in payload  # no secret in the plist
