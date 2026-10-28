"""create_routine (2026-10-28): 「让 FinBot 每小时找一条新闻」 got 「没有对应的工具」.

Scheduled tasks could only be made on the 例行任务 page; now a bot proposes one on a confirmation card and the
owner's press creates it, attached to that bot.
"""
import asyncio
import json

import pytest

from app import agent_tasks as tasks
from app.agent_bots import ALWAYS_ON, ensure_default_bot
from app.agent_chat_models import AgentTask
from app.agent_chat_store import owner_hash
from app.agent_tool_catalog import OWNER_ONLY, definitions, schemas, validate, write_tool_names
from app.agent_workflow import consent
from app.agent_workflow.authorization import authorized
from app.agent_workflow.grants import suggestion_operation
from app.agent_workflow.models import AgentConsent
from app.database import Base, SessionLocal, engine

OWNER = "owner"
OWNER_HASH = owner_hash(OWNER)
WORDS = "每小时帮我找一条新闻"
ROUTINE = {"name": "每小时新闻", "instruction": "找一条关于研究队列公司的最新新闻，有新的就用 notify_telegram 发给我。",
           "schedule_kind": "hourly", "schedule_time": "00:05"}


@pytest.fixture
def owner(monkeypatch, disposable_database):
    # The tables the API creates at startup (as in test_agent_tasks), so the default bot's backfill works.
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    with SessionLocal() as db:
        db.query(AgentTask).delete()
        db.commit()
    from app.agent_workflow import direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return OWNER


def _context(conversation, **kwargs):
    from app.agent_workflow.state import AgentRuntimeContext
    # 「一条」 reads as a reference to the resolver: a first turn has none.
    return AgentRuntimeContext("custom", "test", "dummy", identity_credential=OWNER, owner_hash=OWNER_HASH,
                               conversation_id=conversation, resolver=_new, **kwargs)


def _new(payload):
    return {"relationship": "new", "canonical_question": payload["question"]}


def _route(*actions):
    return lambda _payload: {"category": "D", "days_back": None, "requested_actions": list(actions)}


def _call(name, arguments):
    return {"tool_calls": [{"id": "c1", "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


async def _quiet(_event):
    pass


def _turn(question, context, **kwargs):
    from app.agent_workflow import direct
    return asyncio.run(direct.run_direct_turn(question, context, emit=_quiet, **kwargs))


def _offered(tools):
    return {(t.get("function") or {}).get("name") for t in tools or () if isinstance(t, dict)}


def _proposing():
    """A model that calls create_routine once when the turn offers it (the resolver also asks this model)."""
    called = []

    def decide(_messages, tools):
        if "create_routine" in _offered(tools) and not called:
            called.append(1)
            return _call("create_routine", {**ROUTINE})
        return {"content": "好的。"}
    return decide


def _press(card, context):
    return _turn(card["choices"][0]["value"], context, state={"pending_request": card, "requested_pending_id": card["id"]})


def test_schema_is_an_owner_only_reversible_write_in_every_bot():
    policy = definitions()["create_routine"]
    assert policy.level == "reversible" and "create_routine" in write_tool_names()
    assert "create_routine" in OWNER_ONLY and "create_routine" in ALWAYS_ON
    assert validate("create_routine", {**ROUTINE})["symbols"] == []
    with pytest.raises(Exception):
        validate("create_routine", {**ROUTINE, "schedule_kind": "minutely"})
    with pytest.raises(Exception):
        validate("create_routine", {**ROUTINE, "schedule_time": "25:00"})
    # Offered only to the owner, and only once granted (like every write).
    names = lambda **k: {s["function"]["name"] for s in schemas(**k)}
    assert "create_routine" not in names(owner=False, granted={"create_routine"})
    assert "create_routine" not in names(owner=True)
    assert "create_routine" in names(owner=True, granted={"create_routine"})
    # An agent suggestion is not the owner's ask.
    with pytest.raises(ValueError):
        suggestion_operation("create_routine", {**ROUTINE})


def test_the_owners_words_grant_it_and_a_plain_question_does_not():
    assert authorized("create_routine", WORDS)
    assert authorized("create_routine", "每天早上 9 点给我总结一下")
    assert not authorized("create_routine", "AAPL 最近有什么新闻")
    assert not authorized("create_routine", "不要定时推送")


def test_hourly_news_card_then_press_creates_the_task_on_this_bot(owner):
    bot = ensure_default_bot(OWNER_HASH)
    # The classifier missed the ask: the owner's own words still grant the tool (the card is the guard).
    context = _context("conv-finbot", intent_recognizer=_route(), decider=_proposing(), bot_id=str(bot.id))
    first = _turn(WORDS, context)
    card = first["pending_request"]
    assert card and card["display"]["title"] == "新建例行任务 · 每小时新闻", first["answer"]
    assert card["display"]["targets"] == [{"kind": "每小时第 05 分"}]
    assert tasks.list_tasks(OWNER_HASH) == []  # nothing is written before the press

    _press(card, _context("conv-finbot", decider=lambda *_: {"content": "已创建。"}, bot_id=str(bot.id)))
    [task] = tasks.list_tasks(OWNER_HASH)
    assert (task["name"], task["kind"], task["schedule_kind"], task["schedule_time"]) == ("每小时新闻", "brief", "hourly", "00:05")
    assert task["instruction"] == ROUTINE["instruction"] and task["scope"] == "research_queue"
    assert task["bot_id"] == str(bot.id)


def test_a_guest_never_gets_a_card(owner):
    from app.agent_workflow.state import AgentRuntimeContext
    context = AgentRuntimeContext("custom", "test", "dummy", identity_credential="guest", owner_hash=owner_hash("guest"),
                                  conversation_id="conv-guest", resolver=_new, intent_recognizer=_route("create_routine"),
                                  decider=_proposing())
    result = _turn(WORDS, context)
    assert not result.get("pending_request")
    assert tasks.list_tasks(owner_hash("guest")) == []
