"""The owner's Telegram bridge binding (plan §2.4).

The bot token is encrypted with secret_box and never returned by any endpoint.  A one-time bind code,
stored as a hash, lets the owner prove they control a Telegram account; once confirmed, the bridge
only handles messages from that chat.  Strangers are ignored.

Second edition, stage D: sending is a tool.  A bot calls ``notify_telegram`` (``notify`` here): the text
starts with the bot's name, every figure carries one of this turn's evidence numbers (checked, then dropped:
Telegram gets plain chat text with no sources list, see ``chat_text``), and the bot's daily cap holds.
The recipient is the bound chat, decided by the bridge, never by the model: the tool has no recipient argument and nothing here stores one.  Alerts with 也发 Telegram use the
same queue (``enqueue``).  ``route`` finds the bot a message from the owner is for: the bot whose message
it replies to, else an ``@名字`` prefix, else the default bot.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text as sql

from .agent_chat_models import AgentBot, AgentTelegramBind, AgentTelegramMessage
from .database import SessionLocal
from .secret_box import CredentialEncryptionError, decrypt_secret, encrypt_secret

BIND_CODE_LENGTH = 8
BIND_CODE_EXPIRE_MINUTES = 30


class TelegramError(ValueError):
    """A short code the API may show; never a stored value."""


def _stamp(value):
    return value.astimezone(UTC).isoformat() if value else None


def status(owner_hash: str) -> dict:
    """What the settings page shows.  Never the token, nor any part of it."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is None:
            return {"configured": False, "has_token": False, "bound": False,
                    "bot_username": None, "chat_id": None, "bound_at": None}
        bound = bool(row.chat_id and row.user_id and row.bot_token_cipher)
        return {"configured": True, "has_token": bool(row.bot_token_cipher),
                "bound": bound, "bot_username": row.bot_username,
                "chat_id": row.chat_id, "bound_at": _stamp(row.bound_at) if bound else None,
                "bind_code_active": bool(row.bind_code_hash)}


def save_token(owner_hash: str, token: str) -> dict:
    """Encrypt and store the bot token.  Clearing the chat and user ids when the token changes."""
    if not token or not 8 <= len(token) <= 256 or ":" not in token:
        raise TelegramError("invalid_bot_token")
    try:
        ciphertext = encrypt_secret(token)
    except CredentialEncryptionError as exc:
        raise TelegramError("credential_key_missing") from exc
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is None:
            row = AgentTelegramBind(owner_hash=owner_hash)
            db.add(row)
        changed = row.bot_token_cipher and row.bot_token_cipher != ciphertext
        row.bot_token_cipher = ciphertext
        row.bot_username = None
        if changed:
            row.chat_id = None
            row.user_id = None
            row.bind_code_hash = None
            row.bound_at = None
            row.conversation_id = None
        db.commit()
    return status(owner_hash)


def _make_code_hash(code: str) -> str:
    return hashlib.sha256(f"telegram-bind:{code}".encode()).hexdigest()


def start_bind(owner_hash: str) -> dict:
    """Generate a one-time code the owner sends to the bot to prove identity.  The code is 8 hex
    chars, good for 30 minutes; only the hash is stored, never the code, so an old bind_code_hash
    is useless even when the token is still encrypted at rest."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is None or not row.bot_token_cipher:
            raise TelegramError("no_bot_token")
        code = secrets.token_hex(BIND_CODE_LENGTH // 2)
        row.bind_code_hash = _make_code_hash(code)
        row.bind_code_expires_at = datetime.now(UTC) + timedelta(minutes=BIND_CODE_EXPIRE_MINUTES)
        db.commit()
    return {"code": code, "bot_username": status(owner_hash).get("bot_username")}


def code_expired(expires_at, now: datetime | None = None) -> bool:
    """A bind code past its 30 minutes.  A code minted before expiry was recorded (NULL) is not expired."""
    return bool(expires_at and (now or datetime.now(UTC)) > expires_at)


def confirm_bind(owner_hash: str, code: str, chat_id: int | None, user_id: int | None) -> dict:
    """Called by the bridge when a stranger sends the code.  The bridge passes the incoming chat_id
    and user_id; if the code matches the stored hash, the bind is confirmed."""
    if not isinstance(code, str) or len(code) != BIND_CODE_LENGTH:
        raise TelegramError("invalid_bind_code")
    if chat_id is None or not isinstance(chat_id, int):
        raise TelegramError("invalid_chat_id")
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash, with_for_update=True)
        if row is None or not row.bot_token_cipher or not row.bind_code_hash:
            raise TelegramError("no_active_bind")
        if not secrets.compare_digest(row.bind_code_hash, _make_code_hash(code)):
            raise TelegramError("bind_code_mismatch")
        now = datetime.now(UTC)
        if code_expired(row.bind_code_expires_at, now):
            raise TelegramError("bind_code_expired")
        if row.bound_at and (now - row.bound_at).total_seconds() < 1:
            raise TelegramError("bind_already_confirmed")
        row.chat_id = chat_id
        row.user_id = user_id if user_id is not None else None
        row.bind_code_hash = None
        row.bound_at = now
        db.commit()
    return status(owner_hash)


def unbind(owner_hash: str) -> dict:
    """Clear the binding (chat and user).  The token stays so the owner can re-bind."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is None or not row.chat_id:
            raise TelegramError("not_bound")
        row.chat_id = None
        row.user_id = None
        row.bind_code_hash = None
        row.bound_at = None
        row.conversation_id = None
        db.commit()
    return status(owner_hash)


def load_bot_token(owner_hash: str) -> str | None:
    """The bot token alone, whether or not a chat is bound: the bridge needs it to receive the /bind that
    binds one.  None when no token is saved or it no longer decrypts."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is None or not row.bot_token_cipher:
            return None
        try:
            return decrypt_secret(row.bot_token_cipher)
        except CredentialEncryptionError:
            return None


def load_bind(owner_hash: str) -> dict | None:
    """The bridge's lookup: bot token (decrypted), bound chat, conversation id.  Returns None when
    there is no saved token or the token no longer decrypts."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is None or not row.bot_token_cipher or not row.chat_id:
            return None
        try:
            token = decrypt_secret(row.bot_token_cipher)
        except CredentialEncryptionError:
            return None
        return {"bot_token": token, "chat_id": row.chat_id, "user_id": row.user_id,
                "conversation_id": str(row.conversation_id) if row.conversation_id else None,
                "bot_username": row.bot_username,
                "bind_code_hash": row.bind_code_hash}


def set_bot_username(owner_hash: str, username: str) -> None:
    """The bridge updates the bot's username from getMe, so status() shows it."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is not None:
            row.bot_username = str(username)[:64]
            db.commit()


def set_conversation(owner_hash: str, conversation_id: str) -> None:
    """Store the 「手机」conversation id so messages from this chat thread into it."""
    from uuid import UUID
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is not None:
            row.conversation_id = UUID(conversation_id)
            db.commit()


def check_bind_code(owner_hash: str, code: str) -> bool:
    """Return True when ``code`` matches the stored pending bind code (constant-time)."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBind, owner_hash)
        if row is None or not row.bind_code_hash:
            return False
        return secrets.compare_digest(row.bind_code_hash, _make_code_hash(code))

# --- stage D: notify_telegram, the outbox and routing ---------------------------------------------------

NOTIFY_MAX_CHARS = 1500
MESSAGE_MAX_CHARS = 3800
CAP_TIMEZONE = "Asia/Shanghai"
SEND_ATTEMPTS = 3
COUNTED = ("notify", "alert")
_MARKER = re.compile(r"\[(\d{1,3})\]")
_SEGMENT = re.compile(r"[^。！？!?；;\n]+")
_DIGIT = re.compile(r"\d")


class NotifyError(ValueError):
    """A refused notification; the message is a short code the model and the audit see."""


def ensure_columns() -> None:
    """Widen the bound chat and user ids of a table made with 32-bit columns (Telegram ids pass 2**31)."""
    from .database import engine
    with engine.begin() as connection:
        for column in ("chat_id", "user_id"):
            connection.execute(sql(f"ALTER TABLE agent_telegram_bind ALTER COLUMN {column} TYPE BIGINT"))
        # UX batch 2 (P4): bind codes expire; a message names the bot's own Telegram bot it went through.
        connection.execute(sql("ALTER TABLE agent_telegram_bind ADD COLUMN IF NOT EXISTS bind_code_expires_at TIMESTAMPTZ"))
        connection.execute(sql("ALTER TABLE agent_telegram_messages ADD COLUMN IF NOT EXISTS telegram_bot_row_id UUID"))


def _day_start(now: datetime) -> datetime:
    zone = ZoneInfo(CAP_TIMEZONE)
    local = now.astimezone(zone)
    return datetime.combine(local.date(), time(0, 0), tzinfo=zone).astimezone(UTC)


def sent_today(db, owner_hash: str, bot_id, now: datetime) -> int:
    return int(db.scalar(select(func.count()).select_from(AgentTelegramMessage).where(
        AgentTelegramMessage.owner_hash == owner_hash, AgentTelegramMessage.bot_id == bot_id,
        AgentTelegramMessage.source.in_(COUNTED), AgentTelegramMessage.created_at >= _day_start(now))) or 0)


def _bound(db, owner_hash: str) -> bool:
    row = db.get(AgentTelegramBind, owner_hash)
    return bool(row is not None and row.bot_token_cipher and row.chat_id)


def own_channel(db, owner_hash: str, bot_id):
    """The bot's own Telegram bot when it has one and it is bound (UX batch 2, P4), else None."""
    from .agent_chat_models import AgentTelegramBot
    row = db.scalars(select(AgentTelegramBot).where(AgentTelegramBot.owner_hash == owner_hash,
                                                    AgentTelegramBot.bot_id == bot_id)).first()
    return row if row is not None and row.chat_id and row.status != "invalid" else None


def enqueue(db, owner_hash: str, bot_id, text: str, *, source: str, now: datetime | None = None,
            inbox_item_id=None) -> int:
    """Queue one message of a bot for the bound chat (the one send path of notify and alerts).  Returns how
    many it may still send today.  Raises NotifyError telegram_not_bound or notify_daily_cap."""
    now = now or datetime.now(UTC)
    bot = db.get(AgentBot, uuid.UUID(str(bot_id))) if bot_id else None
    if bot is None or bot.owner_hash != owner_hash:
        raise NotifyError("unknown_bot")
    # A bot with its own bound Telegram bot sends there, without its name in front; any other bot goes
    # through the shared one with 「【名字】」 (UX batch 2, P4).
    channel = own_channel(db, owner_hash, bot.id)
    if channel is None and not _bound(db, owner_hash):
        raise NotifyError("telegram_not_bound")
    used = sent_today(db, owner_hash, bot.id, now)
    if used >= bot.daily_notify_cap:
        raise NotifyError("notify_daily_cap")
    if channel is not None:
        text = text.removeprefix(f"【{bot.name}】\n")
    db.add(AgentTelegramMessage(owner_hash=owner_hash, bot_id=bot.id, source=source, text=text[:MESSAGE_MAX_CHARS],
                                status="pending", created_at=now,
                                inbox_item_id=uuid.UUID(str(inbox_item_id)) if inbox_item_id else None,
                                telegram_bot_row_id=channel.id if channel is not None else None))
    db.flush()
    return bot.daily_notify_cap - used - 1


def check_evidence(text: str, evidence_ids: list[int], ordinals: set[int]) -> list[int]:
    """The evidence numbers a notification uses, after the rules: each [n] in the text and each listed id
    is one of this turn's evidence numbers, and every sentence with a figure in it carries a [n]."""
    used = [int(n) for n in _MARKER.findall(text)]
    wanted = list(dict.fromkeys([*evidence_ids, *used]))
    if any(n not in ordinals for n in wanted):
        raise NotifyError("unknown_evidence")
    for segment in _SEGMENT.findall(text):
        if _DIGIT.search(_MARKER.sub("", segment)) and not _MARKER.search(segment):
            raise NotifyError("unsupported_number")
    return wanted


# Telegram is a chat: what goes out reads like a message, never a paper.  The [n] evidence markers and any
# sources appendix stay in the web line (and the notify check still needs them); a link written into the
# answer itself is kept.
_INLINE_MARKERS = re.compile(r"[ \t]?\[\d{1,3}(?:\s*[,，、]\s*\d{1,3})*\](?!\()")
# A heading alone on its line (「来源：」, **参考资料**, ## Sources) or one followed by its list (「来源：路透」).
_SOURCES_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:来源|资料来源|資料來源|参考来源|参考资料|引用来源|引用|出处|"
    r"sources?|references?|citations?)\s*(?:\*\*|__)?\s*(?:[:：].*)?$", re.I)
_SOURCE_LINE = re.compile(r"^\s*(?:[-*•]\s*)?\[\d{1,3}\]")
_RULE = re.compile(r"^\s*(?:—+|-{3,}|_{3,}|\*{3,})\s*$")


def chat_text(text: str) -> str:
    """The answer as a Telegram chat message: the trailing sources block (a 「来源：」 heading, a rule, a list of
    [n] lines) is cut and the [n] markers are dropped from the sentences; links in the body stay."""
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    # Walk up from the end over the appendix.  A blank line or a rule is cut only above a heading or a [n]
    # line, so an answer that merely ends with a rule keeps it.
    cut, found = len(lines), False
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if _SOURCE_LINE.match(line) or _SOURCES_HEADING.match(line):
            cut, found = index, True
        elif not line.strip() or _RULE.match(line):
            if found:
                cut = index
        else:
            break
    body = "\n".join(_INLINE_MARKERS.sub("", line).rstrip() for line in lines[:cut])
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def render(bot_name: str, text: str, citations: list[dict] | None = None, used: list[int] | None = None) -> str:
    """The message as sent: the bot's name first, then the text as plain chat.  ``citations`` and ``used``
    are accepted for the callers' grounding check and deliberately not listed (no sources appendix)."""
    return "\n".join([f"【{bot_name}】", chat_text(text)])


def notify(owner_hash: str, bot_id, text: str, evidence_ids: list[int], citations: list[dict], *,
           now: datetime | None = None) -> dict:
    """The notify_telegram tool: queue the bot's message for the owner's bound chat.  ``citations`` are this
    turn's numbered evidence; nothing else can be cited."""
    text = str(text or "").strip()
    if not text or len(text) > NOTIFY_MAX_CHARS:
        raise NotifyError("invalid_text")
    ordinals = {c["ordinal"] for c in citations if isinstance(c, dict) and type(c.get("ordinal")) is int}
    used = check_evidence(text, [int(n) for n in evidence_ids or []], ordinals)
    with SessionLocal() as db:
        if not bot_id:
            from .agent_bots import ensure_default_bot
            bot_id = ensure_default_bot(owner_hash).id
        bot = db.get(AgentBot, uuid.UUID(str(bot_id)))
        if bot is None or bot.owner_hash != owner_hash:
            raise NotifyError("unknown_bot")
        left = enqueue(db, owner_hash, bot.id, render(bot.name, text, citations, used), source="notify", now=now)
        db.commit()
    return {"status": "queued_for_telegram", "remaining_today": left,
            "note": "已排队发到你绑定的 Telegram；收件人由服务端决定。"}


def pending_messages(owner_hash: str, limit: int = 20, telegram_bot_row_id=None) -> list[dict]:
    """Queued messages of one Telegram bot: the shared one (None) or a bot's own (its agent_telegram_bots id)."""
    with SessionLocal() as db:
        channel = (AgentTelegramMessage.telegram_bot_row_id.is_(None) if telegram_bot_row_id is None
                   else AgentTelegramMessage.telegram_bot_row_id == uuid.UUID(str(telegram_bot_row_id)))
        rows = db.scalars(select(AgentTelegramMessage).where(AgentTelegramMessage.owner_hash == owner_hash,
                                                             AgentTelegramMessage.status == "pending", channel)
                          .order_by(AgentTelegramMessage.created_at).limit(limit)).all()
        return [{"id": str(r.id), "bot_id": str(r.bot_id) if r.bot_id else None, "text": r.text} for r in rows]


def mark_sent(message_id, telegram_message_id: int | None) -> None:
    with SessionLocal() as db:
        row = db.get(AgentTelegramMessage, uuid.UUID(str(message_id)))
        if row is not None:
            row.status, row.sent_at, row.telegram_message_id = "sent", datetime.now(UTC), telegram_message_id
            db.commit()


def mark_failed(message_id) -> None:
    """One more failed attempt; after SEND_ATTEMPTS it stops being retried."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramMessage, uuid.UUID(str(message_id)))
        if row is not None:
            row.attempts += 1
            if row.attempts >= SEND_ATTEMPTS:
                row.status = "failed"
            db.commit()


def record_reply(owner_hash: str, bot_id, text: str, telegram_message_id: int | None, telegram_bot_row_id=None) -> None:
    """An answer the bridge sent for a bot, so a reply to it goes back to that bot.  Not counted in the cap."""
    with SessionLocal() as db:
        db.add(AgentTelegramMessage(owner_hash=owner_hash, bot_id=uuid.UUID(str(bot_id)) if bot_id else None,
                                    source="reply", text=str(text)[:MESSAGE_MAX_CHARS], status="sent",
                                    telegram_message_id=telegram_message_id, sent_at=datetime.now(UTC),
                                    telegram_bot_row_id=uuid.UUID(str(telegram_bot_row_id)) if telegram_bot_row_id else None))
        db.commit()


def route(owner_hash: str, text: str, reply_to_message_id: int | None = None) -> dict:
    """Which bot an owner's Telegram message is for: the bot whose message it replies to; else a leading
    ``@名字`` naming one of the owner's bots (removed from the text); else the default bot."""
    from .agent_bots import ensure_default_bot
    default = ensure_default_bot(owner_hash)
    text = str(text or "").strip()
    with SessionLocal() as db:
        if reply_to_message_id is not None:
            # Message ids are per chat: only the shared bot's messages are looked up here.
            row = db.scalars(select(AgentTelegramMessage).where(
                AgentTelegramMessage.owner_hash == owner_hash,
                AgentTelegramMessage.telegram_bot_row_id.is_(None),
                AgentTelegramMessage.telegram_message_id == int(reply_to_message_id))
                .order_by(AgentTelegramMessage.created_at.desc()).limit(1)).first()
            bot = db.get(AgentBot, row.bot_id) if row is not None and row.bot_id else None
            if bot is not None and bot.owner_hash == owner_hash:
                return {"bot_id": str(bot.id), "bot_name": bot.name, "text": text, "via": "reply"}
        if text.startswith(("@", "＠")):
            names = sorted(((b.name, b) for b in db.scalars(select(AgentBot).where(AgentBot.owner_hash == owner_hash))),
                           key=lambda pair: -len(pair[0]))
            for name, bot in names:
                if text[1:].startswith(name):
                    rest = text[1 + len(name):].lstrip(" ：:，,")
                    return {"bot_id": str(bot.id), "bot_name": bot.name, "text": rest, "via": "mention"}
    return {"bot_id": str(default.id), "bot_name": default.name, "text": text, "via": "default"}


def sse_events(stream_text: str) -> list[tuple[str, dict]]:
    """The (event, data) pairs of an agent-chat stream: the event name is on its own ``event:`` line."""
    events = []
    for block in stream_text.split("\n\n"):
        name, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if name and data:
            try:
                events.append((name, json.loads("\n".join(data))))
            except json.JSONDecodeError:
                continue
    return events
