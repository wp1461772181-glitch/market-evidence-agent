"""Telegram: 同意 on a consent card lands the write in 资料库, and answers go out as plain chat text.

Regression (2026-10-28): the owner tapped 同意 on a Telegram card to save a material and nothing reached
资料库 or the vector index.  The bridge called ``consent.execute`` itself with an empty operation list (the
payload digest never matched, so ConsentError confirmation_parameter_mismatch) and with the async,
three-argument ``execute_reversible`` as the handler; no confirmed write could ever run from Telegram.  A
press now goes to the bot's line as the button's own value, like the web page, and the server runs it.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.agent_workflow import consent, direct, tools
from app.agent_workflow.models import AgentConsent
from app.agent_workflow.state import AgentRuntimeContext
from app.database import Base, SessionLocal, engine
from app.knowledge.models import KnowledgeUploadOwner
from app.models import UploadedEvidence
from scripts import run_telegram_bridge as bridge

OWNER_ID = 4242
OWNER_HASH = "o" * 64


@pytest.fixture
def owner(monkeypatch, disposable_database):
    Base.metadata.create_all(engine, tables=[AgentConsent.__table__, UploadedEvidence.__table__,
                                             KnowledgeUploadOwner.__table__])
    monkeypatch.setattr(consent, "owner_credential_matches", lambda credential: credential == "owner")
    monkeypatch.setattr(tools, "owner_credential_matches", lambda credential: credential == "owner")
    return "owner"


def _material():
    return {"name": "save_material_evidence", "arguments": {
        "symbol": "TSLA", "title": "特斯拉三季度交付（测试）", "source_url": "https://ir.tesla.com/press/" + uuid.uuid4().hex,
        "published_at": "2026-10-02", "credibility_stars": 4, "credibility_reason": "公司公告。",
        "impact_severity": "medium", "content": "# 交付\n\n" + uuid.uuid4().hex}}


class _Telegram:
    def __init__(self):
        self.replies = []

    def record_reply(self, owner, bot_id, text, message_id):
        self.replies.append((bot_id, text))


def _server_line(conversation, card, asked):
    """The agent-chat stream for a bot's line, reduced to what a card press does there: the server finds the
    card waiting in that conversation and runs it with the operations it saved."""
    async def ask_agent(client, credential, bot_id, text, *, conversation_id=None, pending_request_id=None,
                        display_message=None):
        asked.append({"bot_id": bot_id, "text": text, "conversation_id": conversation_id,
                      "pending_request_id": pending_request_id, "display_message": display_message})
        runtime = AgentRuntimeContext("custom", "test", "dummy", identity_credential=credential,
                                      owner_hash=OWNER_HASH, conversation_id=conversation_id)

        async def emit(_):
            pass

        state = {"pending_request": card, "requested_pending_id": pending_request_id}
        result = await direct.Turn(text, runtime, emit, [], state).consent_reply()
        return result["answer"], [], conversation_id
    return ask_agent


def test_agree_on_telegram_saves_the_material_into_the_library(owner, monkeypatch):
    conversation = "tg-line-" + uuid.uuid4().hex[:8]
    operation = _material()
    card = consent.propose(owner, conversation, [operation], {"title": "保存材料",
                           "targets": [{"symbol": "TSLA", "title": operation["arguments"]["title"]}]},
                           question="把这条公告存进资料库")
    sent, asked = [], []

    async def send_text(client, bot_token, chat_id, text, inline_keyboard=None, *, markdown=True):
        sent.append((chat_id, text, inline_keyboard))
        return 2000 + len(sent)

    async def answer_callback(*_):
        pass

    monkeypatch.setattr(bridge, "_send_text", send_text)
    monkeypatch.setattr(bridge, "_answer_callback", answer_callback)
    monkeypatch.setattr(bridge, "_bot_token_for_send", lambda owner_hash, tg: "123:x")
    monkeypatch.setattr(bridge, "_ask_agent", _server_line(conversation, card, asked))

    pending = {}
    keyboard = bridge._make_keyboard([card], pending, conversation, owner, "b-1", "助理")
    labels = [button["text"] for button in keyboard[0]]
    assert labels == ["同意", "取消"]
    agree = keyboard[0][0]["callback_data"]
    # Telegram only ever sees the short id, never the capability.
    token = card["choices"][0]["value"].split(":", 1)[1]
    assert token not in str(keyboard) and len(agree.encode()) <= 64

    press = {"id": "cb-1", "from": {"id": OWNER_ID}, "data": agree,
             "message": {"chat": {"id": OWNER_ID, "type": "private"}}}
    telegram = _Telegram()
    asyncio.run(bridge._handle_callback(None, press, pending, OWNER_HASH, owner, OWNER_ID, OWNER_ID, telegram))

    assert asked == [{"bot_id": "b-1", "text": "confirm:" + token, "conversation_id": conversation,
                      "pending_request_id": card["id"], "display_message": "同意"}]
    with SessionLocal() as db:
        saved = db.scalar(select(UploadedEvidence).where(UploadedEvidence.source_url == operation["arguments"]["source_url"]))
        assert saved is not None and saved.symbol == "TSLA"
        # The owner mapping is what lets the source sync import it and the indexer embed it.
        mapping = db.scalar(select(KnowledgeUploadOwner).where(KnowledgeUploadOwner.upload_id == saved.id))
        assert mapping is not None and mapping.owner_hash == OWNER_HASH
        assert db.scalar(select(AgentConsent).where(AgentConsent.id == uuid.UUID(card["id"]))).status == "complete"
    assert sent[-1][1].startswith("【助理】") and "特斯拉三季度交付" in sent[-1][1]
    assert telegram.replies and telegram.replies[-1][0] == "b-1"

    # Both buttons of the card are used up; a second press runs nothing and adds no chat message.
    before = len(sent)
    asyncio.run(bridge._handle_callback(None, press, pending, OWNER_HASH, owner, OWNER_ID, OWNER_ID, telegram))
    assert len(asked) == 1 and len(sent) == before
    assert pending == {}


def test_cancel_on_telegram_writes_nothing(owner, monkeypatch):
    conversation = "tg-line-" + uuid.uuid4().hex[:8]
    operation = _material()
    card = consent.propose(owner, conversation, [operation], {"title": "保存材料"}, question="存一下")
    asked, sent = [], []

    async def send_text(client, bot_token, chat_id, text, inline_keyboard=None, *, markdown=True):
        sent.append(text)
        return 1

    async def answer_callback(*_):
        pass

    monkeypatch.setattr(bridge, "_send_text", send_text)
    monkeypatch.setattr(bridge, "_answer_callback", answer_callback)
    monkeypatch.setattr(bridge, "_bot_token_for_send", lambda owner_hash, tg: "123:x")
    monkeypatch.setattr(bridge, "_ask_agent", _server_line(conversation, card, asked))
    pending = {}
    keyboard = bridge._make_keyboard([card], pending, conversation, owner, "b-1", "助理")
    press = {"id": "cb-1", "from": {"id": OWNER_ID}, "data": keyboard[0][1]["callback_data"],
             "message": {"chat": {"id": OWNER_ID, "type": "private"}}}
    asyncio.run(bridge._handle_callback(None, press, pending, OWNER_HASH, owner, OWNER_ID, OWNER_ID, _Telegram()))

    assert asked[0]["text"] == "cancel:" + card["id"]
    assert "已取消" in sent[-1]
    with SessionLocal() as db:
        assert db.scalar(select(UploadedEvidence).where(
            UploadedEvidence.source_url == operation["arguments"]["source_url"])) is None


def test_a_card_that_needs_ticking_gets_no_buttons_and_says_where_to_finish():
    card = {"id": "req-2", "reason": "choose_revision_sources", "options": [{"value": "1", "label": "材料"}],
            "choices": [{"label": "用所选材料修订", "value": "confirm:" + "t" * 40}]}
    assert bridge._make_keyboard([card], {}, "c-1", "cred", "b-1", "助理") is None


# --- plain chat text ---------------------------------------------------------------------------------------

def test_a_streamed_answer_goes_out_without_markers_or_a_sources_list():
    events = [("conversation", {"conversation_id": "c-1"}),
              ("delta", {"text": "特斯拉上一交易日收盘 [1]，公司公告见 https://ir.tesla.com/press [2]。"}),
              ("citations", {"items": [{"ordinal": 1, "title": "Yahoo Finance TSLA"},
                                       {"ordinal": 2, "title": "Tesla IR", "source_url": "https://ir.tesla.com"}]})]
    answer, cards, cid = bridge.format_turn(events)
    assert answer == "特斯拉上一交易日收盘，公司公告见 https://ir.tesla.com/press。"
    assert cid == "c-1" and cards == []
    assert "Yahoo Finance" not in answer and "——" not in answer and "[1]" not in answer


@pytest.mark.parametrize("appendix", [
    "\n\n来源：\n[1] Yahoo Finance https://finance.yahoo.com/quote/TSLA",
    "\n\n**参考资料**\n- [1] Yahoo Finance",
    "\n\n——\n  [1] Yahoo Finance",
    "\n\nSources:\n[1] Yahoo Finance",
    "\n来源：Yahoo Finance、Tesla IR",
])
def test_a_sources_block_the_model_wrote_itself_is_cut(appendix):
    from app.agent_telegram import chat_text
    body = "TSLA 的行情见 [雅虎财经](https://finance.yahoo.com/quote/TSLA) [1]。"
    assert chat_text(body + appendix) == "TSLA 的行情见 [雅虎财经](https://finance.yahoo.com/quote/TSLA)。"


def test_an_ordinary_line_that_starts_with_a_heading_word_is_kept():
    from app.agent_telegram import chat_text
    assert chat_text("结论如上。\n参考一下上季度的交付量。") == "结论如上。\n参考一下上季度的交付量。"


def test_a_notification_lists_no_sources():
    from app.agent_telegram import render
    citations = [{"ordinal": 1, "title": "Tesla IR", "source_url": "https://ir.tesla.com"}]
    assert render("抓新闻", "特斯拉发布交付数据 [1]。", citations, [1]) == "【抓新闻】\n特斯拉发布交付数据。"


# --- a card as it arrives on the wire ----------------------------------------------------------------------

def test_a_streamed_save_card_becomes_telegram_buttons_not_a_go_to_the_web_note(owner, monkeypatch):
    """Regression (2026-10-28): 「那就把第三天存进资料库吧」 on Telegram got only 「请核对确认卡…」 and
    「（这一步需要在网页里选择后确认。）」.  The stream's clarification frame names the card ``request_id``
    (agent_chat_stream), and the bridge only looked for ``id``, so no streamed card ever got buttons; the
    unit tests passed the consent record (which has ``id``) straight to ``_make_keyboard``."""
    conversation = "tg-line-" + uuid.uuid4().hex[:8]
    operation = _material()
    card = consent.propose(owner, conversation, [operation], {"title": "保存材料"}, question="把第三天存进资料库")
    # Exactly the keys agent_chat_stream puts on the clarification frame.
    wire = {"request_id": card["id"], "reason": card["reason"], "choices": card["choices"],
            "display": card.get("display"), "question": card.get("question")}
    events = [("conversation", {"conversation_id": conversation, "turn_id": "t-1"}),
              ("delta", {"text": "请核对确认卡中的全部操作、来源和日期，确认后执行。"}),
              ("clarification", wire)]
    sent, asked = [], []

    async def stream(client, credential, bot_id, text, **press):
        return bridge.format_turn(events)

    async def send_text(client, bot_token, chat_id, text, inline_keyboard=None, *, markdown=True):
        sent.append((text, inline_keyboard))
        return 3000 + len(sent)

    monkeypatch.setattr(bridge, "_ask_agent", stream)
    monkeypatch.setattr(bridge, "_send_text", send_text)
    monkeypatch.setattr(bridge, "_bot_token_for_send", lambda owner_hash, tg: "123:x")
    pending = {}
    asyncio.run(bridge._bot_turn(None, owner, OWNER_HASH, OWNER_ID, pending, _Telegram(), "b-1", "助理",
                                 "那就把第三天存进资料库吧"))

    text, keyboard = sent[-1]
    assert "网页" not in text
    assert keyboard and [b["text"] for b in keyboard[0]] == ["同意", "取消"]
    entry = pending[keyboard[0][0]["callback_data"].split(":", 1)[1]]
    assert entry["card_id"] == card["id"] and entry["conversation_id"] == conversation

    # Pressing 同意 on that keyboard still lands the write through the server's confirmed handler.
    monkeypatch.setattr(bridge, "_ask_agent", _server_line(conversation, card, asked))

    async def answer_callback(*_):
        pass

    monkeypatch.setattr(bridge, "_answer_callback", answer_callback)
    press = {"id": "cb-1", "from": {"id": OWNER_ID}, "data": keyboard[0][0]["callback_data"],
             "message": {"chat": {"id": OWNER_ID, "type": "private"}}}
    asyncio.run(bridge._handle_callback(None, press, pending, OWNER_HASH, owner, OWNER_ID, OWNER_ID, _Telegram()))
    assert asked[0]["pending_request_id"] == card["id"] and asked[0]["text"].startswith("confirm:")
    with SessionLocal() as db:
        assert db.scalar(select(UploadedEvidence).where(
            UploadedEvidence.source_url == operation["arguments"]["source_url"])) is not None
