"""Telegram bridge: answers reach the owner, a bridge with no chat bound yet can bind one, and a message
Telegram cannot parse as Markdown still goes out."""
import asyncio

import httpx

from scripts import run_telegram_bridge as bridge

TOKEN = "123456789:AAH" + "x" * 32
OWNER_ID = 4242


def _command(chat_id, chat_type, sender_id, text):
    command = text.split()[0]
    return {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": sender_id}, "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(command)}]}


def _record_sends(monkeypatch):
    sent = []

    async def send_text(client, bot_token, chat_id, text, inline_keyboard=None, *, markdown=True):
        sent.append((bot_token, chat_id, text))
        return 1000 + len(sent)

    monkeypatch.setattr(bridge, "_send_text", send_text)
    monkeypatch.setattr(bridge, "_bot_token_for_send", lambda owner, tg: TOKEN)
    return sent


class _Telegram:
    def __init__(self):
        self.binds = []

        self.replies = []

    def confirm_bind(self, owner, code, chat_id, user_id):
        self.binds.append((code, chat_id, user_id))
        return {"bound": True}

    def route(self, owner, text, reply_to):
        if reply_to == 1001:
            return {"bot_id": "b-news", "bot_name": "抓新闻", "text": text, "via": "reply"}
        return {"bot_id": "b-default", "bot_name": "助理", "text": text, "via": "default"}

    def record_reply(self, owner, bot_id, text, message_id):
        self.replies.append((bot_id, message_id))


def test_the_owner_gets_an_answer_back_from_the_bot_the_message_was_for(monkeypatch):
    sent = _record_sends(monkeypatch)
    asked = []

    async def ask_agent(client, credential, bot_id, text):
        asked.append((bot_id, text))
        return "微软 FY2025 营收 2,817.24 亿美元 [1]", [], "c-1"

    monkeypatch.setattr(bridge, "_ask_agent", ask_agent)
    telegram = _Telegram()
    message = {"chat": {"id": OWNER_ID, "type": "private"}, "from": {"id": OWNER_ID}, "text": "去年微软营收多少"}
    asyncio.run(bridge._handle_message(None, "cred", "owner", message, OWNER_ID, OWNER_ID, {}, telegram))
    assert asked == [("b-default", "去年微软营收多少")]
    assert sent == [(TOKEN, OWNER_ID, "【助理】\n微软 FY2025 营收 2,817.24 亿美元 [1]")]
    assert telegram.replies == [("b-default", 1001)]
    # Replying to that message goes on with the bot that sent it (here the fake routes 1001 to the news bot).
    reply = {**message, "text": "细说", "reply_to_message": {"message_id": 1001}}
    asyncio.run(bridge._handle_message(None, "cred", "owner", reply, OWNER_ID, OWNER_ID, {}, telegram))
    assert asked[-1] == ("b-news", "细说") and sent[-1][2].startswith("【抓新闻】")


def test_an_unbound_bridge_binds_from_a_private_chat(monkeypatch):
    sent = _record_sends(monkeypatch)
    telegram = _Telegram()
    message = _command(OWNER_ID, "private", OWNER_ID, "/bind abcdef01")
    asyncio.run(bridge._handle_message(None, "cred", "owner", message, None, None, {}, telegram))
    assert telegram.binds == [("abcdef01", OWNER_ID, OWNER_ID)]
    assert sent[-1][2].startswith("绑定成功")


def test_an_unbound_bridge_ignores_everything_else(monkeypatch):
    sent = _record_sends(monkeypatch)
    telegram = _Telegram()
    question = {"chat": {"id": 7, "type": "private"}, "from": {"id": 7}, "text": "去年微软营收多少"}
    in_a_group = _command(-100, "group", 7, "/bind abcdef01")
    for message in (question, in_a_group):
        asyncio.run(bridge._handle_message(None, "cred", "owner", message, None, None, {}, telegram))
    assert telegram.binds == []
    assert [text for _, _, text in sent] == ["请在和 bot 的私聊里绑定。"]


def _client(statuses):
    class Client:
        def __init__(self):
            self.bodies = []

        async def post(self, url, json):
            self.bodies.append(dict(json))
            status = statuses[len(self.bodies) - 1]
            return httpx.Response(status, json={"ok": status == 200,
                                                "description": "Bad Request: can't parse entities"})

    return Client()


def test_markdown_telegram_cannot_parse_goes_out_as_plain_text():
    client = _client([400, 200])
    asyncio.run(bridge._send_text(client, TOKEN, OWNER_ID, "*营收* 同比 -14.93%，来源 \\[1\\]，FY2025_Q3"))
    first, second = client.bodies
    assert first["parse_mode"] == "Markdown" and "parse_mode" not in second
    assert second["text"] == "*营收* 同比 -14.93%，来源 [1]，FY2025_Q3"


def test_a_message_telegram_accepts_goes_out_once():
    client = _client([200])
    asyncio.run(bridge._send_text(client, TOKEN, OWNER_ID, "营收 2,817.24 亿美元"))
    assert len(client.bodies) == 1
