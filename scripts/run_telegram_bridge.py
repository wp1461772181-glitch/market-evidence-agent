"""Telegram bridge for the personal agent (plan §2.4).

Long-polls the Telegram Bot API with getUpdates (no webhook).  Only handles messages and button presses
from the bound account in the bound chat; strangers are ignored.  Owner text is forwarded to the agent chat API (a fixed conversation named
「手机」).  Consent cards become inline keyboard buttons with a short id (the real token stays server-side);
callback_query runs consent.execute once and sends a receipt.  Inbox briefs with channel=telegram are
delivered to this chat.

    python scripts/run_telegram_bridge.py            # long-poll forever
    python scripts/run_telegram_bridge.py --dry-run  # validate config and exit

The bridge reads the bot token and binding from the database (encrypted), the owner credential from
data/personal-chat-login.json, and the background model (the LLM key) from the database.  It calls the
local agent-chat stream endpoint via HTTP, so the API server must be running.  Fails closed when the
token or binding is missing.
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
INBOX_CHECK_INTERVAL = 120
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
    from app.agent_tasks import load_background_model, add_item
    owner = owner_hash(credential)

    bind = await asyncio.to_thread(tg.load_bind, owner)
    if not bind:
        print("Telegram bridge: no bound bot token or chat.  Save a token and bind a chat first.")
        return 0 if dry_run else 1
    if not bind.get("chat_id"):
        print("Telegram bridge: bot token saved but no chat is bound.  Run /bind in the settings page.")
        return 0 if dry_run else 1

    model = await asyncio.to_thread(load_background_model, owner)
    if not model:
        print("Telegram bridge: no background model configured.  The bridge cannot chat without a model.")
        return 0 if dry_run else 1

    bot_token = bind["bot_token"]
    bound_chat = bind["chat_id"]
    bound_user = bind.get("user_id")
    conversation_id = bind.get("conversation_id")
    del bind  # the decrypted token stays in bot_token only

    if dry_run:
        print("Bot token: ****" + bot_token[-4:])
        print(f"Bound chat: {bound_chat}")
        print(f"Conversation: {conversation_id or '(will create 「手机」 on first message)'}")
        print(f"Model: {model['provider']} / {model['model']}")
        print(f"Local API: {LOCAL_API}")
        return 0

    pending_cards: dict[str, dict] = {}

    async def _ensure_conversation() -> str:
        nonlocal conversation_id
        if conversation_id:
            cid = conversation_id
            try:
                from app.agent_chat_store import verify_conversation
                if await verify_conversation(cid, credential):
                    return cid
            except Exception:
                pass
        cid = await _store_create_conversation(credential)
        conversation_id = cid
        await asyncio.to_thread(tg.set_conversation, owner, cid)
        return cid

    last_inbox_check = 0.0
    offset = 0

    print(f"Telegram bridge started.  Chat: {bound_chat}, API: {LOCAL_API}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(LONG_POLL_TIMEOUT + 15)) as client:
        while True:
            try:
                now = time.time()
                if now - last_inbox_check >= INBOX_CHECK_INTERVAL:
                    await _deliver_inbox(client, credential, owner, bound_chat, tg)
                    last_inbox_check = now

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
                        await _handle_message(client, credential, owner, model, message,
                                              bound_chat, bound_user, pending_cards, tg,
                                              lambda: _ensure_conversation())
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


async def _handle_message(client, credential: str, owner: str, model: dict, message: dict,
                          bound_chat: int, bound_user: int | None, pending_cards: dict, tg, ensure_conv):
    chat = message.get("chat", {})
    if not _from_owner(chat, message.get("from"), bound_chat, bound_user):
        return
    text = str(message.get("text") or message.get("caption") or "").strip()
    entities = message.get("entities") or []
    bot_cmd = None
    for entity in entities:
        if entity.get("type") == "bot_command" and isinstance(entity.get("offset"), int):
            cmd_text = text[entity["offset"]:entity["offset"] + entity["length"]]
            if cmd_text.startswith("/"):
                bot_cmd = cmd_text
                break
    if bot_cmd:
        if bot_cmd.startswith("/start"):
            code = text[len(bot_cmd):].strip()
            await _handle_bind_command(client, chat, message.get("from"), owner, code, tg)
            return
        elif bot_cmd.startswith("/bind"):
            code = text[len(bot_cmd):].strip()
            await _handle_bind_command(client, chat, message.get("from"), owner, code, tg)
            return
        return

    if not text:
        return
    cid = await ensure_conv()
    answer, cards = await _ask_agent(client, credential, owner, model, text, cid)
    keyboard = _make_keyboard(cards, pending_cards, cid, credential)
    if answer:
        chunks = [answer[i:i+ANSWER_MAX_CHARS] for i in range(0, len(answer), ANSWER_MAX_CHARS)]
        for i, chunk in enumerate(chunks):
            kb = keyboard if i == len(chunks) - 1 else None
            await _send_text(client, bot_token, bound_chat, chunk, inline_keyboard=kb)
    elif keyboard:
        await _send_text(client, bot_token, bound_chat, "完成。", inline_keyboard=keyboard)
    else:
        await _send_text(client, bot_token, bound_chat, "（这条回复没有内容。）")


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
    bind = agent_telegram.load_bind(owner)
    if bind:
        return bind["bot_token"]
    return ""


async def _ask_agent(client, credential: str, owner: str, model: dict, text: str, conversation_id: str):
    """Call the local agent-chat stream API and return the answer and any cards."""
    import httpx

    body = {
        "provider": model["provider"],
        "model": model["model"],
        "api_key": model["api_key"],
        "message": text[:MESSAGE_MAX_CHARS],
        "conversation_id": conversation_id,
    }
    if model.get("base_url"):
        body["base_url"] = model["base_url"]

    answer_parts: list[str] = []
    cards: list[dict] = []
    citations: list[dict] = []

    try:
        async with client.stream(
            "POST",
            f"{LOCAL_API}/agent-chat/stream",
            json=body,
            headers={"X-Chat-Session": credential, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(300),
        ) as response:
            response.raise_for_status()
            buffer = ""
            async for chunk in response.aiter_bytes():
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    raw, buffer = buffer.split("\n\n", 1)
                    for line_str in raw.split("\n"):
                        line = line_str.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        kind = event.get("event")
                        if kind == "delta":
                            answer_parts.append(str(event.get("data", {}).get("text", "")))
                        elif kind == "citations":
                            citations = event.get("data", {}).get("items", [])
                        elif kind == "clarification":
                            cards.append(event.get("data", {}))
                        elif kind == "done":
                            pass
    except Exception as exc:
        answer_parts.append(f"\n\n（Agent 回答出错：{_redact(exc)}）")

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

    return answer, cards


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


async def _deliver_inbox(client, credential: str, owner: str, bound_chat: int, tg) -> None:
    """Deliver inbox briefs with channel_pending=telegram, then clear the flag."""
    from app.agent_tasks import list_inbox, set_item_status
    bot_token = _bot_token_for_send(owner, tg)
    if not bot_token:
        return
    items = list_inbox(owner, section="brief", limit=10)
    for item in items:
        payload = item.get("payload") or {}
        if payload.get("channel_pending") != "telegram":
            continue
        title = item.get("title", "")
        body = item.get("body", "")
        sources = item.get("sources") or []
        text = f"*{title}*\n\n{body}"
        if sources:
            source_lines = []
            for s in sources[:10]:
                ordinal = s.get("ordinal")
                s_title = str(s.get("title") or "")[:80]
                if ordinal:
                    source_lines.append(f"  \\[{ordinal}\\] {s_title}")
            if source_lines:
                text += "\n\n——\n" + "\n".join(source_lines)
        # Remove the channel_pending flag so we don't deliver it again
        try:
            set_item_status(owner, item["id"], "read")
        except Exception:
            pass
        try:
            await _send_text(client, bot_token, bound_chat, text[:ANSWER_MAX_CHARS])
        except Exception:
            pass


async def _send_text(client, bot_token: str, chat_id: int, text: str,
                     inline_keyboard: list | None = None) -> None:
    import httpx
    if not bot_token:
        return
    body: dict = {"chat_id": chat_id, "text": text[:ANSWER_MAX_CHARS],
                  "parse_mode": "Markdown", "disable_web_page_preview": True}
    if inline_keyboard:
        body["reply_markup"] = {"inline_keyboard": inline_keyboard}
    try:
        await client.post(f"{TELEGRAM_API}/bot{bot_token}/sendMessage", json=body)
    except httpx.HTTPError:
        # Retry without Markdown if it fails (common with unescaped characters)
        body["text"] = _strip_markdown(text[:ANSWER_MAX_CHARS])
        del body["parse_mode"]
        try:
            await client.post(f"{TELEGRAM_API}/bot{bot_token}/sendMessage", json=body)
        except httpx.HTTPError:
            pass


def _strip_markdown(text: str) -> str:
    for char in "*_[]()~`>#+-=|{}.!":
        text = text.replace(char, "")
    return text


async def _answer_callback(client, bot_token: str, callback_id: str) -> None:
    import httpx
    try:
        await client.post(f"{TELEGRAM_API}/bot{bot_token}/answerCallbackQuery",
                          json={"callback_query_id": callback_id})
    except httpx.HTTPError:
        pass


async def _store_create_conversation(credential: str) -> str:
    from app.agent_chat_store import create_conversation, set_conversation_title
    cid = await create_conversation(credential)
    await set_conversation_title(credential, cid, "手机")
    return cid


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