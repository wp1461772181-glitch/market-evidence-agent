"""A bot's own Telegram bot (UX batch 2, P4).

Optional and per bot.  The shared Telegram bot of ``agent_telegram_bind`` stays the default bot's and every
other bot's fallback (its messages start with 「【名字】」, ``agent_telegram.enqueue``).  A bot with its own:

- saves a BotFather token here (checked with getMe, encrypted with secret_box, never returned: the page sees
  ``@username`` and the last four characters only); one token belongs to one bot (``token_fingerprint``);
- is bound by the owner sending ``/start <code>`` to that Telegram bot (the code is good for 30 minutes);
- receives: whatever its Telegram bot gets goes to this bot's line, no 「@名字」 needed (the bridge);
- sends: ``enqueue`` puts its messages on its own Telegram bot, without the name in front.

There is no limit on how many bots have one (Levi, 2026-10-28).
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .agent_chat_models import AgentBot, AgentTelegramBind, AgentTelegramBot
from .agent_telegram import BIND_CODE_EXPIRE_MINUTES, BIND_CODE_LENGTH, TelegramError, _make_code_hash, code_expired
from .agent_telegram import status as shared_status
from .database import SessionLocal
from .secret_box import CredentialEncryptionError, decrypt_secret, encrypt_secret

TELEGRAM_API = "https://api.telegram.org"


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(f"tg-token:{token}".encode()).hexdigest()


def fetch_me(token: str) -> dict:
    """Telegram's getMe for a token: {"ok": bool, "id", "username"} or {"ok": False, "unreachable": True}."""
    import httpx
    try:
        response = httpx.get(f"{TELEGRAM_API}/bot{token}/getMe", timeout=10)
    except httpx.HTTPError:
        return {"ok": False, "unreachable": True}
    if response.status_code in (401, 404):
        return {"ok": False}
    try:
        result = response.json().get("result") or {}
    except ValueError:
        return {"ok": False, "unreachable": True}
    return {"ok": bool(result.get("id")), "id": result.get("id"), "username": result.get("username")}


def _bot(db, owner_hash: str, bot_id) -> AgentBot:
    try:
        bot = db.get(AgentBot, uuid.UUID(str(bot_id)))
    except ValueError:
        bot = None
    if bot is None or bot.owner_hash != owner_hash:
        raise TelegramError("unknown_bot")
    return bot


def _row(db, owner_hash: str, bot_id) -> AgentTelegramBot | None:
    return db.scalars(select(AgentTelegramBot).where(AgentTelegramBot.owner_hash == owner_hash,
                                                     AgentTelegramBot.bot_id == bot_id)).first()


def _view(row: AgentTelegramBot | None, token_tail: str | None = None) -> dict:
    if row is None:
        return {"shared": False, "has_token": False, "bound": False, "status": "none", "bot_username": None,
                "token_tail": None, "bound_at": None, "last_error": None, "bind_code_active": False}
    bound = bool(row.chat_id)
    if token_tail is None:
        try:
            token_tail = decrypt_secret(row.token_cipher)[-4:]
        except CredentialEncryptionError:
            token_tail = None
    return {"shared": False, "has_token": True, "bound": bound, "status": row.status if bound or row.status == "invalid" else "unbound",
            "bot_username": row.bot_username, "token_tail": token_tail,
            "bound_at": row.bound_at.astimezone(UTC).isoformat() if bound and row.bound_at else None,
            "last_error": row.last_error,
            "bind_code_active": bool(row.bind_code_hash and not code_expired(row.bind_code_expires_at))}


def status(owner_hash: str, bot_id) -> dict:
    """What the bot's 档案 shows.  The default bot's Telegram is the shared one (settings page)."""
    with SessionLocal() as db:
        bot = _bot(db, owner_hash, bot_id)
        if bot.is_default:
            return {**shared_status(owner_hash), "shared": True}
        return _view(_row(db, owner_hash, bot.id))


def _fingerprint_taken(db, owner_hash: str, fingerprint: str, bot_id) -> bool:
    other = db.scalars(select(AgentTelegramBot).where(AgentTelegramBot.token_fingerprint == fingerprint)).first()
    if other is not None and other.bot_id != bot_id:
        return True
    # The shared bot's token is not fingerprinted in its table: compare it decrypted.
    shared = db.get(AgentTelegramBind, owner_hash)
    if shared is not None and shared.bot_token_cipher:
        try:
            return token_fingerprint(decrypt_secret(shared.bot_token_cipher)) == fingerprint
        except CredentialEncryptionError:
            return False
    return False


def save_token(owner_hash: str, bot_id, token: str, *, me=None) -> dict:
    """Save (or replace) a bot's own Telegram token.  A new token clears the binding and the update offset."""
    token = str(token or "").strip()
    if not 8 <= len(token) <= 256 or ":" not in token:
        raise TelegramError("invalid_bot_token")
    with SessionLocal() as db:
        bot = _bot(db, owner_hash, bot_id)
        if bot.is_default:
            raise TelegramError("default_bot_uses_shared")
        fingerprint = token_fingerprint(token)
        if _fingerprint_taken(db, owner_hash, fingerprint, bot.id):
            raise TelegramError("token_in_use")
    info = (me or fetch_me)(token)
    if not info.get("ok") and not info.get("unreachable"):
        raise TelegramError("invalid_bot_token")
    try:
        cipher = encrypt_secret(token)
    except CredentialEncryptionError as exc:
        raise TelegramError("credential_key_missing") from exc
    with SessionLocal() as db:
        bot = _bot(db, owner_hash, bot_id)
        row = _row(db, owner_hash, bot.id)
        if row is None:
            row = AgentTelegramBot(owner_hash=owner_hash, bot_id=bot.id, token_cipher=cipher, token_fingerprint=fingerprint,
                                   update_offset=0, status="unbound")
            db.add(row)
        elif row.token_fingerprint != fingerprint:
            row.token_cipher, row.token_fingerprint = cipher, fingerprint
            row.chat_id = row.user_id = row.bound_at = None
            row.bind_code_hash = row.bind_code_expires_at = None
            row.update_offset, row.status, row.last_error = 0, "unbound", None
        row.tg_bot_id = info.get("id")
        row.bot_username = str(info.get("username") or "")[:64] or None
        db.commit()
        return _view(row, token[-4:])


def start_bind(owner_hash: str, bot_id) -> dict:
    """A one-time code for ``/start <code>`` in the bot's own Telegram bot, with the t.me link that sends it."""
    with SessionLocal() as db:
        bot = _bot(db, owner_hash, bot_id)
        row = _row(db, owner_hash, bot.id)
        if row is None:
            raise TelegramError("no_bot_token")
        code = secrets.token_hex(BIND_CODE_LENGTH // 2)
        row.bind_code_hash = _make_code_hash(code)
        row.bind_code_expires_at = datetime.now(UTC) + timedelta(minutes=BIND_CODE_EXPIRE_MINUTES)
        db.commit()
        link = f"https://t.me/{row.bot_username}?start={code}" if row.bot_username else None
        return {"code": code, "bot_username": row.bot_username, "link": link,
                "expires_at": row.bind_code_expires_at.isoformat()}


def confirm_bind(row_id, code: str, chat_id, user_id, *, now: datetime | None = None) -> dict:
    """The bridge, when someone sends /start <code> to the bot's own Telegram bot in a private chat."""
    if not isinstance(code, str) or len(code) != BIND_CODE_LENGTH:
        raise TelegramError("invalid_bind_code")
    if not isinstance(chat_id, int):
        raise TelegramError("invalid_chat_id")
    with SessionLocal() as db:
        row = db.get(AgentTelegramBot, uuid.UUID(str(row_id)), with_for_update=True)
        if row is None or not row.bind_code_hash:
            raise TelegramError("no_active_bind")
        if not secrets.compare_digest(row.bind_code_hash, _make_code_hash(code)):
            raise TelegramError("bind_code_mismatch")
        now = now or datetime.now(UTC)
        if code_expired(row.bind_code_expires_at, now):
            raise TelegramError("bind_code_expired")
        row.chat_id, row.user_id, row.bound_at = chat_id, user_id if isinstance(user_id, int) else None, now
        row.bind_code_hash = row.bind_code_expires_at = None
        row.status, row.last_error = "running", None
        db.commit()
        return _view(row)


def unbind(owner_hash: str, bot_id) -> dict:
    """Forget the bound chat; the token stays so the owner can bind again."""
    with SessionLocal() as db:
        bot = _bot(db, owner_hash, bot_id)
        row = _row(db, owner_hash, bot.id)
        if row is None or not row.chat_id:
            raise TelegramError("not_bound")
        row.chat_id = row.user_id = row.bound_at = None
        row.bind_code_hash = row.bind_code_expires_at = None
        row.status = "unbound"
        db.commit()
        return _view(row)


def delete_token(owner_hash: str, bot_id) -> dict:
    """Remove the bot's own Telegram bot; it falls back to the shared one.  Revoke the token in BotFather too."""
    with SessionLocal() as db:
        bot = _bot(db, owner_hash, bot_id)
        row = _row(db, owner_hash, bot.id)
        if row is not None:
            db.delete(row)
            db.commit()
        return _view(None)


def delete_for_bot(db, owner_hash: str, bot_id) -> None:
    """Called inside agent_bots.delete_bot's session: the bot's Telegram row goes with it."""
    row = _row(db, owner_hash, bot_id)
    if row is not None:
        db.delete(row)


# --- the bridge's side ------------------------------------------------------------------------------------

def channels(owner_hash: str) -> list[dict]:
    """Every own Telegram bot of the owner with a usable token, for the bridge to poll.  Tokens are decrypted
    here and live only in the bridge process."""
    out = []
    with SessionLocal() as db:
        rows = db.scalars(select(AgentTelegramBot).where(AgentTelegramBot.owner_hash == owner_hash)).all()
        names = {b.id: b.name for b in db.scalars(select(AgentBot).where(AgentBot.owner_hash == owner_hash))}
        for row in rows:
            if row.status == "invalid" or row.bot_id not in names:
                continue
            try:
                token = decrypt_secret(row.token_cipher)
            except CredentialEncryptionError:
                continue
            out.append({"row_id": str(row.id), "bot_id": str(row.bot_id), "bot_name": names[row.bot_id], "token": token,
                        "fingerprint": row.token_fingerprint, "chat_id": row.chat_id, "user_id": row.user_id,
                        "offset": int(row.update_offset or 0), "bot_username": row.bot_username})
    return out


def channel(row_id) -> dict | None:
    """One channel's current binding (re-read each round, so a /start or an unbind applies at once)."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBot, uuid.UUID(str(row_id)))
        if row is None:
            return None
        return {"chat_id": row.chat_id, "user_id": row.user_id, "status": row.status, "offset": int(row.update_offset or 0)}


def save_offset(row_id, offset: int) -> None:
    with SessionLocal() as db:
        row = db.get(AgentTelegramBot, uuid.UUID(str(row_id)))
        if row is not None and offset > (row.update_offset or 0):
            row.update_offset = offset
            db.commit()


def mark(row_id, state: str, error: str | None = None, username: str | None = None) -> None:
    """The bridge's verdict on a token: running, invalid (401) or conflict (409: polled twice).  ``error`` is
    already redacted."""
    with SessionLocal() as db:
        row = db.get(AgentTelegramBot, uuid.UUID(str(row_id)))
        if row is None:
            return
        if state == "running" and not row.chat_id:
            state = "unbound"
        row.status, row.last_error = state, (error or None) and str(error)[:200]
        if username:
            row.bot_username = str(username)[:64]
        db.commit()
