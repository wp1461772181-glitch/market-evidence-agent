import pytest

from app import main


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def test_public_mode_allows_reads_but_protects_owner_writes(monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "a" * 40)

    health = client.get("/health")
    origin = main.allowed_origins[0]
    public_read = client.get("/openapi.json", headers={"Origin": origin})
    denied = client.post("/forecast-runs", json={"symbol": "AAPL"}, headers={"Origin": origin})
    denied_price_seed = client.post("/market-data/AMD/initialize", headers={"Origin": origin})
    owner_valid = client.post("/forecast-runs", json={"symbol": "!invalid!"}, headers={"Origin": origin, "X-App-Access-Key": "a" * 40})

    assert health.status_code == 200
    assert public_read.status_code == 200
    assert public_read.headers["cache-control"] == "no-store"
    assert denied.status_code == 401
    assert denied_price_seed.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
    assert denied.headers["access-control-allow-origin"] == origin
    assert denied.headers["vary"] == "Origin"
    assert owner_valid.status_code == 422


def test_public_chat_bypasses_owner_key_but_owner_write_uses_it(client, monkeypatch):
    access_key = "b" * 40
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", access_key)

    from app.agent_chat_api import ChatResponse
    async def fake_call(payload, messages, max_tokens):
        return ChatResponse(content="hello", model=payload.model)
    monkeypatch.setattr("app.agent_chat_api._limited_call", fake_call)
    response = client.post("/agent-chat/hello", json={"provider":"deepseek","model":"deepseek-chat","api_key":"test-key-123","messages":[]})
    denied_write = client.post("/forecast-runs", json={"symbol":"AAPL"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert denied_write.status_code == 401


def test_public_mode_rejects_unicode_and_fails_closed_without_a_key(client, monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "c" * 40)

    unicode_response = client.post("/forecast-runs", json={"symbol":"AAPL"}, headers={"X-App-Access-Key": b"\xff"})
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "")
    missing_key_response = client.post("/forecast-runs", json={"symbol":"AAPL"})

    assert unicode_response.status_code == 401
    assert missing_key_response.status_code == 503
    assert missing_key_response.headers["cache-control"] == "no-store"


def test_public_mode_refuses_to_start_with_a_short_key(monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "too-short")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        main.create_tables()


def test_public_mode_refuses_to_start_with_wildcard_or_missing_cors(monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "d" * 40)
    monkeypatch.setattr(main, "allowed_origins", ["*"])

    with pytest.raises(RuntimeError, match="exact CORS_ALLOWED_ORIGINS"):
        main.create_tables()
