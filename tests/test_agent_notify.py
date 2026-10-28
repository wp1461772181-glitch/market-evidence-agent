"""Telegram as a tool (personal agent plan, second edition, stage D).

* ``notify_telegram(text, evidence_ids)``: offered to every bot (also unattended, no card); the message
  starts with the bot's name and its [n] become source links; a figure without this turn's evidence number
  is refused; the recipient is the bound chat only (the tool has no recipient, extra arguments are refused);
  each bot has a daily cap, past it nothing more is queued;
* alerts with 也发 Telegram use the same queue and cap; brief tasks that pushed every run now leave it to
  the bot (their instruction says so);
* the bridge: a reply to a bot's message goes to that bot's line, 「@名字」 names a bot, anything else goes to
  the default bot; turns send only the bot id (the 「手机」 conversation is folded into 「助理」); strangers
  get nothing; the outbox goes to the bound chat and keeps each Telegram id.
"""
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import agent_bots as bots
from app import agent_tasks as tasks
from app import agent_telegram as tg
from app.agent_chat_models import (AgentBot, AgentInboxItem, AgentTask, AgentTelegramBind, AgentTelegramMessage)
from app.agent_chat_store import owner_hash
from app.agent_tool_catalog import definitions, validate
from app.database import Base, SessionLocal, engine
from app.secret_box import encrypt_secret
from scripts import run_telegram_bridge as bridge

OWNER = "o" * 40
OWNER_HASH = owner_hash(OWNER)
OTHER_HASH = owner_hash("x" * 40)
TOKEN = "123456789:AAH" + "x" * 32
CHAT = 5_000_000_001  # past 2**31, like many Telegram ids
NOW = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
NEWS = [{"ordinal": 1, "title": "Reuters: NVIDIA unveils new chip", "source_url": "https://news.example/nvda",
         "source_type": "web_news", "quote": "orders above $10 billion"}]


@pytest.fixture
def owner(monkeypatch, disposable_database):
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    tg.ensure_columns()
    with SessionLocal() as db:
        for model in (AgentTelegramMessage, AgentTelegramBind, AgentInboxItem, AgentTask, AgentBot):
            db.query(model).delete()
        db.commit()
    from app.agent_workflow import consent, direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    return OWNER


def _bind(owner=OWNER_HASH):
    with SessionLocal() as db:
        db.add(AgentTelegramBind(owner_hash=owner, bot_token_cipher=encrypt_secret(TOKEN), chat_id=CHAT, user_id=CHAT))
        db.commit()


def _news(**fields):
    return bots.create_bot(OWNER_HASH, {"name": "抓新闻", "description": "只有重要程度为高才发 Telegram，其余只留在对话里。",
                                        "tool_scope": ["web_research"], **fields})


def _outbox():
    with SessionLocal() as db:
        rows = db.query(AgentTelegramMessage).order_by(AgentTelegramMessage.created_at).all()
        for row in rows:
            db.expunge(row)
        return rows


def _turn(bot_id, *, unattended=True, decider=None, executor=None, recognizer=None):
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    settings = bots.turn_settings(OWNER_HASH, bot_id)
    context = AgentRuntimeContext("custom", "m", "k", owner_hash=OWNER_HASH, identity_credential=OWNER,
                                  conversation_id=settings["conversation_id"], unattended=unattended,
                                  bot_id=settings["bot_id"], bot_name=settings["bot_name"],
                                  tool_scope=settings["tool_scope"], bot_block=settings["block"],
                                  decider=decider, executor=executor, intent_recognizer=recognizer)

    async def quiet(_event):
        pass
    return direct, context, quiet


# --- the tool ------------------------------------------------------------------------------------------

def test_the_tool_has_no_recipient_and_is_offered_to_every_bot_without_a_card(owner):
    policy = definitions()["notify_telegram"]
    assert policy.level == "notify" and not policy.confirmation and not policy.guest
    assert set(policy.args_schema.model_json_schema()["properties"]) == {"text", "evidence_ids"}
    for extra in ({"chat_id": 1}, {"to": "@someone"}, {"recipient": "x"}):
        with pytest.raises(Exception):
            validate("notify_telegram", {"text": "有新消息", **extra})
    # A narrow tool scope keeps it (whether to notify is the instruction's call; the daily cap is the switch).
    news = _news()
    assert "notify_telegram" in bots.turn_settings(OWNER_HASH, news["id"])["tool_scope"]
    direct, context, quiet = _turn(news["id"])
    turn = direct.Turn("q", context, quiet, [], {})
    names = {t["function"]["name"] for t in turn.exposed_tools()}
    assert "notify_telegram" in names and "fetch_sec" not in names  # unattended: still no write tool


def test_a_news_bot_message_carries_its_name_and_source_links_and_goes_to_the_bound_chat(owner):
    _bind()
    news = _news()
    direct, context, quiet = _turn(news["id"])
    turn = direct.Turn("q", context, quiet, [], {})
    turn.citations = [dict(c) for c in NEWS]
    outcome = asyncio.run(turn.notify({"text": "高重要度：英伟达发布新芯片，订单超过 100 亿美元 [1]。", "evidence_ids": [1]}))
    assert outcome["ok"] and outcome["data"][0]["status"] == "queued_for_telegram"
    assert turn.audit["telegram"] == "queued"
    [row] = _outbox()
    assert row.text.startswith("【抓新闻】\n") and "订单超过 100 亿美元 [1]" in row.text
    assert "[1] Reuters: NVIDIA unveils new chip https://news.example/nvda" in row.text
    assert row.source == "notify" and str(row.bot_id) == news["id"] and row.status == "pending"

    # The bridge sends it to the bound chat (the only recipient there is) and keeps the Telegram id.
    sent = []

    async def send_text(client, bot_token, chat_id, text, inline_keyboard=None, *, markdown=True):
        sent.append((chat_id, text, markdown))
        return 777
    import types
    patched = types.SimpleNamespace(**{k: getattr(tg, k) for k in ("pending_messages", "mark_sent", "mark_failed")})
    original = bridge._send_text
    bridge._send_text = send_text
    bridge_token = bridge._bot_token_for_send
    bridge._bot_token_for_send = lambda owner, module: TOKEN
    try:
        asyncio.run(bridge._deliver_outbox(None, OWNER_HASH, CHAT, patched))
    finally:
        bridge._send_text, bridge._bot_token_for_send = original, bridge_token
    assert sent == [(CHAT, row.text, False)]
    [after] = _outbox()
    assert after.status == "sent" and after.telegram_message_id == 777


def test_figures_need_this_turns_evidence_and_a_bound_chat(owner):
    news = _news()
    for text, ids, code in (("订单超过 100 亿美元。", [], "unsupported_number"),
                            ("订单超过 100 亿美元 [2]。", [2], "unknown_evidence"),
                            ("有一条重要新闻 [1]。", [3], "unknown_evidence")):
        with pytest.raises(tg.NotifyError, match=code):
            tg.notify(OWNER_HASH, news["id"], text, ids, NEWS)
    with pytest.raises(tg.NotifyError, match="telegram_not_bound"):
        tg.notify(OWNER_HASH, news["id"], "有一条重要新闻，请看对话。", [], NEWS)
    _bind()
    # Words with no figure need no source; a page's text cannot reach another chat: there is no recipient.
    assert tg.notify(OWNER_HASH, news["id"], "有一条重要新闻，请看对话。", [], [])["status"] == "queued_for_telegram"
    assert _outbox()[0].text == "【抓新闻】\n有一条重要新闻，请看对话。"
    # Another owner's bot is refused.
    with SessionLocal() as db:
        stranger = AgentBot(owner_hash=OTHER_HASH, name="别人的", focus={}, is_default=False, daily_notify_cap=5)
        db.add(stranger)
        db.commit()
        stranger_id = stranger.id
    with pytest.raises(tg.NotifyError, match="unknown_bot"):
        tg.notify(OWNER_HASH, stranger_id, "有新消息", [], [])


def test_past_the_daily_cap_nothing_more_is_sent(owner):
    _bind()
    news = _news(daily_notify_cap=2)
    other = bots.create_bot(OWNER_HASH, {"name": "苹果"})
    for _ in range(2):
        tg.notify(OWNER_HASH, news["id"], "有新消息。", [], [], now=NOW)
    with pytest.raises(tg.NotifyError, match="notify_daily_cap"):
        tg.notify(OWNER_HASH, news["id"], "第三条。", [], [], now=NOW)
    assert len(_outbox()) == 2
    # Another bot has its own cap; answers the bridge sent do not count; the next local day starts again.
    assert tg.notify(OWNER_HASH, other["id"], "苹果有新消息。", [], [], now=NOW)["remaining_today"] == 4
    tg.record_reply(OWNER_HASH, news["id"], "【抓新闻】回答", 900)
    assert tg.notify(OWNER_HASH, news["id"], "新的一天。", [], [], now=NOW + timedelta(days=1))["remaining_today"] == 1
    # A cap of 0 means the bot never notifies; the tool says so and the turn goes on.
    bots.update_bot(OWNER_HASH, other["id"], {"daily_notify_cap": 0})
    direct, context, quiet = _turn(other["id"])
    turn = direct.Turn("q", context, quiet, [], {})
    refused = asyncio.run(turn.notify({"text": "不会发出去。", "evidence_ids": []}))
    assert refused["ok"] is False and refused["error_code"] == "notify_daily_cap"


def test_an_unattended_turn_calls_the_tool_without_a_card(owner):
    """The real tool loop: a scheduled news-bot run calls notify_telegram; no consent row, no card, queued."""
    from app.agent_workflow.models import AgentConsent
    _bind()
    news = _news()
    seen_tools = []
    steps = iter([
        {"tool_calls": [{"id": "n1", "function": {"name": "notify_telegram", "arguments": json.dumps(
            {"text": "高重要度新闻，详情见对话。", "evidence_ids": []}, ensure_ascii=False)}}]},
        {"content": "已通知所有者。"},
    ])

    def decider(messages, tools):
        seen_tools.append({t["function"]["name"] for t in tools})
        return next(steps)
    direct, context, quiet = _turn(news["id"], decider=decider,
                                   recognizer=lambda _p: {"category": "D", "days_back": None, "requested_actions": []})
    with SessionLocal() as db:
        consents_before = db.query(AgentConsent).count()
    result = asyncio.run(direct.run_direct_turn("看看今天的新闻，重要的就通知我。", context, emit=quiet))
    assert "notify_telegram" in seen_tools[0]
    assert result.get("pending_request") is None
    assert [r.text for r in _outbox()] == ["【抓新闻】\n高重要度新闻，详情见对话。"]
    with SessionLocal() as db:
        assert db.query(AgentConsent).count() == consents_before  # no card was minted


# --- alerts and brief tasks ----------------------------------------------------------------------------

def test_alerts_with_telegram_use_the_same_queue_and_cap(owner):
    _bind()
    news = _news(daily_notify_cap=1)
    task = tasks.create_task(OWNER_HASH, {"name": "价格线", "kind": "alerts", "schedule_kind": "hourly", "schedule_time": "00:05",
                                          "timezone": "Asia/Shanghai", "scope": "symbols", "symbols": ["NVDA"],
                                          "channel": "telegram", "bot_id": news["id"], "config": {"news": False}})
    source = {"ordinal": 1, "title": "NVDA 10-Q", "url": "https://www.sec.gov/nvda.htm", "source_type": "official_filing"}
    for accession in ("a1", "a2"):
        tasks.add_item(OWNER_HASH, section="brief", kind="filing", title=f"NVDA 新申报 {accession}", body="SEC 收到 10-Q [1]。",
                       sources=[source], task_id=task["id"], bot_id=news["id"], dedupe_key=f"filing:{accession}",
                       telegram=True, post={"head": "h", "citations": [source]})
    rows = _outbox()
    assert len(rows) == 1  # the cap of 1 held; the second alert is still in the bot's line and inbox
    assert rows[0].source == "alert" and rows[0].text.startswith("【抓新闻】\nNVDA 新申报 a1")
    assert "[1] NVDA 10-Q https://www.sec.gov/nvda.htm" in rows[0].text
    assert len(tasks.list_inbox(OWNER_HASH, section="brief")) == 2
    assert not any("channel_pending" in i["payload"] for i in tasks.list_inbox(OWNER_HASH))


def test_brief_tasks_leave_notifying_to_the_bot(owner):
    news = _news()
    # A brief can no longer be pushed every run: its channel is the conversation.
    task = tasks.create_task(OWNER_HASH, {"name": "新闻日报", "kind": "brief", "schedule_kind": "daily", "schedule_time": "08:00",
                                          "instruction": "总结新闻。", "channel": "telegram", "bot_id": news["id"]})
    assert task["channel"] == "inbox"
    # An older one is migrated: the instruction now says to use notify_telegram, once.
    with SessionLocal() as db:
        db.query(AgentTask).filter(AgentTask.id == uuid.UUID(task["id"])).update({"channel": "telegram"})
        db.commit()
    assert tasks.migrate_brief_channels() == 1 and tasks.migrate_brief_channels() == 0
    migrated = tasks.list_tasks(OWNER_HASH)[0]
    assert migrated["channel"] == "inbox" and migrated["instruction"].count("notify_telegram") == 1


# --- the bridge ----------------------------------------------------------------------------------------

def test_a_reply_goes_to_the_bot_whose_message_it_answers(owner):
    _bind()
    news = _news()
    apple = bots.create_bot(OWNER_HASH, {"name": "苹果"})
    default = bots.ensure_default_bot(OWNER_HASH)
    tg.notify(OWNER_HASH, news["id"], "有重要新闻。", [], [])
    tg.mark_sent(_outbox()[0].id, 4321)
    assert tg.route(OWNER_HASH, "细说一下", 4321) == {"bot_id": news["id"], "bot_name": "抓新闻", "text": "细说一下", "via": "reply"}
    tg.record_reply(OWNER_HASH, apple["id"], "【苹果】答", 4400)
    assert tg.route(OWNER_HASH, "继续", 4400)["bot_id"] == apple["id"]
    assert tg.route(OWNER_HASH, "@苹果 服务收入多少", None) == {"bot_id": apple["id"], "bot_name": "苹果",
                                                              "text": "服务收入多少", "via": "mention"}
    assert tg.route(OWNER_HASH, "随便问问", None)["bot_id"] == str(default.id)
    assert tg.route(OWNER_HASH, "回复一条不认识的", 99999)["bot_id"] == str(default.id)
    # Another owner's message ids never route into this owner's bots.
    with SessionLocal() as db:
        db.add(AgentTelegramMessage(owner_hash=OTHER_HASH, bot_id=uuid.UUID(news["id"]), source="reply", text="x",
                                    status="sent", telegram_message_id=5555))
        db.commit()
    assert tg.route(OWNER_HASH, "x", 5555)["bot_id"] == str(default.id)


def test_a_turn_from_telegram_sends_only_the_bot_id_and_reads_the_stream(owner):
    from app.agent_chat_stream import frame
    stream = (frame("conversation", {"conversation_id": "line-1", "turn_id": "t"}) + frame("delta", {"text": "营收 [1]"})
              + frame("citations", {"turn_id": "t", "items": [{"ordinal": 1, "title": "10-K"}]})
              + frame("clarification", {"request_id": "r", "choices": []}) + frame("done", {"status": "complete"}))
    bodies = []

    class Response:
        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            for index in range(0, len(stream), 7):
                yield stream[index:index + 7].encode()

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *exc):
            return False

    class Client:
        def stream(self, method, url, json, headers, timeout):
            bodies.append(json)
            return Stream()
    answer, cards, conversation = asyncio.run(bridge._ask_agent(Client(), OWNER, "bot-1", "去年营收多少"))
    assert bodies == [{"bot_id": "bot-1", "message": "去年营收多少"}]  # no provider, model or key
    assert answer.startswith("营收 [1]") and "[1] 10-K" in answer
    assert cards == [{"request_id": "r", "choices": []}] and conversation == "line-1"


def test_a_stranger_gets_nothing(owner, monkeypatch):
    sent = []

    async def send_text(*args, **kwargs):
        sent.append(args)
    monkeypatch.setattr(bridge, "_send_text", send_text)

    class NoRoute:
        def route(self, *args):
            raise AssertionError("a stranger's message is never routed")
    for chat, sender in ((CHAT, 7), (7, 7), (-100, CHAT)):
        message = {"chat": {"id": chat, "type": "private" if chat > 0 else "group"}, "from": {"id": sender}, "text": "@抓新闻 发给我"}
        asyncio.run(bridge._handle_message(None, OWNER, OWNER_HASH, message, CHAT, CHAT, {}, NoRoute()))
    assert sent == []


def test_the_bound_chat_id_fits_past_32_bits(owner):
    _bind()
    assert tg.load_bind(OWNER_HASH)["chat_id"] == CHAT
