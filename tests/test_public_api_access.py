import pytest

from app import main


def test_public_mode_keeps_health_open_and_requires_the_access_key(client, monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "a" * 40)

    health = client.get("/health")
    origin = main.allowed_origins[0]
    denied = client.get("/openapi.json", headers={"Origin": origin})

    assert health.status_code == 200
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
    assert denied.headers["access-control-allow-origin"] == origin
    assert denied.headers["vary"] == "Origin"


def test_public_mode_accepts_the_access_key_and_disables_caching(client, monkeypatch):
    access_key = "b" * 40
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", access_key)

    response = client.get("/openapi.json", headers={"X-App-Access-Key": access_key})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_public_mode_rejects_unicode_and_fails_closed_without_a_key(client, monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_API_MODE", True)
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "c" * 40)

    unicode_response = client.get("/openapi.json", headers={"X-App-Access-Key": b"\xff"})
    monkeypatch.setattr(main, "APP_API_ACCESS_KEY", "")
    missing_key_response = client.get("/openapi.json")

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
