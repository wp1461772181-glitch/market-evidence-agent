"""Telegram bridge for the personal agent (plan §2.4).

Long-polls the Telegram Bot API with getUpdates (no webhook).  Only handles messages and button presses
from the bound account in the bound chat; strangers are ignored.  Until a chat is bound it answers
only /bind <code> (or /start <code>) in a private chat.  Owner text goes to a bot's line through the agent
chat API (second edition, stage D): a reply to a bot's message goes to that bot, a message starting with
「@名字」 to the bot of that name, anything else to the default bot 「助理」 (whose line the old 「手机」
conversation was folded into).  Each answer starts with the bot's name and is remembered by its Telegram
message id, so replying to it continues with that bot.  Answers go out as plain chat text: no [n] markers
and no sources list (agent_telegram.chat_text); links written in the answer stay.  Consent cards become
inline keyboard buttons with a short id (the real token stays in this process); pressing one sends the
card's own button value to that bot's line, exactly as the web page does, so the write runs through the
same confirmed handler (save to 资料库, index, ...) and the receipt is the bot's own report.
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
# Short ids of cards already pressed, so a second tap is told the card was handled, not that it expired.
USED_CARDS_KEPT = 256
_USED_CARDS: dict[str, None] = {}
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

    lock = _single_instance_lock()
    if lock is None:
        print("Telegram bridge: another bridge is already running (data/telegram-bridge.lock).", file=sys.stderr)
        return 1

    pending_cards: dict[str, dict] = {}
    last_outbox_check = 0.0
    offset = 0
    # UX batch 2 (P4): bots with their own Telegram bot, one polling task each, reconciled every round.
    own_tasks: dict[str, tuple[str, asyncio.Task]] = {}

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

                await _reconcile_own_channels(client, credential, owner, own_tasks, tg)

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
                                               bound_chat, bound_user, tg)
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
    reply_to = (message.get("reply_to_message") or {}).get("message_id")
    target = await asyncio.to_thread(tg.route, owner, text, reply_to if isinstance(reply_to, int) else None)
    if not target["text"]:
        return
    await _bot_turn(client, credential, owner, bound_chat, pending_cards, tg, target["bot_id"], target["bot_name"],
                    target["text"])


async def _bot_turn(client, credential: str, owner: str, bound_chat: int, pending_cards: dict, tg,
                    bot_id: str, bot_name: str, text: str, channel: dict | None = None, **press):
    """One turn of a bot, sent back as its chat message(s); ``press`` carries a card press (its conversation,
    card id and the button's label).  ``channel`` is the bot's own Telegram bot (UX batch 2, P4): the answer goes
    out through it, without the name in front, since that chat is the bot's alone."""
    bot_token = channel["token"] if channel else _bot_token_for_send(owner, tg)
    answer, cards, cid = await _ask_agent(client, credential, bot_id, text, **press)
    keyboard = _make_keyboard(cards, pending_cards, cid, credential, bot_id, bot_name) if cid else None
    if cards and not keyboard:
        answer = answer.strip() + "\n\n（这一步需要在网页里选择后确认。）"
    answer = ("" if channel else f"【{bot_name}】\n") + (answer.strip() or ("完成。" if keyboard else "（这条回复没有内容。）"))
    chunks = [answer[i:i+ANSWER_MAX_CHARS] for i in range(0, len(answer), ANSWER_MAX_CHARS)]
    for i, chunk in enumerate(chunks):
        kb = keyboard if i == len(chunks) - 1 else None
        sent = await _send_text(client, bot_token, bound_chat, chunk, inline_keyboard=kb)
        # Each part is remembered with its bot, so replying to any of them continues with that bot.
        if channel:
            await asyncio.to_thread(tg.record_reply, owner, bot_id, chunk, sent, channel["row_id"])
        else:
            await asyncio.to_thread(tg.record_reply, owner, bot_id, chunk, sent)


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


async def _ask_agent(client, credential: str, bot_id: str, text: str, *, conversation_id: str | None = None,
                     pending_request_id: str | None = None, display_message: str | None = None):
    """One turn of the bot (its line, its model from the server catalog).  Returns the answer as chat text,
    any cards, and the conversation id the turn ran in.  A card press sends the button's value with the
    card id, as the web page does."""
    import httpx
    from app.agent_telegram import sse_events

    body = {"bot_id": bot_id, "message": text[:MESSAGE_MAX_CHARS]}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if pending_request_id:
        body["pending_request_id"] = pending_request_id
        if display_message:
            body["display_message"] = display_message[:120]
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
    """The answer as Telegram chat text (no [n] markers, no sources list), the cards and the conversation id
    of a parsed stream.  The citations event is the web page's; Telegram does not list it."""
    from app.agent_telegram import chat_text
    answer_parts: list[str] = []
    cards: list[dict] = []
    conversation_id = None
    for kind, data in events:
        if kind == "conversation":
            conversation_id = data.get("conversation_id")
        elif kind == "delta":
            answer_parts.append(str(data.get("text", "")))
        elif kind == "clarification":
            # The stream names the card ``request_id`` (agent_chat_stream); the consent record calls it ``id``.
            card = dict(data)
            if not card.get("id") and card.get("request_id"):
                card["id"] = card["request_id"]
            cards.append(card)
    return chat_text("".join(answer_parts)), cards, conversation_id


def _make_keyboard(cards: list[dict], pending: dict, cid: str, credential: str, bot_id: str | None = None,
                   bot_name: str = "") -> list | None:
    """Inline buttons for the write cards: 同意 and 取消.  A card that needs ticking (forecast revision
    sources) has no Telegram form and gets no buttons."""
    buttons = []
    for card in cards:
        if not isinstance(card, dict) or card.get("options"):
            continue
        card_id = card.get("id") or card.get("request_id")
        if not card_id:
            continue
        row = []
        for choice in card.get("choices") or []:
            value = str((choice or {}).get("value", "")) if isinstance(choice, dict) else ""
            kind = "ok" if value.startswith("confirm:") else "no" if value.startswith("cancel:") else None
            if not kind:
                continue
            short_id = _make_short_id()
            label = str(choice.get("label") or ("同意" if kind == "ok" else "取消"))[:20]
            pending[short_id] = {"value": value, "card_id": str(card_id), "conversation_id": cid,
                                 "credential": credential, "bot_id": bot_id, "bot_name": bot_name, "label": label, "group": []}
            row.append({"text": label, "callback_data": f"{kind}:{short_id}"})
        # Either button of a card uses the card up, so pressing one forgets the other.
        group = [b["callback_data"].split(":", 1)[1] for b in row]
        for short_id in group:
            pending[short_id]["group"] = group
        if row:
            buttons.append(row)
    return buttons or None


def _make_short_id() -> str:
    # Pressing a button needs nothing but this id, so it must not be guessable.
    return secrets.token_urlsafe(9)


async def _handle_callback(client, callback: dict, pending: dict, owner: str, credential: str,
                           bound_chat: int, bound_user: int | None, tg=None, channel: dict | None = None):
    cb_id = callback.get("id")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    data = str(callback.get("data") or "")

    if not chat_id or not cb_id:
        return
    if not _from_owner(chat, callback.get("from"), bound_chat, bound_user):
        return

    if tg is None:
        from app import agent_telegram as tg
    bot_token = channel["token"] if channel else _bot_token_for_send(owner, tg)

    kind, _, short_id = data.partition(":")
    if kind not in {"ok", "no"}:
        await _answer_callback(client, bot_token, cb_id)
        return
    entry = pending.pop(short_id, None)
    if not entry:
        if short_id in _USED_CARDS:
            # A second tap while the first one runs (the agent turn takes seconds, live 2026-10-28) is not a
            # stale card: say so on the button, not with a chat message under the answer still on its way.
            await _answer_callback(client, bot_token, cb_id, "这张确认卡已经处理过了，结果见上面的回复。")
            return
        await _answer_callback(client, bot_token, cb_id)
        await _send_text(client, bot_token, chat_id, "这张确认卡已经过期了（每张卡只能用一次）。")
        return
    for other in entry.get("group") or [short_id]:
        pending.pop(other, None)
        _USED_CARDS[other] = None
    while len(_USED_CARDS) > USED_CARDS_KEPT:
        _USED_CARDS.pop(next(iter(_USED_CARDS)))
    await _answer_callback(client, bot_token, cb_id, "正在执行……" if kind == "ok" else "正在取消……")
    # The card's buttons go at once, so the card cannot be pressed again while its turn runs.
    await _clear_keyboard(client, bot_token, chat_id, message.get("message_id"))
    if not entry.get("bot_id"):
        await _send_text(client, bot_token, chat_id, "这张确认卡已经过期了，请在网页里重新确认。")
        return
    # The press goes to the bot's line as the button's own value, like a press on the web page: the server
    # finds the card, runs consent.execute with the operations it saved and the confirmed handler, and the
    # bot reports what was saved.  Calling consent.execute from here could not: the bridge holds neither
    # the operations (the payload digest) nor the turn context the handler needs.
    await _bot_turn(client, entry.get("credential") or credential, owner, bound_chat, pending, tg,
                    entry["bot_id"], entry.get("bot_name") or "", entry["value"], channel=channel,
                    conversation_id=entry.get("conversation_id"), pending_request_id=entry["card_id"],
                    display_message=entry.get("label"))


async def _deliver_outbox(client, owner: str, bound_chat: int, tg, channel: dict | None = None) -> None:
    """Send what the bots queued (notify_telegram, alerts) to the bound chat, the only recipient there is.
    Each sent message keeps its Telegram id, so a reply to it goes back to its bot.  With ``channel`` only the
    messages queued for that bot's own Telegram bot, through it."""
    bot_token = channel["token"] if channel else _bot_token_for_send(owner, tg)
    if not bot_token:
        return
    queued = (await asyncio.to_thread(tg.pending_messages, owner, 20, channel["row_id"]) if channel
              else await asyncio.to_thread(tg.pending_messages, owner))
    for item in queued:
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


async def _answer_callback(client, bot_token: str, callback_id: str, text: str | None = None) -> None:
    import httpx
    body = {"callback_query_id": callback_id}
    if text:
        body["text"] = text
    try:
        await client.post(f"{TELEGRAM_API}/bot{bot_token}/answerCallbackQuery", json=body)
    except httpx.HTTPError:
        pass


async def _clear_keyboard(client, bot_token: str, chat_id: int, message_id) -> None:
    """Take the buttons off a card message.  Best effort: a failed edit must not stop the press itself."""
    if not bot_token or not isinstance(message_id, int):
        return
    try:
        await client.post(f"{TELEGRAM_API}/bot{bot_token}/editMessageReplyMarkup",
                          json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
    except Exception as exc:
        print(f"Telegram editMessageReplyMarkup failed: {_redact(exc)}", file=sys.stderr)



# --- bots with their own Telegram bot (UX batch 2, P4) ---------------------------------------------------
# The shared bot above keeps working exactly as before.  Each bot that saved its own token gets one polling
# task here: whatever that Telegram bot receives from the owner goes to that bot's line (no 「@名字」), its
# answers and its queued messages go out through it, and its offset is kept in the database.  A 401 stops
# that task only (status invalid), a 409 (the token polled elsewhere) marks it conflict.  No limit on how many.

def _single_instance_lock():
    """An exclusive lock on data/telegram-bridge.lock for the life of the process; None when another bridge
    holds it (two pollers of one token make Telegram answer 409)."""
    import fcntl
    path = PROJECT_ROOT / "data" / "telegram-bridge.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


async def _reconcile_own_channels(client, credential: str, owner: str, tasks: dict, tg) -> None:
    """Start a task for each new own token, stop the ones removed or replaced, leave the rest running."""
    from app import agent_telegram_bots as own
    wanted = {c["row_id"]: c for c in await asyncio.to_thread(own.channels, owner)}
    for row_id, (fingerprint, task) in list(tasks.items()):
        current = wanted.get(row_id)
        if current is None or current["fingerprint"] != fingerprint or task.done():
            task.cancel()
            tasks.pop(row_id, None)
    for row_id, channel in wanted.items():
        if row_id not in tasks:
            tasks[row_id] = (channel["fingerprint"], asyncio.create_task(_own_channel_loop(client, credential, owner, channel, tg)))


async def _poll_own(client, token: str, offset: int) -> tuple[list | None, int | None]:
    """getUpdates for an own token: (updates, None), or (None, HTTP status) when Telegram refused."""
    import httpx
    try:
        response = await client.get(f"{TELEGRAM_API}/bot{token}/getUpdates",
                                    params={"offset": offset, "timeout": LONG_POLL_TIMEOUT,
                                            "allowed_updates": ["message", "callback_query"]})
    except httpx.TimeoutException:
        return [], None
    except httpx.HTTPError as exc:
        print(f"Telegram API error: {_redact(exc)}", file=sys.stderr)
        return None, 0
    if response.status_code in (401, 404, 409):
        return None, response.status_code
    try:
        data = response.json()
    except ValueError:
        return None, 0
    return (data.get("result") or [], None) if data.get("ok") else (None, response.status_code)


async def _own_channel_loop(client, credential: str, owner: str, channel: dict, tg) -> None:
    from app import agent_telegram_bots as own
    pending: dict[str, dict] = {}
    last_outbox = 0.0
    name = channel.get("bot_username") or channel["bot_name"]
    print(f"Telegram bridge: own bot @{name} for 「{channel['bot_name']}」 started.")
    await asyncio.to_thread(own.mark, channel["row_id"], "running")
    while True:
        try:
            state = await asyncio.to_thread(own.channel, channel["row_id"])
            if state is None:
                return
            channel = {**channel, "chat_id": state["chat_id"], "user_id": state["user_id"]}
            offset = max(int(state["offset"]), int(channel.get("offset") or 0))
            now = time.time()
            if channel["chat_id"] is not None and now - last_outbox >= OUTBOX_CHECK_INTERVAL:
                await _deliver_outbox(client, owner, channel["chat_id"], tg, channel)
                last_outbox = now
            updates, refused = await _poll_own(client, channel["token"], offset)
            if refused in (401, 404):
                await asyncio.to_thread(own.mark, channel["row_id"], "invalid", "Telegram 拒绝了这个 token（401）。请在 BotFather 检查后换一个。")
                print(f"Telegram bridge: own bot @{name}: token refused, stopped.", file=sys.stderr)
                return
            if refused == 409:
                await asyncio.to_thread(own.mark, channel["row_id"], "conflict", "这个 token 正在被别处轮询（409）。")
                await asyncio.sleep(30)
                continue
            if updates is None:
                await asyncio.sleep(5)
                continue
            for update in updates:
                if not isinstance(update, dict):
                    continue
                if isinstance(update.get("update_id"), int):
                    offset = update["update_id"] + 1
                    channel["offset"] = offset
                if isinstance(update.get("message"), dict):
                    await _handle_own_message(client, credential, owner, update["message"], channel, pending, tg)
                elif isinstance(update.get("callback_query"), dict) and channel["chat_id"] is not None:
                    await _handle_callback(client, update["callback_query"], pending, owner, credential,
                                           channel["chat_id"], channel["user_id"], tg, channel)
            if updates:
                await asyncio.to_thread(own.save_offset, channel["row_id"], offset)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Bridge error (own bot @{name}): {_redact(exc)}", file=sys.stderr)
            await asyncio.sleep(10)


async def _handle_own_message(client, credential: str, owner: str, message: dict, channel: dict, pending: dict, tg):
    """A message to a bot's own Telegram bot: /start <code> binds it; the owner's text goes to that bot."""
    chat = message.get("chat") or {}
    text = str(message.get("text") or message.get("caption") or "").strip()
    command = text.split(maxsplit=1)[0] if text.startswith("/") else ""
    if command.split("@", 1)[0] in {"/start", "/bind"}:
        if channel["chat_id"] is None or _from_owner(chat, message.get("from"), channel["chat_id"], channel["user_id"]):
            await _handle_own_bind(client, chat, message.get("from"), channel, text[len(command):].strip())
        return
    if channel["chat_id"] is None or not _from_owner(chat, message.get("from"), channel["chat_id"], channel["user_id"]):
        return
    if command or not text:
        return
    await _bot_turn(client, credential, owner, channel["chat_id"], pending, tg, channel["bot_id"], channel["bot_name"],
                    text, channel=channel)


async def _handle_own_bind(client, chat: dict, sender: dict | None, channel: dict, code: str):
    from app import agent_telegram_bots as own
    from app.agent_telegram import TelegramError
    chat_id = chat.get("id")
    if chat.get("type") != "private":
        await _send_text(client, channel["token"], chat_id, "请在和 bot 的私聊里绑定。", markdown=False)
        return
    if not code:
        await _send_text(client, channel["token"], chat_id, f"请在「{channel['bot_name']}」的档案页生成绑定码，再点那里的链接。", markdown=False)
        return
    try:
        await asyncio.to_thread(own.confirm_bind, channel["row_id"], code, chat_id, (sender or {}).get("id"))
        channel["chat_id"], channel["user_id"] = chat_id, (sender or {}).get("id")
        await _send_text(client, channel["token"], chat_id, f"绑定成功！这里的消息都会交给「{channel['bot_name']}」。", markdown=False)
    except TelegramError as exc:
        reason = {"bind_code_expired": "绑定码已过期（30 分钟），请重新生成。", "bind_code_mismatch": "绑定码不对，请重新生成。"}.get(str(exc), "绑定码无效或已过期，请在档案页重新生成。")
        await _send_text(client, channel["token"], chat_id, reason, markdown=False)


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