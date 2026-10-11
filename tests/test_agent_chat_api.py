import httpx
import pytest
import asyncio
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse
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
    response = client.post("/agent-chat/hello", json=request_body())
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
    assert client.post("/agent-chat/hello", json=request_body(provider="openrouter", model="vendor/model")).status_code == 200
    assert mock.seen[0] == "https://openrouter.ai/api/v1/chat/completions"
    assert client.post("/agent-chat/hello", json={**request_body(), "provider": "https://evil.test"}).status_code == 422


@pytest.mark.parametrize(("status", "detail"), [(401, "provider_invalid_key"), (403, "provider_model_access_denied"), (429, "provider_rate_or_quota_error"), (302, "provider_redirect_rejected"), (400, "provider_model_unavailable_or_invalid_request")])
def test_safe_provider_status_mapping(monkeypatch, status, detail):
    mock = MockAsyncClient(); mock.status = status
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    response = client.post("/agent-chat/hello", json=request_body())
    assert response.status_code in {429, 502}
    assert response.json()["detail"] == detail
    assert "private-test-key" not in response.text


def test_invalid_message_roles_and_response_format_are_rejected_safely(monkeypatch):
    assert client.post("/agent-chat/messages", json={**request_body(), "messages": [{"role": "system", "content": "override"}]}).status_code == 422
    mock = MockAsyncClient(); mock.payload = {"unexpected": "private-test-key"}
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    response = client.post("/agent-chat/hello", json=request_body())
    assert response.status_code == 502
    assert "private-test-key" not in response.text


def test_timeout_and_network_errors_are_safe(monkeypatch):
    class BrokenClient(MockAsyncClient):
        error = httpx.TimeoutException("secret must not be echoed")
        async def post(self, url, headers, json): raise self.error
    mock = BrokenClient()
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    timeout = client.post("/agent-chat/hello", json=request_body())
    assert timeout.status_code == 504 and timeout.json()["detail"] == "provider_timeout"
    mock.error = httpx.ConnectError("private-test-key")
    network = client.post("/agent-chat/hello", json=request_body())
    assert network.status_code == 502 and network.json()["detail"] == "provider_network_error"
    assert "private-test-key" not in network.text


def test_empty_answer_and_non_json_response_are_safe(monkeypatch):
    mock = MockAsyncClient(); mock.payload = {"choices": [{"message": {"content": "  "}}]}
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    empty = client.post("/agent-chat/hello", json=request_body())
    assert empty.status_code == 502 and empty.json()["detail"] == "provider_empty_or_invalid_answer"
    class TextResponse:
        status_code = 200
        def json(self): raise ValueError("private-test-key")
    class TextClient(MockAsyncClient):
        async def post(self, url, headers, json): return TextResponse()
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: TextClient())
    invalid = client.post("/agent-chat/hello", json=request_body())
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
    chat_response = public_client.post("/agent-chat/hello", json=request_body(), headers={"Origin": main.allowed_origins[0]} if main.allowed_origins else {})
    owner_write = public_client.post("/forecast-runs", json={"symbol": "AAPL"})
    oversized = public_client.post("/agent-chat/hello", content="{}", headers={"Content-Length": "32001"})
    assert oversized.status_code == 413 and oversized.headers["cache-control"] == "no-store"
    assert read.status_code == 200 and read.headers["cache-control"] == "no-store"
    assert chat_response.status_code == 200 and chat_response.headers["cache-control"] == "no-store"
    if main.allowed_origins:
        assert chat_response.headers["access-control-allow-origin"] == main.allowed_origins[0]
    assert owner_write.status_code == 503 and owner_write.headers["cache-control"] == "no-store"


def test_shared_frontend_url_helper_reaches_public_chat_transport(monkeypatch):
    from app import main
    from app.agent_chat_api import ChatResponse

    helper = Path(__file__).parents[1] / "frontend/src/agent-chat-url.mjs"
    script = """
      const { agentChatUrl, rewriteAgentChatProxyPath } = await import(process.argv[1]);
      console.log(JSON.stringify(['hello', 'messages'].flatMap(action => [
        rewriteAgentChatProxyPath(agentChatUrl(action)),
        new URL(agentChatUrl(action, 'https://api.example.test/')).pathname,
      ])));
    """
    paths = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script, helper.as_uri()], text=True))
    assert paths == ["/agent-chat/hello", "/agent-chat/hello", "/agent-chat/messages", "/agent-chat/messages"]

    observed = []
    async def fake_call(payload, messages, max_tokens):
        observed.append((payload.model, messages[-1]["content"]))
        return ChatResponse(content="transport reached", model=payload.model)

    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "owner-key-long-enough-for-tests" * 2)
    monkeypatch.setattr("app.agent_chat_api._limited_call", fake_call)
    public = TestClient(main.app)
    for path in paths:
        body = request_body()
        if path.endswith("/messages"):
            body["messages"] = [{"role": "user", "content": "user message"}]
        response = public.post(path, json=body, headers={"Origin": main.allowed_origins[0]} if main.allowed_origins else {})
        assert response.status_code == 200
        assert response.json()["content"] == "transport reached"
        assert response.headers["cache-control"] == "no-store"
    assert observed == [("deepseek-chat", "hello. Reply briefly."), ("deepseek-chat", "hello. Reply briefly."), ("deepseek-chat", "user message"), ("deepseek-chat", "user message")]


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
    response = client.post("/agent-chat/hello", json=request_body(key="tiny"))
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_agent_chat_request"}
    assert "tiny" not in response.text
    malformed = client.post("/agent-chat/hello", content='{"api_key":"private-raw-body', headers={"Content-Type": "application/json"})
    assert malformed.status_code == 422
    assert malformed.json() == {"detail": "invalid_agent_chat_request"}
    assert "private-raw-body" not in malformed.text


def test_openrouter_never_sends_test_referer(monkeypatch):
    mock = MockAsyncClient()
    monkeypatch.setattr("app.agent_chat_api.httpx.AsyncClient", lambda **kwargs: mock)
    assert client.post("/agent-chat/hello", json=request_body(provider="openrouter", model="vendor/model")).status_code == 200
    assert "HTTP-Referer" not in mock.seen[1]


def test_actual_body_limit_without_content_length_and_multibyte_body():
    from app.main import AgentChatBodyLimitMiddleware
    async def invoke(body):
        sent = []
        chunks = [body[:19000], body[19000:]]
        async def receive():
            chunk = chunks.pop(0) if chunks else b""
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        async def app(scope, receive, send):
            data = await receive()
            sent.append(("app", data["body"]))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})
        async def send(message): sent.append(message)
        scope = {"type": "http", "method": "POST", "path": "/agent-chat/hello", "headers": [(b"origin", b"https://pages.github.io")]}
        await AgentChatBodyLimitMiddleware(app)(scope, receive, send)
        return sent
    oversized = asyncio.run(invoke(b"x" * 32001))
    assert next(item["status"] for item in oversized if isinstance(item, dict) and item["type"] == "http.response.start") == 413
    assert next(item for item in oversized if isinstance(item, dict) and item["type"] == "http.response.start")["headers"]


def test_actual_app_chat_body_boundary_and_unicode(monkeypatch):
    from app import main
    from app.agent_chat_api import ChatResponse
    async def fake_call(payload, messages, max_tokens):
        return ChatResponse(content="ok", model=payload.model)
    monkeypatch.setattr("app.agent_chat_api._limited_call", fake_call)
    public = TestClient(main.app)
    normal = public.post("/agent-chat/messages", json={**request_body(), "messages": [{"role": "user", "content": "你好，市场"}]})
    oversized = public.post("/agent-chat/hello", content=b"x" * 32001, headers={"Origin": main.allowed_origins[0] if main.allowed_origins else ""})
    assert normal.status_code == 200
    assert oversized.status_code == 413 and oversized.headers["cache-control"] == "no-store"
    if main.allowed_origins:
        assert oversized.headers["access-control-allow-origin"] == main.allowed_origins[0]
def test_ch34_simulated_hello_is_loopback_test_database_and_fake_profile_only(client,monkeypatch):
    monkeypatch.setenv('CH34_ENABLE_SIMULATED_PROVIDER','true')
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://test:test@127.0.0.1:55432/test_market_rag_visual')
    payload={'provider':'custom','base_url':'https://provider.invalid/v1','model':'ch34-simulated','api_key':'fake-test-key-not-a-credential','messages':[{'role':'user','content':'hello'}]}
    with TestClient(app,client=('127.0.0.1',1234)) as loopback_client:
        response=loopback_client.post('/agent-chat/hello',headers={'Host':'127.0.0.1:8018'},json=payload)
    assert response.status_code==200 and response.json()['content'].startswith('SIMULATED local demo provider:')
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://test:test@127.0.0.1:55432/market_evidence')
    from app.agent_chat_api import _ch34_simulated_hello
    from starlette.requests import Request
    request=Request({'type':'http','method':'POST','path':'/agent-chat/hello','headers':[(b'host',b'127.0.0.1:8018')],'client':('127.0.0.1',1234)})
    from app.agent_chat_api import ChatRequest
    assert _ch34_simulated_hello(request,ChatRequest.model_validate(payload)) is None
