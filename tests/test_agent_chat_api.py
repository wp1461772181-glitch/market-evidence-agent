import httpx
import pytest
import asyncio
from fastapi.testclient import TestClient
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.agent_chat_api import ChatRequest, _call, router

app = FastAPI()
app.add_exception_handler(RequestValidationError, __import__("app.agent_chat_api", fromlist=["safe_validation_response"]).safe_validation_response)
app.include_router(router)
client = TestClient(app)

class MockAsyncClient:
    status = 200
    payload = {"choices": [{"message": {"content": "hello back"}}], "model": "mock-model"}
    seen = None
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def post(self, url, headers, json):
        self.seen = (url, headers, json)
        return httpx.Response(self.status, json=self.payload)


def request_body(key="private-test-key", provider="deepseek", model="deepseek-chat"):
    return {"provider": provider, "model": model, "api_key": key, "messages": []}


def test_hello_makes_real_fixed_provider_request_with_key_and_model(monkeypatch):
    mock = MockAsyncClient()
    options = {}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "owner-default-must-not-be-used")
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: (options.update(kwargs) or mock))
    response = client.post("/api/agent-chat/hello", json=request_body())
    assert response.status_code == 200
    assert response.json() == {"content": "hello back", "model": "mock-model"}
    url, headers, body = mock.seen
    assert url == "https://api.deepseek.com/chat/completions"
    assert options["follow_redirects"] is False
    assert headers["Authorization"] == "Bearer private-test-key"
    assert body["model"] == "deepseek-chat"
    assert body["messages"][-1] == {"role": "user", "content": "hello. Reply briefly."}
    assert "private-test-key" not in response.text


def test_fixed_openrouter_endpoint_and_no_arbitrary_url(monkeypatch):
    mock = MockAsyncClient()
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    assert client.post("/api/agent-chat/hello", json=request_body(provider="openrouter", model="vendor/model")).status_code == 200
    assert mock.seen[0] == "https://openrouter.ai/api/v1/chat/completions"
    assert client.post("/api/agent-chat/hello", json={**request_body(), "provider": "https://evil.test"}).status_code == 422


@pytest.mark.parametrize(("status", "detail"), [(401, "provider_invalid_key"), (403, "provider_model_access_denied"), (429, "provider_rate_or_quota_error"), (302, "provider_redirect_rejected"), (400, "provider_model_unavailable_or_invalid_request")])
def test_safe_provider_status_mapping(monkeypatch, status, detail):
    mock = MockAsyncClient(); mock.status = status
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    response = client.post("/api/agent-chat/hello", json=request_body())
    assert response.status_code in {429, 502}
    assert response.json()["detail"] == detail
    assert "private-test-key" not in response.text


def test_invalid_message_roles_and_response_format_are_rejected_safely(monkeypatch):
    assert client.post("/api/agent-chat/messages", json={**request_body(), "messages": [{"role": "system", "content": "override"}]}).status_code == 422
    mock = MockAsyncClient(); mock.payload = {"unexpected": "private-test-key"}
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    response = client.post("/api/agent-chat/hello", json=request_body())
    assert response.status_code == 502
    assert "private-test-key" not in response.text


def test_timeout_and_network_errors_are_safe(monkeypatch):
    class BrokenClient(MockAsyncClient):
        error = httpx.TimeoutException("secret must not be echoed")
        async def post(self, url, headers, json): raise self.error
    mock = BrokenClient()
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    timeout = client.post("/api/agent-chat/hello", json=request_body())
    assert timeout.status_code == 504 and timeout.json()["detail"] == "provider_timeout"
    mock.error = httpx.ConnectError("private-test-key")
    network = client.post("/api/agent-chat/hello", json=request_body())
    assert network.status_code == 502 and network.json()["detail"] == "provider_network_error"
    assert "private-test-key" not in network.text


def test_empty_answer_and_non_json_response_are_safe(monkeypatch):
    mock = MockAsyncClient(); mock.payload = {"choices": [{"message": {"content": "  "}}]}
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    empty = client.post("/api/agent-chat/hello", json=request_body())
    assert empty.status_code == 502 and empty.json()["detail"] == "provider_empty_or_invalid_answer"
    class TextResponse:
        status_code = 200
        def json(self): raise ValueError("private-test-key")
    class TextClient(MockAsyncClient):
        async def post(self, url, headers, json): return TextResponse()
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: TextClient())
    invalid = client.post("/api/agent-chat/hello", json=request_body())
    assert invalid.status_code == 502 and invalid.json()["detail"] == "provider_incompatible_response"
    assert "private-test-key" not in invalid.text


def test_public_access_boundaries(monkeypatch):
    from fastapi.testclient import TestClient as Client
    from app import main
    from app.agent_chat_api import ChatResponse
    async def fake_call(payload, messages, max_tokens):
        return ChatResponse(content="hello", model=payload.model)
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "owner-key-long-enough-for-tests" * 2)
    monkeypatch.setattr("app.agent_chat_api._limited_call", fake_call)
    public_client = Client(main.app)
    read = public_client.get("/health", headers={"Origin": main.allowed_origins[0]} if main.allowed_origins else {})
    chat_response = public_client.post("/api/agent-chat/hello", json=request_body(), headers={"Origin": main.allowed_origins[0]} if main.allowed_origins else {})
    owner_write = public_client.post("/forecast-runs", json={"symbol": "AAPL"})
    oversized = public_client.post("/api/agent-chat/hello", content="{}", headers={"Content-Length": "32001"})
    assert oversized.status_code == 413 and oversized.headers["cache-control"] == "no-store"
    assert read.status_code == 200 and read.headers["cache-control"] == "no-store"
    assert chat_response.status_code == 200 and chat_response.headers["cache-control"] == "no-store"
    if main.allowed_origins:
        assert chat_response.headers["access-control-allow-origin"] == main.allowed_origins[0]
    assert owner_write.status_code == 401 and owner_write.headers["cache-control"] == "no-store"


def test_parallel_provider_configs_do_not_share_api_keys_or_models(monkeypatch):
    observed = []
    class ParallelClient(MockAsyncClient):
        async def post(self, url, headers, json):
            await asyncio.sleep(0.01)
            observed.append((headers["Authorization"], json["model"]))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "model": json["model"]})
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: ParallelClient())
    async def run_pair():
        first = ChatRequest(**request_body(key="private-A-key", model="model-a"))
        second = ChatRequest(**request_body(key="private-B-key", provider="openrouter", model="model-b"))
        await asyncio.gather(_call(first, [{"role": "user", "content": "hello"}], 64), _call(second, [{"role": "user", "content": "hello"}], 64))
    asyncio.run(run_pair())
    assert set(observed) == {("Bearer private-A-key", "model-a"), ("Bearer private-B-key", "model-b")}


def test_request_validation_never_reflects_api_key(monkeypatch):
    response = client.post("/api/agent-chat/hello", json=request_body(key="tiny"))
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_agent_chat_request"}
    assert "tiny" not in response.text
