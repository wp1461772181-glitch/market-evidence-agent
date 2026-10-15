from __future__ import annotations

import re
import secrets

from fastapi.responses import JSONResponse


class AgentChatBodyLimitMiddleware:
    """Enforce a byte cap while receiving agent-chat bodies."""
    LIMIT = 32_000
    PATHS = {"/agent-chat/hello", "/agent-chat/messages", "/agent-chat/stream", "/agent-chat/models", "/agent-chat/personal-login"}

    def __init__(self, app, allowed_origins=()):
        self.app = app
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") not in self.PATHS:
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared:
            try:
                if int(declared) > self.LIMIT:
                    await self._reject(scope, send, 413, "request_too_large")
                    return
            except ValueError:
                await self._reject(scope, send, 400, "invalid_content_length")
                return
        chunks, total, more = [], 0, True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.LIMIT:
                await self._reject(scope, send, 413, "request_too_large")
                return
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, scope, send, status_code, detail):
        body = (f'{{"detail":"{detail}"}}').encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]
        origin = dict(scope.get("headers", [])).get(b"origin")
        if origin and origin.decode("latin-1").rstrip("/") in self.allowed_origins:
            headers.extend([(b"access-control-allow-origin", origin), (b"vary", b"Origin")])
        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def create_agent_chat_access_middleware(public_mode: bool, access_key: str, owner_configured=None):
    def current(value):
        return value() if callable(value) else value

    async def protect_public_api(request, call_next):
        is_public = current(public_mode)
        configured_key = current(access_key)
        from .agent_chat_identity import _load_config, owner_credential_matches
        login_config = _load_config()
        owner_configured_now = current(owner_configured) if owner_configured is not None else login_config is not None
        public_chat = request.method == "POST" and request.url.path in AgentChatBodyLimitMiddleware.PATHS
        public_chat_list = request.method == "GET" and request.url.path == "/agent-chat/conversations"
        # Naming a conversation is the caller's own business: the handler only touches conversations
        # of the credential sent (a guest's included), so it is not an owner-only write.
        public_chat_rename = (request.method == "PATCH" and bool(
            re.fullmatch(r"/agent-chat/conversations/[0-9a-fA-F-]{36}", request.url.path)))
        if request.method == "GET" and request.url.path.startswith("/agent-chat/conversations/") and request.url.path.endswith(("/messages", "/context")):
            chat_session = request.headers.get("X-Chat-Session", "")
            if not 32 <= len(chat_session) <= 256:
                return JSONResponse(status_code=401, content={"detail": "chat_session_required"}, headers={"Cache-Control": "no-store"})
        public_read = request.method in {"GET", "HEAD"} or request.url.path == "/health" or request.method == "OPTIONS"
        if not is_public and not owner_configured_now:
            response = await call_next(request)
            if public_chat or public_read or request.url.path == "/agent-chat/local-model-profiles":
                response.headers["Cache-Control"] = "no-store"
            return response
        if public_chat or public_chat_list or public_chat_rename or public_read:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        if is_public and login_config is None:
            return JSONResponse(status_code=503, content={"detail": "owner_login_unavailable"}, headers={"Cache-Control": "no-store"})
        supplied_key = request.headers.get("X-App-Access-Key", "")
        session = request.headers.get("X-Chat-Session", "")
        legacy_ok = bool(configured_key and supplied_key and secrets.compare_digest(supplied_key.encode("utf-8"), configured_key.encode("utf-8")))
        if not owner_credential_matches(session) and not legacy_ok:
            status = 503 if is_public and not login_config and not configured_key else 401
            return JSONResponse(status_code=status, content={"detail": "owner_login_required"}, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return protect_public_api
