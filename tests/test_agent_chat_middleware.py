import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.agent_chat_api import router, safe_validation_response
from app.agent_chat_middleware import AgentChatBodyLimitMiddleware, create_agent_chat_access_middleware


def test_public_chat_is_no_store_and_body_limited_without_access_key():
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, safe_validation_response)
    app.include_router(router)
    app.add_middleware(AgentChatBodyLimitMiddleware)
    app.middleware("http")(create_agent_chat_access_middleware(True, "synthetic-app-access-key"))
    client = TestClient(app)

    class Pinned(httpx.AsyncBaseTransport):
        def __init__(self, **kwargs): pass
        async def handle_async_request(self, request):
            return httpx.Response(200, json={"data": []})
        async def aclose(self): pass

    from app import agent_chat_api
    old = agent_chat_api.PinnedPublicTransport
    agent_chat_api.PinnedPublicTransport = Pinned
    try:
        response = client.post("/agent-chat/models", json={"provider":"openrouter","base_url":"https://openrouter.ai/api/v1"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "x-app-access-key" not in response.request.headers

        oversized = client.post("/agent-chat/models", content=b"{" + b" " * 32_000)
        assert oversized.status_code == 413
        assert oversized.json()["detail"] == "request_too_large"
        assert oversized.headers["cache-control"] == "no-store"

        invalid = client.post("/agent-chat/models", json={"provider":"openrouter","api_key":"tiny"})
        assert invalid.status_code == 422
        assert "tiny" not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
    finally:
        agent_chat_api.PinnedPublicTransport = old
