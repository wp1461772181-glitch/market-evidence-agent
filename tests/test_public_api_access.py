import base64
import hashlib
import json

import pytest

from app import main


def login_config(tmp_path, monkeypatch):
    salt = b"fixture-salt" * 3
    code = "generated-fixture-code"
    credential = "o" * 72
    path = tmp_path / "personal-login.json"
    path.write_text(json.dumps({
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 100000)).decode(),
        "iterations": 100000,
        "chat_credential": credential,
    }))
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH", str(path))
    return code, credential


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def test_public_mode_allows_reads_and_configured_personal_owner_writes(client, monkeypatch, tmp_path):
    _, owner = login_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "")
    origin = "https://example.test"
    health = client.get("/health")
    public_read = client.get("/openapi.json")
    denied = client.post("/forecast-runs", json={"symbol": "AAPL"})
    random = client.post("/forecast-runs", json={"symbol": "AAPL"}, headers={"X-Chat-Session": "r" * 72})
    valid = client.post("/forecast-runs", json={"symbol": "!invalid!"}, headers={"X-Chat-Session": owner})
    assert health.status_code == 200
    assert public_read.status_code == 200 and public_read.headers["cache-control"] == "no-store"
    assert denied.status_code == random.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
    assert valid.status_code == 422
    assert owner not in valid.text


def test_public_mode_fails_closed_without_valid_login_config(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "legacy-key")
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH", str(tmp_path / "missing.json"))
    missing = client.post("/forecast-runs", json={"symbol": "AAPL"}, headers={"X-App-Access-Key": "legacy-key"})
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text("{}")
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH", str(malformed_path))
    malformed = client.post("/forecast-runs", json={"symbol": "AAPL"})
    assert missing.status_code == malformed.status_code == 503
    assert "legacy-key" not in missing.text
    assert "personal-login" not in malformed.text


def test_local_mode_requires_personal_owner_when_login_configured(client, monkeypatch, tmp_path):
    _, owner = login_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "PUBLIC_API_MODE", False)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "")
    guest = client.post("/forecast-runs", json={"symbol": "AAPL"}, headers={"X-Chat-Session": "g" * 72})
    accepted = client.post("/forecast-runs", json={"symbol": "!invalid!"}, headers={"X-Chat-Session": owner})
    assert guest.status_code == 401
    assert accepted.status_code == 422


def test_local_mode_without_login_config_keeps_historical_internal_behavior(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", False)
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH", str(tmp_path / "missing.json"))
    response = client.post("/forecast-runs", json={"symbol": "!invalid!"})
    assert response.status_code == 422


def test_public_startup_requires_owner_login_config_and_exact_cors(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "")
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="personal chat login configuration"):
        main.create_tables()
    login_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "allowed_origins", ["*"])
    with pytest.raises(RuntimeError, match="exact CORS_ALLOWED_ORIGINS"):
        main.create_tables()


def test_personal_login_startup_allows_empty_legacy_access_key_on_disposable_database(monkeypatch, tmp_path):
    login_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "")
    monkeypatch.setattr(main, "allowed_origins", ["https://example.test"])
    main.create_tables()
