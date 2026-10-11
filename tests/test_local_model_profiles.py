import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_model_profiles import router


def document():
    return {"version": 1, "profiles": [{"id": "fake", "display_name": "Fake", "provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key": "fake-test-key-123", "tested_at": None}], "selected_id": "fake"}


def client(tmp_path, **env):
    app = FastAPI()
    app.include_router(router)
    old = {key: os.environ.get(key) for key in ("LOCAL_MODEL_PROFILES_ENABLED", "PUBLIC_API_MODE", "LOCAL_MODEL_PROFILES_PATH", "PERSONAL_CHAT_LOGIN_PATH")}
    os.environ.update({"LOCAL_MODEL_PROFILES_ENABLED": "true", "PUBLIC_API_MODE": "false", "LOCAL_MODEL_PROFILES_PATH": str(tmp_path / "profiles.json"), **env})
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)), old


def restore(old):
    for key, value in old.items():
        if value is None: os.environ.pop(key, None)
        else: os.environ[key] = value


def test_roundtrip_survives_new_store_and_restricts_permissions(tmp_path):
    c, old = client(tmp_path)
    try:
        response = c.put("/agent-chat/local-model-profiles", json=document())
        assert response.status_code == 200
        path = tmp_path / "profiles.json"
        assert path.stat().st_mode & 0o777 == 0o600
        c.close()
        c2 = TestClient(c.app, base_url="http://localhost", client=("127.0.0.1", 50000))
        assert c2.get("/agent-chat/local-model-profiles").json() == document()
        assert c2.get("/agent-chat/local-model-profiles").headers["cache-control"] == "no-store"
        c2.close()
    finally: restore(old)


def test_configured_owner_is_required_for_local_profile_reads_and_writes(tmp_path, monkeypatch):
    import base64, hashlib
    salt, code, credential = b"s" * 32, "generated-test-code", "o" * 72
    login = tmp_path / "generated-login.json"
    login.write_text(json.dumps({"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 100000)).decode(), "iterations": 100000, "chat_credential": credential}))
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH", str(login))
    c, old = client(tmp_path)
    try:
        assert c.get("/agent-chat/local-model-profiles").status_code == 401
        assert c.put("/agent-chat/local-model-profiles", json=document()).status_code == 401
        assert c.get("/agent-chat/local-model-profiles", headers={"X-Chat-Session": credential}).status_code == 200
        assert c.put("/agent-chat/local-model-profiles", json=document(), headers={"X-Chat-Session": credential}).status_code == 200
    finally:
        c.close(); restore(old)


def test_disabled_and_public_mode_deny(tmp_path):
    for env in ({"LOCAL_MODEL_PROFILES_ENABLED": "false"}, {"PUBLIC_API_MODE": "true"}):
        c, old = client(tmp_path, **env)
        try:
            assert c.get("/agent-chat/local-model-profiles").status_code == 404
            assert c.put("/agent-chat/local-model-profiles", json=document()).status_code == 404
        finally: c.close(); restore(old)


def test_rejects_bad_authority_origin_proxy_and_peer(tmp_path):
    c, old = client(tmp_path)
    try:
        for headers in ({"host": "evil.example"}, {"origin": "https://evil.example"}, {"x-forwarded-for": "127.0.0.1"}, {"forwarded": "for=127.0.0.1"}):
            assert c.get("/agent-chat/local-model-profiles", headers=headers).status_code == 403
        remote = TestClient(c.app, base_url="http://localhost", client=("192.0.2.4", 50000))
        assert remote.get("/agent-chat/local-model-profiles").status_code == 403
    finally: c.close(); restore(old)


def test_invalid_input_and_corrupt_file_never_echo_or_overwrite(tmp_path):
    c, old = client(tmp_path)
    try:
        raw = b'{"api_key":"DO_NOT_ECHO-secret"}'
        path = tmp_path / "profiles.json"
        path.write_bytes(raw)
        response = c.get("/agent-chat/local-model-profiles")
        assert response.status_code == 500 and "DO_NOT_ECHO" not in response.text
        assert path.read_bytes() == raw
        response = c.put("/agent-chat/local-model-profiles", json={**document(), "selected_id": "missing"})
        assert response.status_code == 422 and "fake-test-key" not in response.text
        assert path.read_bytes() == raw
    finally: c.close(); restore(old)


def test_oversize_and_atomic_failure_preserve_original(tmp_path, monkeypatch):
    c, old = client(tmp_path)
    try:
        path = tmp_path / "profiles.json"
        path.write_text(json.dumps(document()))
        original = path.read_bytes()
        response = c.put("/agent-chat/local-model-profiles", content=iter([b"x" * (128 * 1024 + 1)]), headers={"content-type": "application/json"})
        assert response.status_code == 413
        import app.local_model_profiles as module
        monkeypatch.setattr(module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("secret path")))
        response = c.put("/agent-chat/local-model-profiles", json=document())
        assert response.status_code == 500 and "secret path" not in response.text
        assert path.read_bytes() == original
        assert not list(tmp_path.glob("*.tmp"))
    finally: c.close(); restore(old)


def test_custom_display_path_header_and_any_valid_loopback_port(tmp_path):
    c, old = client(tmp_path, LOCAL_MODEL_PROFILES_PATH=str(tmp_path / "custom" / "profiles.json"))
    try:
        response = c.get("/agent-chat/local-model-profiles", headers={"host": "[::1]:61234", "origin": "http://[::1]:61234"})
        assert response.status_code == 200
        assert response.headers["x-model-profile-path"] == "server-configured model profile file"
    finally:
        c.close(); restore(old)


def test_oversized_existing_file_is_rejected_without_replacement(tmp_path):
    c, old = client(tmp_path)
    try:
        path = tmp_path / "profiles.json"
        raw = b"x" * (128 * 1024 + 1)
        path.write_bytes(raw)
        response = c.get("/agent-chat/local-model-profiles")
        assert response.status_code == 500
        assert path.stat().st_size == len(raw)
        assert path.read_bytes() == raw
    finally:
        c.close(); restore(old)


def test_symlink_profile_file_is_rejected(tmp_path):
    c, old = client(tmp_path)
    try:
        target = tmp_path / "target.json"
        target.write_text(json.dumps(document()))
        path = tmp_path / "profiles.json"
        path.symlink_to(target)
        response = c.get("/agent-chat/local-model-profiles")
        assert response.status_code == 500
        assert target.read_text() == json.dumps(document())
    finally:
        c.close(); restore(old)


def test_empty_key_cannot_claim_verified_timestamp(tmp_path):
    c, old = client(tmp_path)
    try:
        value = document()
        value["profiles"][0]["api_key"] = ""
        value["profiles"][0]["tested_at"] = "2026-10-11T00:00:00Z"
        response = c.put("/agent-chat/local-model-profiles", json=value)
        assert response.status_code == 422
    finally:
        c.close(); restore(old)


def test_full_string_lengths_are_bounded_even_with_whitespace(tmp_path):
    c, old = client(tmp_path)
    try:
        for field, value in (("id", "x" * 81), ("display_name", "x" * 101), ("base_url", " " * 2049)):
            payload = document()
            payload["profiles"][0][field] = value
            assert c.put("/agent-chat/local-model-profiles", json=payload).status_code == 422
    finally:
        c.close(); restore(old)
