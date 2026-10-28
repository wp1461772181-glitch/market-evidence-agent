"""Telegram bridge for the personal agent (plan §2.4).

Long-polls the Telegram Bot API with getUpdates (no webhook).  Only handles messages and button presses
from the bound account in the bound chat; strangers are ignored.  Until a chat is bound it answers
only /bind <code> (or /start <code>) in a private chat.  Owner text goes to a bot's line through the agent
chat API (second edition, stage D): a reply to a bot's message goes to that bot, a message starting with
「@名字」 to the bot of that name, anything else to the default bot 「助理」 (whose line the old 「手机」
conversation was folded into).  Each answer starts with the bot's name and is remembered by its Telegram
message id, so replying to it continues with that bot.  Consent cards become inline keyboard buttons with
a short id (the real token stays server-side); callback_query runs consent.execute once and sends a receipt.
Messages the bots queued (notify_telegram, alerts with 也发 Telegram) are sent from the outbox to the
bound chat only.

    python scripts/run_telegram_bridge.py            # long-poll forever
    python scripts/run_telegram_bridge.py --dry-run  # validate config and exit

The bridge reads the bot token and binding from the database (encrypted), the owner credential from
data/personal-chat-login.json.  It calls the local agent-chat stream endpoint via HTTP with a bot id only,
so the model (and its key) comes from the server's catalog for that bot; the API server must be running.
Fails closed when the token is missing; with no chat bound yet it waits for /bind.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TELEGRAM_API = "https://api.telegram.org"
LOCAL_API = os.environ.get("TELEGRAM_BRIDGE_API_URL", "http://127.0.0.1:8000")
LONG_POLL_TIMEOUT = 60
OUTBOX_CHECK_INTERVAL = 30
MESSAGE_MAX_CHARS = 4000
ANSWER_MAX_CHARS = 3800
# httpx puts the request URL, and with it /bot<token>/, into its error text.
BOT_TOKEN_PATTERN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")

os.chdir(PROJECT_ROOT)


def _redact(value) -> str:
    return BOT_TOKEN_PATTERN.sub("***", str(value))


def _owner_credential() -> str:
    """Read the configured owner credential, like the task runner."""
    from app.agent_chat_identity import _load_config
    config = _load_config()
    if not config:
        raise SystemExit("no owner credential configured (data/personal-chat-login.json)")
    return config[3]


async def main(dry_run: bool = False) -> int:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        import httpx
    except ImportError:
        print("httpx is required.  Run: pip install httpx", file=sys.stderr)
        return 1

    credential = _owner_credential()
    from app.agent_chat_store import engine as store_engine, owner_hash
    from app import agent_telegram as tg
    owner = owner_hash(credential)

    bot_token = await asyncio.to_thread(tg.load_bot_token, owner)
    if not bot_token:
        print("Telegram bridge: no bot token saved.  Save one in the settings page first.")
        return 0 if dry_run else 1

    # No chat bound yet is fine: the bridge has to be running to receive the /bind that binds one.
    bind = await asyncio.to_thread(tg.load_bind, owner) or {}
    bound_chat = bind.get("chat_id")
    bound_user = bind.get("user_id")
    del bind  # the decrypted token stays in bot_token only

    if dry_run:
        print("Bot token: ****" + bot_token[-4:])
        print(f"Bound chat: {bound_chat or '(none yet; send /bind <code> to the bot in a private chat)'}")
        print("Model: each bot's model from the server catalog")
        print(f"Local API: {LOCAL_API}")
        return 0

    pending_cards: dict[str, dict] = {}
    last_outbox_check = 0.0
    offset = 0

    print(f"Telegram bridge started.  Chat: {bound_chat or 'none yet, waiting for /bind'}, API: {LOCAL_API}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(LONG_POLL_TIMEOUT + 15)) as client:
        while True:
            try:
                # Re-read the token and binding every round, so a first /bind, an unbind or a new token saved
                # in the settings page takes effect without restarting the bridge.
                latest_token = await asyncio.to_thread(tg.load_bot_token, owner)
                if not latest_token:
                    print("Telegram bridge: the bot token is gone or no longer decrypts.  Stopping.",
                          file=sys.stderr)
                    return 1
                if latest_token != bot_token:
                    bot_token, offset = latest_token, 0  # update ids belong to one bot
                bind = await asyncio.to_thread(tg.load_bind, owner) or {}
                bound_chat, bound_user = bind.get("chat_id"), bind.get("user_id")
                del bind

                now = time.time()
                if bound_chat is not None and now - last_outbox_check >= OUTBOX_CHECK_INTERVAL:
                    await _deliver_outbox(client, owner, bound_chat, tg)
                    last_outbox_check = now

                updates = await _get_updates(client, bot_token, offset, LONG_POLL_TIMEOUT)
                if updates is None:
                    await asyncio.sleep(5)
                    continue
                for update in updates:
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    message = update.get("message")
                    callback = update.get("callback_query")

                    if isinstance(message, dict):
                        await _handle_message(client, credential, owner, message,
                                              bound_chat, bound_user, pending_cards, tg)
                    elif isinstance(callback, dict):
                        await _handle_callback(client, callback, pending_cards, owner, credential,
                                               bound_chat, bound_user)
            except Exception as exc:
                print(f"Bridge error: {_redact(exc)}", file=sys.stderr)
                await asyncio.sleep(10)


async def _get_updates(client, bot_token: str, offset: int, timeout: int) -> list | None:
    import httpx
    try:
        response = await client.get(
            f"{TELEGRAM_API}/bot{bot_token}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            print(f"Telegram API error: {_redact(data)}", file=sys.stderr)
            return None
        return data.get("result") or []
    except httpx.TimeoutException:
        return []
    except Exception as exc:
        print(f"Telegram API error: {_redact(exc)}", file=sys.stderr)
        return None


def _from_owner(chat: dict, sender: dict | None, bound_chat: int, bound_user: int | None) -> bool:
    """Only the bound account in the bound chat.  A bind saved before the user id was recorded falls back
    to the chat id, which is the user id in a private chat; a group with no user id is refused."""
    if not isinstance(chat, dict) or chat.get("id") != bound_chat:
        return False
    owner_id = bound_user if bound_user is not None else (bound_chat if chat.get("type") == "private" else None)
    return owner_id is not None and isinstance(sender, dict) and sender.get("id") == owner_id


async def _handle_message(client, credential: str, owner: str, message: dict,
                          bound_chat: int | None, bound_user: int | None, pending_cards: dict, tg):
    chat = message.get("chat", {})
    text = str(message.get("text") or message.get("caption") or "").strip()
    entities = message.get("entities") or []
    bot_cmd = None
    for entity in entities:
        if entity.get("type") == "bot_command" and isinstance(entity.get("offset"), int):
            cmd_text = text[entity["offset"]:entity["offset"] + entity["length"]]
            if cmd_text.startswith("/"):
                bot_cmd = cmd_text
                break
    binding = bool(bot_cmd) and bot_cmd.startswith(("/start", "/bind"))
    if bound_chat is None:
        # Nothing is bound yet: the one thing a chat can do is bind with the code from the settings page.
        if binding:
            await _handle_bind_command(client, chat, message.get("from"), owner, text[len(bot_cmd):].strip(), tg)
        return
    if not _from_owner(chat, message.get("from"), bound_chat, bound_user):
        return
    if bot_cmd:
        if binding:
            await _handle_bind_command(client, chat, message.get("from"), owner, text[len(bot_cmd):].strip(), tg)
        return

    if not text:
        return
    bot_token = _bot_token_for_send(owner, tg)
    reply_to = (message.get("reply_to_message") or {}).get("message_id")
    target = await asyncio.to_thread(tg.route, owner, text, reply_to if isinstance(reply_to, int) else None)
    if not target["text"]:
        return
    answer, cards, cid = await _ask_agent(client, credential, target["bot_id"], target["text"])
    keyboard = _make_keyboard(cards, pending_cards, cid, credential) if cid else None
    answer = f"【{target['bot_name']}】\n" + (answer.strip() or ("完成。" if keyboard else "（这条回复没有内容。）"))
    chunks = [answer[i:i+ANSWER_MAX_CHARS] for i in range(0, len(answer), ANSWER_MAX_CHARS)]
    for i, chunk in enumerate(chunks):
        kb = keyboard if i == len(chunks) - 1 else None
        sent = await _send_text(client, bot_token, bound_chat, chunk, inline_keyboard=kb)
        # Each part is remembered with its bot, so replying to any of them continues with that bot.
        await asyncio.to_thread(tg.record_reply, owner, target["bot_id"], chunk, sent)


async def _handle_bind_command(client, chat: dict, sender: dict | None, owner: str, code: str, tg):
    from app.agent_telegram import TelegramError
    chat_id = chat.get("id")
    bot_token = _bot_token_for_send(owner, tg)
    if chat.get("type") != "private":
        await _send_text(client, bot_token, chat_id, "请在和 bot 的私聊里绑定。")
        return
    if not code:
        await _send_text(client, bot_token, chat_id, "请发送绑定码，例如 /bind abcdef01")
        return
    try:
        # The sender's id is recorded so later messages and button presses can be checked against it.
        result = tg.confirm_bind(owner, code, chat_id, (sender or {}).get("id"))
        bound = result.get("bound")
        if bound:
            await _send_text(client, bot_token, chat_id, "绑定成功！现在可以从这个聊天使用 Agent。")
        else:
            await _send_text(client, bot_token, chat_id, "绑定码无效或已过期。请在设置页面重新生成。")
    except TelegramError as exc:
        await _send_text(client, bot_token, chat_id, f"绑定失败：{_redact(exc)}")


def _bot_token_for_send(owner: str, tg) -> str:
    from app import agent_telegram
    # Not load_bind: before any chat is bound the bridge still has to answer /bind.
    return agent_telegram.load_bot_token(owner) or ""


async def _ask_agent(client, credential: str, bot_id: str, text: str):
    """One turn of the bot (its line, its model from the server catalog).  Returns the answer with its
    sources listed, any cards, and the conversation id the turn ran in."""
    import httpx
    from app.agent_telegram import sse_events

    body = {"bot_id": bot_id, "message": text[:MESSAGE_MAX_CHARS]}
    raw = ""
    try:
        async with client.stream(
            "POST",
            f"{LOCAL_API}/agent-chat/stream",
            json=body,
            headers={"X-Chat-Session": credential, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(300),
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                raw += chunk.decode("utf-8", errors="replace")
    except Exception as exc:
        raw += "\n\n" + "event: delta\ndata: " + json.dumps({"text": f"\n\n（Agent 回答出错：{_redact(exc)}）"}, ensure_ascii=False) + "\n\n"
    return format_turn(sse_events(raw))


def format_turn(events: list[tuple[str, dict]]):
    """The answer (with its sources listed), the cards and the conversation id of a parsed stream."""
    answer_parts: list[str] = []
    cards: list[dict] = []
    citations: list[dict] = []
    conversation_id = None
    for kind, data in events:
        if kind == "conversation":
            conversation_id = data.get("conversation_id")
        elif kind == "delta":
            answer_parts.append(str(data.get("text", "")))
        elif kind == "citations":
            citations = data.get("items", [])
        elif kind == "clarification":
            cards.append(data)
    answer = "".join(answer_parts)
    if citations:
        cite_lines = []
        for c in citations[:10]:
            if not isinstance(c, dict):
                continue
            ordinal = c.get("ordinal")
            title = str(c.get("title") or c.get("source_title") or "")[:80]
            if ordinal:
                cite_lines.append(f"  [{ordinal}] {title}")
        if cite_lines:
            answer = answer.strip() + "\n\n——\n" + "\n".join(cite_lines)

    return answer, cards, conversation_id


def _make_keyboard(cards: list[dict], pending: dict, cid: str, credential: str) -> list | None:
    if not cards:
        return None
    buttons = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        choices = card.get("choices") or []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            value = choice.get("value", "")
            if value.startswith("confirm:"):
                token = value[len("confirm:"):]
                short_id = _make_short_id()
                pending[short_id] = {"token": token, "operations": [], "conversation_id": cid, "credential": credential}
                label = str(choice.get("label", "同意"))[:20]
                buttons.append([{"text": label, "callback_data": f"ok:{short_id}"}])
    if not buttons:
        msg = cards[0].get("original_question") or (cards[0].get("display") or {}).get("title") or ""
        msg = str(msg)[:200]
        return buttons
    return buttons if buttons else None


def _make_short_id() -> str:
    # Pressing a button needs nothing but this id, so it must not be guessable.
    return secrets.token_urlsafe(9)


async def _handle_callback(client, callback: dict, pending: dict, owner: str, credential: str,
                           bound_chat: int, bound_user: int | None):
    cb_id = callback.get("id")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    data = str(callback.get("data") or "")

    if not chat_id or not cb_id:
        return
    if not _from_owner(chat, callback.get("from"), bound_chat, bound_user):
        return

    bot_token = _bot_token_for_send(owner, None)
    await _answer_callback(client, bot_token, cb_id)

    if not data.startswith("ok:"):
        return

    short_id = data[len("ok:"):]
    entry = pending.pop(short_id, None)
    if not entry:
        await _send_text(client, bot_token, chat_id, "这张确认卡已经过期了（每张卡只能用一次）。")
        return

    token = entry["token"]
    ops = entry["operations"]
    cid = entry["conversation_id"]
    cred = entry.get("credential") or credential

    try:
        from app.agent_workflow.consent import execute, ConsentError
        from app.agent_workflow.tools import execute_reversible
        result = await asyncio.to_thread(execute, cred, cid, token, ops, execute_reversible)
        ok = result.get("ok") if isinstance(result, dict) else False
        if ok:
            await _send_text(client, bot_token, chat_id, "已执行。")
        else:
            data_items = result.get("data", []) if isinstance(result, dict) else []
            errors = [str(d.get("error_code", "failed")) for d in data_items if isinstance(d, dict) and not d.get("ok")]
            await _send_text(client, bot_token, chat_id, f"未能执行：{', '.join(errors) or 'unknown'}")
    except ConsentError as exc:
        await _send_text(client, bot_token, chat_id, f"未能执行：{_redact(exc)}")
    except Exception as exc:
        await _send_text(client, bot_token, chat_id, f"执行出错：{_redact(exc)}")


async def _deliver_outbox(client, owner: str, bound_chat: int, tg) -> None:
    """Send what the bots queued (notify_telegram, alerts) to the bound chat, the only recipient there is.
    Each sent message keeps its Telegram id, so a reply to it goes back to its bot."""
    bot_token = _bot_token_for_send(owner, tg)
    if not bot_token:
        return
    for item in await asyncio.to_thread(tg.pending_messages, owner):
        sent = await _send_text(client, bot_token, bound_chat, item["text"], markdown=False)
        if sent is None:
            await asyncio.to_thread(tg.mark_failed, item["id"])
        else:
            await asyncio.to_thread(tg.mark_sent, item["id"], sent)


def _message_id(response) -> int | None:
    try:
        value = response.json().get("result", {}).get("message_id")
    except Exception:
        return None
    return value if isinstance(value, int) else None


async def _send_text(client, bot_token: str, chat_id: int, text: str,
                     inline_keyboard: list | None = None, *, markdown: bool = True) -> int | None:
    """Send one message; returns its Telegram message id, or None when it was not sent."""
    import httpx
    if not bot_token:
        return None
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    body: dict = {"chat_id": chat_id, "text": text[:ANSWER_MAX_CHARS], "disable_web_page_preview": True}
    if markdown:
        body["parse_mode"] = "Markdown"
    if inline_keyboard:
        body["reply_markup"] = {"inline_keyboard": inline_keyboard}
    try:
        response = await client.post(url, json=body)
    except httpx.HTTPError:
        response = None
    if response is not None and (response.status_code != 400 or not markdown):
        if response.status_code >= 400:
            print(f"Telegram sendMessage failed: {response.status_code} {_redact(response.text[:200])}",
                  file=sys.stderr)
            return None
        return _message_id(response)
    # Telegram answers 400, without raising, when it cannot parse the Markdown (one stray * or _ is enough).
    # Send the same words as plain text; deleting characters instead would turn -14.93% into 1493%.
    body["text"] = _plain(body["text"])
    body.pop("parse_mode", None)
    try:
        response = await client.post(url, json=body)
        if response.status_code >= 400:
            print(f"Telegram sendMessage failed: {response.status_code} {_redact(response.text[:200])}",
                  file=sys.stderr)
            return None
        return _message_id(response)
    except httpx.HTTPError as exc:
        print(f"Telegram sendMessage failed: {_redact(exc)}", file=sys.stderr)
        return None


def _plain(text: str) -> str:
    """The text as plain text: drop the backslashes that escaped Markdown, keep every other character."""
    return re.sub(r"\\([\[\]*_`])", r"\1", text)


async def _answer_callback(client, bot_token: str, callback_id: str) -> None:
    import httpx
    try:
        await client.post(f"{TELEGRAM_API}/bot{bot_token}/answerCallbackQuery",
                          json={"callback_query_id": callback_id})
    except httpx.HTTPError:
        pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Telegram bridge for the personal agent.")
    parser.add_argument("--dry-run", action="store_true", help="validate config and exit")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args.dry_run)))
    except KeyboardInterrupt:
        print("")
        raise SystemExit(0)