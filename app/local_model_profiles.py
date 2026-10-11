"""Loopback-only storage for profiles owned by a local product installation."""
from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import threading
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/agent-chat", tags=["local-model-profiles"])
MAX_BYTES = 128 * 1024
PROVIDERS = {"deepseek", "kimi", "minimax", "qwen", "zhipu", "doubao", "hunyuan", "baidu", "siliconflow", "openrouter", "custom"}
_LOCK = threading.Lock()


def _enabled() -> bool:
    return os.getenv("LOCAL_MODEL_PROFILES_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def _public_mode() -> bool:
    return os.getenv("PUBLIC_API_MODE", "false").strip().lower() in {"1", "true", "yes"}


def _path() -> Path:
    configured = os.getenv("LOCAL_MODEL_PROFILES_PATH", "data/model-profiles.json")
    path = Path(configured)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


def _loopback(host: str | None) -> bool:
    if not host or host.endswith(".") or "%" in host:
        return False
    lowered = host.lower()
    if lowered == "localhost":
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _guard(request: Request) -> None:
    if _public_mode() or not _enabled():
        raise HTTPException(404, "local_profile_storage_unavailable")
    peer = request.client.host if request.client else None
    try:
        if not peer or not ipaddress.ip_address(peer.split("%", 1)[0]).is_loopback:
            raise ValueError
    except ValueError:
        raise HTTPException(403, "local_profile_request_denied") from None
    host = request.headers.get("host", "")
    try:
        authority = urlsplit("//" + host)
        if not _loopback(authority.hostname) or authority.username or authority.password or authority.path or authority.query or authority.fragment or authority.port is not None and not 1 <= authority.port <= 65535:
            raise ValueError
    except ValueError:
        raise HTTPException(403, "local_profile_request_denied") from None
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not _loopback(parsed.hostname) or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except ValueError:
            raise HTTPException(403, "local_profile_request_denied") from None
    if any(name.lower() == "forwarded" or name.lower() == "x-real-ip" or name.lower().startswith("x-forwarded-") for name, _ in request.headers.items()):
        raise HTTPException(403, "local_profile_request_denied")


def _validate(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"version", "profiles", "selected_id"} or type(value.get("version")) is not int or value.get("version") != 1:
        raise ValueError
    profiles = value.get("profiles")
    selected = value.get("selected_id")
    if not isinstance(profiles, list) or len(profiles) > 20 or selected is not None and not isinstance(selected, str):
        raise ValueError
    ids: set[str] = set()
    for p in profiles:
        if not isinstance(p, dict) or set(p) != {"id", "display_name", "provider", "base_url", "model", "api_key", "tested_at"}:
            raise ValueError
        if not all(isinstance(p[k], str) for k in ("id", "display_name", "provider", "base_url", "model", "api_key")):
            raise ValueError
        if not (1 <= len(p["id"].strip()) <= 80 and len(p["id"]) <= 80 and p["id"] not in ids and 1 <= len(p["display_name"].strip()) <= 100 and len(p["display_name"]) <= 100 and p["provider"] in PROVIDERS and 1 <= len(p["base_url"].strip()) <= 2048 and len(p["base_url"]) <= 2048 and re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", p["model"]) is not None and len(p["api_key"]) <= 2048 and (not p["api_key"] or len(p["api_key"]) >= 8)) :
            raise ValueError
        if p["provider"] == "custom" and not p["base_url"].strip():
            raise ValueError
        if p["base_url"]:
            parsed = urlsplit(p["base_url"].strip())
            try:
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port is not None and not 1 <= parsed.port <= 65535 or "%" in parsed.netloc or any(ord(c) < 32 or c == "\\" for c in p["base_url"]):
                    raise ValueError
            except ValueError:
                raise ValueError from None
        if p["tested_at"] is not None:
            if not isinstance(p["tested_at"], str) or len(p["tested_at"]) > 64:
                raise ValueError
            try:
                datetime.fromisoformat(p["tested_at"].replace("Z", "+00:00"))
            except ValueError:
                raise ValueError from None
            if not p["api_key"].strip() or not p["model"].strip():
                raise ValueError
        ids.add(p["id"])
    if selected is not None and selected not in ids:
        raise ValueError
    return value


def _read() -> dict:
    path = _path()
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError
    if not path.exists():
        return {"version": 1, "profiles": [], "selected_id": None}
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise RuntimeError
    return _validate(json.loads(raw))


def _write(value: dict) -> None:
    path = _path()
    parent = path.parent
    if path.is_symlink() or parent.is_symlink():
        raise RuntimeError
    parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".model-profiles-", suffix=".tmp", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        try:
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _locked_read() -> dict:
    with _LOCK:
        return _read()


def _locked_write(value: dict) -> None:
    with _LOCK:
        _write(value)


def _display_path() -> str:
    return "data/model-profiles.json" if os.getenv("LOCAL_MODEL_PROFILES_PATH") is None else "server-configured model profile file"


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": code}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.api_route("/local-model-profiles", methods=["GET", "PUT"])
async def local_model_profiles(request: Request):
    try:
        _guard(request)
    except HTTPException as exc:
        return _error(exc.status_code, str(exc.detail))
    if request.method == "GET":
        try:
            data = await run_in_threadpool(_locked_read)
            return JSONResponse(data, headers={"Cache-Control": "no-store", "Pragma": "no-cache", "X-Model-Profile-Storage": "local-file", "X-Model-Profile-Path": _display_path()})
        except Exception:
            return _error(500, "local_profile_read_failed")
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                return _error(413, "local_profile_too_large")
        try:
            data = _validate(json.loads(body))
        except Exception:
            return _error(422, "local_profile_invalid")
        await run_in_threadpool(_locked_write, data)
        return JSONResponse({"ok": True, "storage": "local-file", "path": _display_path()}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except Exception:
        return _error(500, "local_profile_write_failed")
