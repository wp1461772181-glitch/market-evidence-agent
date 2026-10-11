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

from .agent_chat_store import list_conversations

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
