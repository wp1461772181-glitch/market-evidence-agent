"""Personal chat login and owner-scoped conversation listing."""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent_chat_store import list_conversations, set_conversation_title

router = APIRouter(prefix="/agent-chat", tags=["agent-chat-identity"])
_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()
_WINDOW, _MAX_ATTEMPTS = 900, 8

class LoginBody(BaseModel):
    code: str = Field(min_length=1, max_length=256)

def _decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)

def _allowed(ip: str) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        times = [at for at in _attempts.get(ip, []) if now - at < _WINDOW]
        if len(times) >= _MAX_ATTEMPTS:
            _attempts[ip] = times
            return False
        times.append(now)
        _attempts[ip] = times
        return True

def _load_config():
    path = Path(os.environ.get("PERSONAL_CHAT_LOGIN_PATH", Path(__file__).resolve().parent.parent / "data" / "personal-chat-login.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        salt, expected = _decode(data["salt"]), _decode(data["hash"])
        iterations = data["iterations"]
        credential = data["chat_credential"]
        if not isinstance(iterations, int) or iterations < 100_000 or len(expected) != 32 or not isinstance(credential, str) or len(credential) < 64:
            raise ValueError("invalid config")
        return salt, expected, iterations, credential
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

def owner_credential_matches(value: str | None) -> bool:
    """Return whether a session header is the configured site-owner credential."""
    config = _load_config()
    if config is None or not isinstance(value, str) or not 64 <= len(value) <= 256:
        return False
    try:
        return hmac.compare_digest(value.encode("ascii"), config[3].encode("ascii"))
    except UnicodeEncodeError:
        return False

@router.get("/session-status")
async def session_status(x_chat_session: str | None = Header(default=None)):
    return {"owner": owner_credential_matches(x_chat_session)}

@router.post("/personal-login")
async def personal_login(body: LoginBody, request: Request):
    if not _allowed(request.client.host if request.client else "unknown"):
        return JSONResponse(status_code=429, content={"detail": "login_unavailable"}, headers={"Cache-Control": "no-store"})
    config = _load_config()
    if config is None:
        return JSONResponse(status_code=503, content={"detail": "login_unavailable"}, headers={"Cache-Control": "no-store"})
    salt, expected, iterations, credential = config
    actual = hashlib.pbkdf2_hmac("sha256", body.code.encode("utf-8"), salt, iterations, dklen=32)
    if not hmac.compare_digest(actual, expected):
        return JSONResponse(status_code=401, content={"detail": "login_unavailable"}, headers={"Cache-Control": "no-store"})
    return JSONResponse({"credential": credential}, headers={"Cache-Control": "no-store"})

@router.get("/conversations")
async def conversations(x_chat_session: str | None = Header(default=None), limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0, le=10000)):
    if not x_chat_session or not 32 <= len(x_chat_session) <= 256:
        raise HTTPException(401, "chat_session_required", headers={"Cache-Control": "no-store"})
    rows = await list_conversations(x_chat_session, limit + 1, offset)
    return {"conversations": rows[:limit], "has_more": len(rows) > limit}


class RenameBody(BaseModel):
    title: str = Field(max_length=400)

@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str, body: RenameBody, x_chat_session: str | None = Header(default=None)):
    """Give one of this owner's conversations a name (an empty name restores the default)."""
    if not x_chat_session or not 32 <= len(x_chat_session) <= 256:
        raise HTTPException(401, "chat_session_required", headers={"Cache-Control": "no-store"})
    if any(ord(char) < 32 or ord(char) == 127 for char in body.title):
        raise HTTPException(422, "invalid_conversation_title", headers={"Cache-Control": "no-store"})
    title = " ".join(body.title.split())
    if len(title) > 80:
        raise HTTPException(422, "conversation_title_too_long", headers={"Cache-Control": "no-store"})
    if not await set_conversation_title(x_chat_session, conversation_id, title or None):
        raise HTTPException(404, "conversation_not_found", headers={"Cache-Control": "no-store"})
    return JSONResponse({"id": conversation_id, "title": title or None}, headers={"Cache-Control": "no-store"})
