"""The owner's Telegram bridge binding (plan §2.4).

The bot token is encrypted with secret_box and never returned by any endpoint.  A one-time bind code,
stored as a hash, lets the owner prove they control a Telegram account; once confirmed, the bridge
only handles messages from that chat.  Strangers are ignored.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from .agent_chat_models import AgentTelegramBind
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
        db.commit()
    return {"code": code, "bot_username": status(owner_hash).get("bot_username")}


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