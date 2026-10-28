"""A bot's own Telegram bot (UX batch 2, P4).

* a bot may save its own BotFather token: checked with getMe, encrypted, never returned (only @username and the
  last four characters); one token on one bot; the default bot keeps the shared Telegram (settings page);
* binding: a 30-minute code sent as /start <code> to that Telegram bot; an expired code is refused (the shared
  bind code now expires too);
* sending: a bot with its own bound Telegram bot queues there without 「【名字】」; any other bot goes through the
  shared bot with it; a reply on the shared bot never matches a message of another Telegram bot;
* receiving: the owner's message to that Telegram bot goes to that bot, no 「@名字」; strangers get nothing;
* deleting the bot deletes its Telegram row.  No limit on how many bots have one.
"""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app import agent_bots as bots
from app import agent_telegram as tg
from app import agent_telegram_bots as own
from app.agent_chat_models import AgentBot, AgentInboxItem, AgentTask, AgentTelegramBind, AgentTelegramBot, AgentTelegramMessage
from app.agent_chat_store import owner_hash
from app.agent_telegram import TelegramError
from app.database import Base, SessionLocal, engine
from app.secret_box import encrypt_secret
from scripts import run_telegram_bridge as bridge

OWNER = "m" * 40
OWNER_HASH = owner_hash(OWNER)
SHARED_TOKEN = "111111111:AAH" + "s" * 32
NEWS_TOKEN = "222222222:AAH" + "n" * 32
SHARED_CHAT = 5_000_000_001
OWN_CHAT = 5_000_000_001  # the owner's private chat id is the same user id on every Telegram bot
ME = lambda token: {"ok": True, "id": int(token.split(":")[0]), "username": "news_finbot"}


@pytest.fixture
def owner(monkeypatch, disposable_database):
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    tg.ensure_columns()
    with SessionLocal() as db:
        for model in (AgentTelegramMessage, AgentTelegramBot, AgentTelegramBind, AgentInboxItem, AgentTask, AgentBot):
            db.query(model).delete()
        db.commit()
    with SessionLocal() as db:
        db.add(AgentTelegramBind(owner_hash=OWNER_HASH, bot_token_cipher=encrypt_secret(SHARED_TOKEN),
                                 chat_id=SHARED_CHAT, user_id=SHARED_CHAT))
        db.commit()
    return OWNER


def _bot(name):
    return bots.create_bot(OWNER_HASH, {"name": name})


def _bound_news():
    news = _bot("新闻")
    own.save_token(OWNER_HASH, news["id"], NEWS_TOKEN, me=ME)
    code = own.start_bind(OWNER_HASH, news["id"])["code"]
    row_id = own.channels(OWNER_HASH)[0]["row_id"]
    own.confirm_bind(row_id, code, OWN_CHAT, OWN_CHAT)
    return news, row_id


def test_a_token_is_saved_encrypted_and_never_returned(owner):
    news = _bot("新闻")
    view = own.save_token(OWNER_HASH, news["id"], NEWS_TOKEN, me=ME)
    assert view["bot_username"] == "news_finbot" and view["token_tail"] == NEWS_TOKEN[-4:]
    assert NEWS_TOKEN not in str(view) and NEWS_TOKEN not in str(own.status(OWNER_HASH, news["id"]))
    with SessionLocal() as db:
        row = db.query(AgentTelegramBot).one()
        assert NEWS_TOKEN not in row.token_cipher and row.status == "unbound"
    with pytest.raises(TelegramError, match="invalid_bot_token"):
        own.save_token(OWNER_HASH, news["id"], "333333333:" + "z" * 35, me=lambda t: {"ok": False})


def test_one_token_belongs_to_one_bot_and_the_default_bot_keeps_the_shared_one(owner):
    news, other = _bot("新闻"), _bot("财报")
    own.save_token(OWNER_HASH, news["id"], NEWS_TOKEN, me=ME)
    with pytest.raises(TelegramError, match="token_in_use"):
        own.save_token(OWNER_HASH, other["id"], NEWS_TOKEN, me=ME)
    with pytest.raises(TelegramError, match="token_in_use"):
        own.save_token(OWNER_HASH, other["id"], SHARED_TOKEN, me=ME)
    default = bots.ensure_default_bot(OWNER_HASH)
    assert own.status(OWNER_HASH, default.id)["shared"] is True
    with pytest.raises(TelegramError, match="default_bot_uses_shared"):
        own.save_token(OWNER_HASH, default.id, "444444444:" + "d" * 35, me=ME)


def test_there_is_no_limit_on_how_many_bots_have_their_own(owner):
    for index in range(12):
        bot = _bot(f"bot{index}")
        own.save_token(OWNER_HASH, bot["id"], f"5{index:08d}:AAH" + "q" * 32, me=ME)
    assert len(own.channels(OWNER_HASH)) == 12


def test_a_bind_code_expires_after_thirty_minutes(owner):
    news = _bot("新闻")
    own.save_token(OWNER_HASH, news["id"], NEWS_TOKEN, me=ME)
    started = own.start_bind(OWNER_HASH, news["id"])
    assert started["link"] == f"https://t.me/news_finbot?start={started['code']}"
    row_id = own.channels(OWNER_HASH)[0]["row_id"]
    with pytest.raises(TelegramError, match="bind_code_expired"):
        own.confirm_bind(row_id, started["code"], OWN_CHAT, OWN_CHAT, now=datetime.now(UTC) + timedelta(minutes=31))
    assert own.confirm_bind(row_id, started["code"], OWN_CHAT, OWN_CHAT)["bound"] is True


def test_the_shared_bind_code_now_expires_too(owner):
    with SessionLocal() as db:
        db.query(AgentTelegramBind).delete()
        db.add(AgentTelegramBind(owner_hash=OWNER_HASH, bot_token_cipher=encrypt_secret(SHARED_TOKEN)))
        db.commit()
    code = tg.start_bind(OWNER_HASH)["code"]
    with SessionLocal() as db:
        db.get(AgentTelegramBind, OWNER_HASH).bind_code_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    with pytest.raises(TelegramError, match="bind_code_expired"):
        tg.confirm_bind(OWNER_HASH, code, SHARED_CHAT, SHARED_CHAT)


def test_a_bot_with_its_own_telegram_sends_there_without_its_name_others_use_the_shared_one(owner):
    news, row_id = _bound_news()
    plain = _bot("财报")
    with SessionLocal() as db:
        tg.enqueue(db, OWNER_HASH, news["id"], "【新闻】\n英伟达发布新芯片。", source="notify")
        tg.enqueue(db, OWNER_HASH, plain["id"], "【财报】\n苹果财报发布。", source="notify")
        db.commit()
    shared = tg.pending_messages(OWNER_HASH)
    mine = tg.pending_messages(OWNER_HASH, 20, row_id)
    assert [m["text"] for m in shared] == ["【财报】\n苹果财报发布。"]
    assert [m["text"] for m in mine] == ["英伟达发布新芯片。"]


def test_a_reply_on_the_shared_bot_ignores_another_telegram_bots_message_ids(owner):
    news, row_id = _bound_news()
    tg.record_reply(OWNER_HASH, news["id"], "答", 4242, row_id)
    routed = tg.route(OWNER_HASH, "继续", 4242)
    assert routed["via"] == "default"


def test_the_owners_message_to_a_bots_own_telegram_goes_to_that_bot_and_strangers_get_nothing(owner, monkeypatch):
    news, row_id = _bound_news()
    channel = next(c for c in own.channels(OWNER_HASH) if c["row_id"] == row_id)
    asked, sent = [], []

    async def ask(client, credential, bot_id, text, **_):
        asked.append((bot_id, text))
        return "收到。", [], "conv-1"

    async def send_text(client, token, chat_id, text, inline_keyboard=None, *, markdown=True):
        sent.append((token, chat_id, text))
        return 9001

    monkeypatch.setattr(bridge, "_ask_agent", ask)
    monkeypatch.setattr(bridge, "_send_text", send_text)
    message = {"chat": {"id": OWN_CHAT, "type": "private"}, "from": {"id": OWN_CHAT}, "text": "今天有什么新闻"}
    asyncio.run(bridge._handle_own_message(None, OWNER, OWNER_HASH, message, channel, {}, tg))
    assert asked == [(news["id"], "今天有什么新闻")]
    assert sent == [(NEWS_TOKEN, OWN_CHAT, "收到。")]  # its own chat: no 【新闻】 in front
    stranger = {"chat": {"id": 7, "type": "private"}, "from": {"id": 7}, "text": "hi"}
    asyncio.run(bridge._handle_own_message(None, OWNER, OWNER_HASH, stranger, channel, {}, tg))
    assert len(asked) == 1 and len(sent) == 1


def test_start_with_a_code_binds_the_bots_own_telegram(owner, monkeypatch):
    news = _bot("新闻")
    own.save_token(OWNER_HASH, news["id"], NEWS_TOKEN, me=ME)
    code = own.start_bind(OWNER_HASH, news["id"])["code"]
    channel = own.channels(OWNER_HASH)[0]
    sent = []

    async def send_text(client, token, chat_id, text, inline_keyboard=None, *, markdown=True):
        sent.append(text)
        return 1

    monkeypatch.setattr(bridge, "_send_text", send_text)
    message = {"chat": {"id": OWN_CHAT, "type": "private"}, "from": {"id": OWN_CHAT}, "text": f"/start {code}"}
    asyncio.run(bridge._handle_own_message(None, OWNER, OWNER_HASH, message, channel, {}, tg))
    assert "绑定成功" in sent[-1]
    assert own.status(OWNER_HASH, news["id"])["bound"] is True


def test_deleting_the_bot_deletes_its_telegram_row(owner):
    news, _ = _bound_news()
    bots.delete_bot(OWNER_HASH, news["id"])
    with SessionLocal() as db:
        assert db.query(AgentTelegramBot).count() == 0


def test_the_api_never_returns_the_token(owner, client):
    news = _bot("新闻")
    import app.agent_telegram_bots as module
    original = module.fetch_me
    module.fetch_me = ME
    try:
        headers = {"X-Chat-Session": OWNER}
        import app.agent_chat_identity as identity
        identity_ok = identity.owner_credential_matches
        identity.owner_credential_matches = lambda c: c == OWNER
        from app import agent_chat_stream
        stream_ok = getattr(agent_chat_stream, "owner_credential_matches", None)
        if stream_ok:
            agent_chat_stream.owner_credential_matches = lambda c: c == OWNER
        try:
            saved = client.post(f"/agent-chat/bots/{news['id']}/telegram/token", headers=headers, json={"bot_token": NEWS_TOKEN})
            assert saved.status_code == 200 and NEWS_TOKEN not in saved.text
            status = client.get(f"/agent-chat/bots/{news['id']}/telegram", headers=headers)
            assert status.status_code == 200 and NEWS_TOKEN not in status.text and status.json()["bot_username"] == "news_finbot"
        finally:
            identity.owner_credential_matches = identity_ok
            if stream_ok:
                agent_chat_stream.owner_credential_matches = stream_ok
    finally:
        module.fetch_me = original
