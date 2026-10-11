from __future__ import annotations

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


def create_agent_chat_access_middleware(public_mode: bool, access_key: str):
    def current(value):
        return value() if callable(value) else value

    async def protect_public_api(request, call_next):
        is_public = current(public_mode)
        configured_key = current(access_key)
        public_chat = request.method == "POST" and request.url.path in AgentChatBodyLimitMiddleware.PATHS
        public_chat_list = request.method == "GET" and request.url.path == "/agent-chat/conversations"
        if request.method == "GET" and request.url.path.startswith("/agent-chat/conversations/") and request.url.path.endswith("/messages"):
            chat_session = request.headers.get("X-Chat-Session", "")
            if not 32 <= len(chat_session) <= 256:
                return JSONResponse(status_code=401, content={"detail": "chat_session_required"}, headers={"Cache-Control": "no-store"})
        public_read = request.method in {"GET", "HEAD"} or request.url.path == "/health" or request.method == "OPTIONS"
        if not is_public:
            response = await call_next(request)
            if public_chat or public_read or request.url.path == "/agent-chat/local-model-profiles":
                response.headers["Cache-Control"] = "no-store"
            return response
        if public_chat or public_chat_list or public_read:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        if not configured_key:
            return JSONResponse(status_code=503, content={"detail": "服务端尚未配置访问口令 / API access key is not configured"}, headers={"Cache-Control": "no-store"})
        supplied_key = request.headers.get("X-App-Access-Key", "")
        if not secrets.compare_digest(supplied_key.encode("utf-8"), configured_key.encode("utf-8")):
            return JSONResponse(status_code=401, content={"detail": "访问口令无效，请重试 / Invalid API access key"}, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return protect_public_api
