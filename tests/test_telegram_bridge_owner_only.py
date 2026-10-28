"""Telegram bridge: only the bound account can use it, button ids cannot be guessed, and the bot token
never reaches the logs."""
import asyncio

import httpx

from app.agent_workflow import consent
from scripts import run_telegram_bridge as bridge

TOKEN = "123456789:AAH" + "x" * 32
OWNER_ID = 4242


def _card():
    return {"abc": {"value": "confirm:t-1", "card_id": "req-1", "conversation_id": "c-1", "credential": "cred",
                    "bot_id": "b-1", "bot_name": "助理", "label": "同意", "group": ["abc"]}}


def _press(chat_id, chat_type, sender_id):
    return {"id": "cb-1", "from": {"id": sender_id}, "data": "ok:abc",
            "message": {"chat": {"id": chat_id, "type": chat_type}}}


class _Telegram:
    def record_reply(self, owner, bot_id, text, message_id):
        pass


def _fake_io(monkeypatch):
    """``executed`` records each press that reached a bot's line (the server runs the card from there)."""
    sent, executed = [], []

    async def send_text(client, bot_token, chat_id, text, inline_keyboard=None):
        sent.append((chat_id, text))

    async def answer_callback(client, bot_token, callback_id):
        pass

    async def ask_agent(client, credential, bot_id, text, **press):
        executed.append(text.removeprefix("confirm:"))
        return "已保存到资料库。", [], press.get("conversation_id")

    def execute(*_):
        raise AssertionError("the bridge never runs consent.execute itself")

    monkeypatch.setattr(bridge, "_send_text", send_text)
    monkeypatch.setattr(bridge, "_answer_callback", answer_callback)
    monkeypatch.setattr(bridge, "_bot_token_for_send", lambda owner, tg: TOKEN)
    monkeypatch.setattr(bridge, "_ask_agent", ask_agent)
    monkeypatch.setattr(consent, "execute", execute)
    return sent, executed


def _handle_press(press, pending, bound_chat, bound_user):
    asyncio.run(bridge._handle_callback(None, press, pending, "owner", "cred", bound_chat, bound_user, _Telegram()))


def test_a_press_from_another_chat_runs_nothing(monkeypatch):
    sent, executed = _fake_io(monkeypatch)
    pending = _card()
    _handle_press(_press(999, "private", 999), pending, OWNER_ID, OWNER_ID)
    assert executed == [] and sent == [] and "abc" in pending


def test_a_press_from_another_member_of_the_bound_chat_runs_nothing(monkeypatch):
    sent, executed = _fake_io(monkeypatch)
    pending = _card()
    _handle_press(_press(-100, "group", 7), pending, -100, OWNER_ID)
    assert executed == [] and sent == [] and "abc" in pending


def test_a_group_bound_without_a_user_id_refuses_presses(monkeypatch):
    sent, executed = _fake_io(monkeypatch)
    pending = _card()
    _handle_press(_press(-100, "group", OWNER_ID), pending, -100, None)
    assert executed == [] and "abc" in pending


def test_the_owner_runs_a_card_once(monkeypatch):
    sent, executed = _fake_io(monkeypatch)
    pending = _card()
    # A private chat bound before user ids were recorded: the chat id is the owner's id.
    _handle_press(_press(OWNER_ID, "private", OWNER_ID), pending, OWNER_ID, None)
    _handle_press(_press(OWNER_ID, "private", OWNER_ID), pending, OWNER_ID, None)
    assert executed == ["t-1"]
    assert sent[0] == (OWNER_ID, "【助理】\n已保存到资料库。")
    assert sent[1][1].startswith("这张确认卡已经过期")


def test_a_message_from_another_member_of_the_bound_chat_is_ignored(monkeypatch):
    _fake_io(monkeypatch)
    message = {"chat": {"id": -100, "type": "group"}, "from": {"id": 7}, "text": "把研究队列清空"}
    asked = []

    async def ask_agent(*args, **kwargs):
        asked.append(args)
        return "", [], "c-1"

    monkeypatch.setattr(bridge, "_ask_agent", ask_agent)
    asyncio.run(bridge._handle_message(None, "cred", "owner", message, -100, OWNER_ID, {}, None))
    assert asked == []


def test_binding_records_the_sender_and_only_happens_in_a_private_chat(monkeypatch):
    sent, _ = _fake_io(monkeypatch)
    binds = []

    class Telegram:
        def confirm_bind(self, owner, code, chat_id, user_id):
            binds.append((chat_id, user_id))
            return {"bound": True}

    asyncio.run(bridge._handle_bind_command(None, {"id": -100, "type": "group"}, {"id": OWNER_ID},
                                            "owner", "code-1", Telegram()))
    assert binds == [] and "私聊" in sent[-1][1]
    asyncio.run(bridge._handle_bind_command(None, {"id": OWNER_ID, "type": "private"}, {"id": OWNER_ID},
                                            "owner", "code-1", Telegram()))
    assert binds == [(OWNER_ID, OWNER_ID)]


def test_button_ids_cannot_be_guessed():
    ids = {bridge._make_short_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(short_id) >= 12 and len(f"ok:{short_id}".encode()) <= 64 for short_id in ids)


def test_the_bot_token_never_reaches_the_logs(capsys):
    class RefusingClient:
        async def get(self, url, params=None):
            request = httpx.Request("GET", url)
            response = httpx.Response(409, request=request)
            raise httpx.HTTPStatusError(f"Client error '409 Conflict' for url '{url}'",
                                        request=request, response=response)

    assert asyncio.run(bridge._get_updates(RefusingClient(), TOKEN, 0, 1)) is None
    logged = capsys.readouterr().err
    assert "409" in logged and TOKEN not in logged and "***" in logged
