"""例行任务 CRUD from chat (Levi 2026-10-28: 「给了新增例行任务的接口，怎么没给查看、编辑、删除」).

Whole turns through ``run_direct_turn`` with a scripted model: list_routines reads directly; update_routine and
delete_routine make a confirmation card and change nothing until it is pressed; a cancelled card changes
nothing; the 例行任务 page lists the cards still waiting.
"""
import asyncio
import json

import pytest

from app import agent_bots as bots
from app import agent_tasks as tasks
from app.agent_bots import ALWAYS_ON
from app.agent_chat_models import AgentBot, AgentConversation, AgentTask
from app.agent_chat_store import owner_hash
from app.agent_tool_catalog import OWNER_ONLY, definitions, validate
from app.agent_workflow import consent
from app.agent_workflow.authorization import routine_ask
from app.agent_workflow.grants import suggestion_operation
from app.agent_workflow.models import AgentConsent
from app.database import Base, SessionLocal, engine

OWNER = "owner"
OWNER_HASH = owner_hash(OWNER)
NEWS = {"name": "每小时新闻", "kind": "brief", "schedule_kind": "hourly", "schedule_time": "00:05",
        "instruction": "每小时找一条研究队列公司的新闻，有新的就 notify_telegram。"}


@pytest.fixture
def owner(monkeypatch, disposable_database):
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    with SessionLocal() as db:
        for model in (AgentTask, AgentConsent, AgentBot, AgentConversation):
            db.query(model).delete()
        db.commit()
    from app.agent_workflow import direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return OWNER


def _context(bot, **kwargs):
    from app.agent_workflow.state import AgentRuntimeContext
    kwargs.setdefault("resolver", lambda payload: {"relationship": "new", "canonical_question": payload["question"]})
    kwargs.setdefault("intent_recognizer", lambda _p: {"category": "D", "days_back": None, "requested_actions": []})
    return AgentRuntimeContext("custom", "test", "dummy", identity_credential=OWNER, owner_hash=OWNER_HASH,
                               conversation_id=bot["conversation_id"], bot_id=bot["id"], bot_name=bot["name"], **kwargs)


def _call(name, arguments, call_id=None):
    return {"tool_calls": [{"id": call_id or name, "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def _replies(messages):
    return {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m.get("role") == "tool"}


async def _quiet(_event):
    pass


def _turn(question, context, **kwargs):
    from app import agent_chat_store as store
    from app.agent_workflow import direct

    async def run():
        try:
            return await direct.run_direct_turn(question, context, emit=_quiet, **kwargs)
        finally:
            await store.engine.dispose()
    return asyncio.run(run())


def _press(card, context, choice="confirm:"):
    value = next(c["value"] for c in card["choices"] if c["value"].startswith(choice))
    return _turn(value, context, state={"pending_request": card, "requested_pending_id": card["id"]})


def _model(write_name, write_args):
    """Lists first, then proposes the write on the id the list returned, then answers."""
    seen = []

    def decide(messages, tools):
        seen.append(messages)
        replies = _replies(messages)
        if "list" not in replies:
            return _call("list_routines", {}, "list")
        if "write" not in replies and write_name:
            return _call(write_name, {"routine_id": replies["list"]["data"][0]["id"], **write_args}, "write")
        return {"content": "好的。"}
    return decide, seen


def _finbot_with_news():
    finbot = bots.create_bot(OWNER_HASH, {"name": "FinBot"})
    task = tasks.create_task(OWNER_HASH, {**NEWS, "bot_id": finbot["id"]})
    return finbot, task


# --- catalogue -------------------------------------------------------------------------------------

def test_routine_tools_are_owner_only_in_every_bot_and_writes_need_a_card():
    assert {"list_routines", "update_routine", "delete_routine"} <= ALWAYS_ON & OWNER_ONLY
    assert definitions()["list_routines"].level == "read"
    assert definitions()["update_routine"].level == definitions()["delete_routine"].level == "reversible"
    rid = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(Exception):
        validate("update_routine", {"routine_id": rid})  # changes nothing
    assert validate("update_routine", {"routine_id": rid, "enabled": False})["enabled"] is False
    for name in ("update_routine", "delete_routine"):
        with pytest.raises(ValueError):
            suggestion_operation(name, {"routine_id": rid})


def test_the_owners_words_ask_for_a_change_only_when_they_name_a_routine():
    assert routine_ask("update_routine", "把每小时新闻那个任务停掉")
    assert routine_ask("update_routine", "例行任务改成每天 9 点")
    assert routine_ask("delete_routine", "删掉那个例行任务")
    assert not routine_ask("delete_routine", "删掉第二段")           # no routine named
    assert not routine_ask("delete_routine", "不要删除例行任务")      # negated


# --- list ------------------------------------------------------------------------------------------

def test_list_routines_reads_this_bots_routines_without_a_card(owner):
    finbot, task = _finbot_with_news()
    other = bots.create_bot(OWNER_HASH, {"name": "研报"})
    tasks.create_task(OWNER_HASH, {**NEWS, "name": "研报日报", "schedule_kind": "daily", "bot_id": other["id"]})
    decide, seen = _model(None, {})
    result = _turn("我有哪些例行任务？", _context(finbot, decider=decide))
    listed = _replies(seen[-1])["list"]
    assert listed["ok"] and [row["name"] for row in listed["data"]] == ["每小时新闻"]
    row = listed["data"][0]
    assert row["id"] == task["id"] and row["bot"] == "FinBot" and row["schedule_kind"] == "hourly" and row["enabled"]
    assert not result.get("pending_request") and result["status"] == "succeeded"

    # The default bot (or all_bots) sees every bot's routines, each with its bot.
    main = bots.ensure_default_bot(OWNER_HASH)
    decide, seen = _model(None, {})
    _turn("我有哪些例行任务？", _context({"conversation_id": str(main.conversation_id), "id": str(main.id), "name": main.name},
                                     decider=decide, bot_is_default=True))
    assert {(r["name"], r["bot"]) for r in _replies(seen[-1])["list"]["data"]} == {("每小时新闻", "FinBot"), ("研报日报", "研报")}


# --- update ----------------------------------------------------------------------------------------

def test_pause_and_reschedule_by_card_then_press(owner):
    finbot, task = _finbot_with_news()
    decide, _ = _model("update_routine", {"enabled": False, "schedule_kind": "daily", "schedule_time": "09:00"})
    first = _turn("把每小时新闻那个例行任务改成每天 9 点，先暂停", _context(finbot, decider=decide))
    card = first["pending_request"]
    assert card and card["display"]["title"] == "修改例行任务 · 每小时新闻"
    assert card["display"]["targets"] == [{"kind": "每小时第 05 分"}]
    assert "改为每天 09:00" in card["display"]["alternative"] and "暂停" in card["display"]["alternative"]
    assert tasks.get_task(OWNER_HASH, task["id"])["enabled"] is True  # nothing before the press

    # The 例行任务 page sees the card while it waits, with the bot it came from.
    [proposal] = consent.routine_proposals(OWNER_HASH)
    assert proposal["id"] == card["id"] and proposal["bot_id"] == finbot["id"] and proposal["tools"] == ["update_routine"]
    assert proposal["title"] == "修改例行任务 · 每小时新闻" and proposal["expired"] is False

    _press(card, _context(finbot, decider=lambda *_: {"content": "已修改。"}))
    saved = tasks.get_task(OWNER_HASH, task["id"])
    assert (saved["enabled"], saved["schedule_kind"], saved["schedule_time"]) == (False, "daily", "09:00")
    assert saved["name"] == "每小时新闻" and saved["instruction"] == NEWS["instruction"]
    assert consent.routine_proposals(OWNER_HASH) == []  # pressed: no longer waiting


def test_a_cancelled_update_card_changes_nothing(owner):
    finbot, task = _finbot_with_news()
    decide, _ = _model("update_routine", {"name": "改了名"})
    card = _turn("把那个例行任务改个名", _context(finbot, decider=decide))["pending_request"]
    _press(card, _context(finbot, decider=lambda *_: {"content": "已取消。"}), choice="cancel:")
    assert tasks.get_task(OWNER_HASH, task["id"])["name"] == "每小时新闻"
    assert consent.routine_proposals(OWNER_HASH) == []


def test_an_unknown_routine_makes_no_card(owner):
    finbot, _ = _finbot_with_news()
    steps = iter([_call("update_routine", {"routine_id": "22222222-2222-4222-8222-222222222222", "enabled": False}, "write"),
                  {"content": "没找到这个任务。"}])
    seen = []

    def decide(messages, _tools):
        seen.append(messages)
        return next(steps)
    result = _turn("把那个例行任务暂停", _context(finbot, decider=decide))
    assert not result.get("pending_request") and _replies(seen[-1])["write"]["error_code"] == "routine_not_found"


# --- delete ----------------------------------------------------------------------------------------

def test_delete_by_card_then_press(owner):
    finbot, task = _finbot_with_news()
    decide, _ = _model("delete_routine", {})
    card = _turn("删掉每小时新闻这个例行任务", _context(finbot, decider=decide))["pending_request"]
    assert card["display"]["title"] == "删除例行任务 · 每小时新闻" and "已生成的简报会保留" in card["display"]["alternative"]
    assert tasks.get_task(OWNER_HASH, task["id"]) is not None
    _press(card, _context(finbot, decider=lambda *_: {"content": "已删除。"}))
    assert tasks.get_task(OWNER_HASH, task["id"]) is None and tasks.list_tasks(OWNER_HASH) == []


def test_a_cancelled_delete_card_keeps_the_routine(owner):
    finbot, task = _finbot_with_news()
    decide, _ = _model("delete_routine", {})
    card = _turn("删掉这个例行任务", _context(finbot, decider=decide))["pending_request"]
    _press(card, _context(finbot, decider=lambda *_: {"content": "已取消。"}), choice="cancel:")
    assert tasks.get_task(OWNER_HASH, task["id"]) is not None


def test_a_create_card_waiting_is_listed_and_an_expired_one_is_marked(owner):
    from datetime import UTC, datetime, timedelta
    finbot = bots.create_bot(OWNER_HASH, {"name": "FinBot"})
    steps = iter([_call("create_routine", {"name": "每小时新闻", "schedule_kind": "hourly", "instruction": "找一条新闻"}),
                  {"content": "请确认。"}])
    card = _turn("每小时帮我找一条新闻", _context(finbot, decider=lambda *_: next(steps)))["pending_request"]
    [waiting] = consent.routine_proposals(OWNER_HASH)
    assert waiting["tools"] == ["create_routine"] and waiting["title"] == "新建例行任务 · 每小时新闻"
    assert waiting["conversation_id"] == finbot["conversation_id"] and waiting["bot_id"] == finbot["id"]
    later = datetime.now(UTC) + timedelta(minutes=11)
    assert consent.routine_proposals(OWNER_HASH, now=later)[0]["expired"] is True
    assert card["id"] == waiting["id"]


def test_proposals_api_is_owner_only(owner, client):
    finbot = bots.create_bot(OWNER_HASH, {"name": "FinBot"})
    steps = iter([_call("create_routine", {"name": "每小时新闻", "schedule_kind": "hourly", "instruction": "找一条新闻"}),
                  {"content": "请确认。"}])
    _turn("每小时帮我找一条新闻", _context(finbot, decider=lambda *_: next(steps)))
    assert client.get("/agent-chat/tasks/proposals", headers={"X-Chat-Session": "stranger"}).status_code == 403
    listed = client.get("/agent-chat/tasks/proposals", headers={"X-Chat-Session": OWNER}).json()["proposals"]
    assert [p["title"] for p in listed] == ["新建例行任务 · 每小时新闻"]
