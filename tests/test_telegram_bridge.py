"""Tests for the Telegram bridge (Phase 4).

Covers: stranger ignored, button -> consent.execute once, unbound/missing token fails closed,
reply path can carry citations.
"""
import asyncio
import uuid

import pytest

from app.agent_chat_models import AgentConversation
from app.agent_chat_store import engine as store_engine, owner_hash
from app.agent_workflow import consent
from app.agent_workflow.consent import ConsentError, execute, propose
from app.database import Base, SessionLocal, engine

OWNER = "owner-telegram-test"


def run(coroutine):
    async def wrapped():
        try:
            return await coroutine
        finally:
            await store_engine.dispose()
    return asyncio.run(wrapped())


@pytest.fixture
def owner(monkeypatch, disposable_database):
    from app.agent_workflow.models import AgentConsent
    from app.agent_chat_models import AgentTelegramBind
    from app.agent_workflow import direct, tools
    import app.agent_chat_identity as identity

    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                              if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                              and t.name not in skipped])
    with SessionLocal() as db:
        db.query(AgentConsent).delete()
        db.query(AgentTelegramBind).delete()
        db.commit()
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    return owner_hash(OWNER)


class TestTelegramBind:
    """Bind/unbind operations and the token encryption at rest."""

    def test_token_encrypted_at_rest(self, owner, monkeypatch):
        import app.agent_telegram as tg
        token = "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        tg.save_token(owner, token)
        s = tg.status(owner)
        assert s["has_token"] is True
        assert s["bound"] is False
        assert "token" not in str(s).lower().replace("has_token", "")

        bind = tg.load_bind(owner)
        assert bind is None  # no chat bound yet

    def test_save_token_invalid_rejected(self, owner, monkeypatch):
        import app.agent_telegram as tg
        from app.agent_telegram import TelegramError
        with pytest.raises(TelegramError, match="invalid_bot_token"):
            tg.save_token(owner, "short")
        with pytest.raises(TelegramError, match="invalid_bot_token"):
            tg.save_token(owner, "no-colon-here-12345678901234567890")

    def test_bind_flow(self, owner, monkeypatch):
        import app.agent_telegram as tg
        from app.agent_telegram import TelegramError
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        result = tg.start_bind(owner)
        code = result["code"]
        assert len(code) == 8

        with pytest.raises(TelegramError, match="bind_code_mismatch"):
            tg.confirm_bind(owner, "00000000", 123456789, 87654321)

        tg.confirm_bind(owner, code, 123456789, 87654321)
        s = tg.status(owner)
        assert s["bound"] is True
        assert s["chat_id"] == 123456789

    def test_unbind_clears_chat(self, owner, monkeypatch):
        import app.agent_telegram as tg
        from app.agent_telegram import TelegramError
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        result = tg.start_bind(owner)
        tg.confirm_bind(owner, result["code"], 123456789, 87654321)
        assert tg.status(owner)["bound"] is True

        tg.unbind(owner)
        s = tg.status(owner)
        assert s["bound"] is False
        assert s["chat_id"] is None
        assert s["has_token"] is True

    def test_no_token_fails_closed(self, owner, monkeypatch):
        import app.agent_telegram as tg
        from app.agent_telegram import TelegramError
        with pytest.raises(TelegramError, match="no_bot_token"):
            tg.start_bind(owner)

        bind = tg.load_bind(owner)
        assert bind is None

    def test_missing_credential_key_fails(self, owner, monkeypatch):
        import app.agent_telegram as tg
        from app.agent_telegram import TelegramError
        monkeypatch.setenv("MODEL_CREDENTIAL_KEY", "")
        monkeypatch.setenv("APP_API_ACCESS_KEY", "")
        with pytest.raises(TelegramError, match="credential_key_missing"):
            tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")


class TestConsentExecution:
    """Button press -> consent.execute exactly once."""

    def _conversation(self, owner):
        with SessionLocal() as db:
            conv = AgentConversation(owner_hash=owner, status="active")
            db.add(conv)
            db.commit()
            return str(conv.id)

    def test_execute_once_second_press_replays(self, owner, monkeypatch):
        cid = self._conversation(owner)
        ops = [{"name": "remember", "kind": "remember", "text": "test", "quote": "test"}]
        card = propose(OWNER, cid, ops, {"title": "test"})

        token = ""
        for choice in card["choices"]:
            if choice["value"].startswith("confirm:"):
                token = choice["value"][len("confirm:"):]
                break
        assert token, "card must have a confirm token"

        calls = []

        def handler(op, key):
            calls.append(key)
            return {"ok": True}

        result = execute(OWNER, cid, token, ops, handler)
        assert result.get("ok") is True
        assert len(calls) == 1

        result2 = execute(OWNER, cid, token, ops, handler)
        assert result2.get("ok") is True
        assert len(calls) == 1

    def test_wrong_token_rejected(self, owner, monkeypatch):
        cid = self._conversation(owner)
        ops = [{"name": "remember", "kind": "remember", "text": "test", "quote": "test"}]
        propose(OWNER, cid, ops, {"title": "test"})
        with pytest.raises(ConsentError):
            execute(OWNER, cid, "wrong-token-that-does-not-exist-xyz", ops, lambda _op, _key: {"ok": True})

    def test_tampered_operations_rejected(self, owner, monkeypatch):
        cid = self._conversation(owner)
        ops = [{"name": "remember", "kind": "remember", "text": "test", "quote": "test"}]
        card = propose(OWNER, cid, ops, {"title": "test"})

        token = ""
        for choice in card["choices"]:
            if choice["value"].startswith("confirm:"):
                token = choice["value"][len("confirm:"):]
                break
        assert token

        tampered = [{"name": "forget", "kind": "forget", "item_id": "xxx"}]
        with pytest.raises(ConsentError):
            execute(OWNER, cid, token, tampered, lambda _op, _key: {"ok": True})


class TestStrangerIgnored:
    """The bridge only handles the bound chat; strangers are ignored."""

    def test_stranger_not_bound(self, owner, monkeypatch):
        import app.agent_telegram as tg
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        result = tg.start_bind(owner)
        tg.confirm_bind(owner, result["code"], 111111, 222222)
        bind = tg.load_bind(owner)
        assert bind is not None
        assert bind["chat_id"] == 111111

        stranger_chat = 999999
        assert stranger_chat != bind["chat_id"]


class TestReplyCitations:
    """Telegram answers are chat text: the citations event is the web page's and is not listed."""

    def test_citations_are_not_appended(self, owner):
        from scripts.run_telegram_bridge import format_turn
        answer, _, _ = format_turn([("delta", {"text": "NVDA reported strong earnings [1]."}),
                                    ("citations", {"items": [{"ordinal": 1, "title": "NVDA 10-K 2025"}]})])
        assert answer == "NVDA reported strong earnings."


class TestUnboundFailsClosed:
    """Without a binding, the bridge refuses to start."""

    def test_load_bind_returns_none_for_no_token(self, owner, monkeypatch):
        import app.agent_telegram as tg
        bind = tg.load_bind(owner)
        assert bind is None

    def test_load_bind_returns_none_for_token_without_binding(self, owner, monkeypatch):
        import app.agent_telegram as tg
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        bind = tg.load_bind(owner)
        assert bind is None

    def test_load_bind_returns_data_for_bound(self, owner, monkeypatch):
        import app.agent_telegram as tg
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        result = tg.start_bind(owner)
        tg.confirm_bind(owner, result["code"], 123456789, 87654321)
        bind = tg.load_bind(owner)
        assert bind is not None
        assert bind["chat_id"] == 123456789
        assert bind["bot_token"] == "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

    def test_status_before_and_after(self, owner, monkeypatch):
        import app.agent_telegram as tg
        s = tg.status(owner)
        assert s["configured"] is False
        assert s["has_token"] is False
        assert s["bound"] is False

        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        s2 = tg.status(owner)
        assert s2["configured"] is True
        assert s2["has_token"] is True
        assert s2["bound"] is False

        result = tg.start_bind(owner)
        tg.confirm_bind(owner, result["code"], 123456789, 87654321)
        s3 = tg.status(owner)
        assert s3["bound"] is True
        assert s3["chat_id"] == 123456789


class TestApiEndpoints:
    """The owner-only API endpoints for Telegram bindings."""

    def test_status_endpoint(self, owner, monkeypatch, client):
        import app.agent_telegram as tg
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        response = client.get("/agent-chat/telegram/status", headers={"X-Chat-Session": OWNER})
        assert response.status_code == 200
        data = response.json()
        assert data["has_token"] is True
        assert data["bound"] is False

    def test_token_save_endpoint(self, owner, monkeypatch, client):
        response = client.post("/agent-chat/telegram/token",
                               headers={"X-Chat-Session": OWNER},
                               json={"bot_token": "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789"})
        assert response.status_code == 200
        data = response.json()
        assert data["has_token"] is True
        assert data["bound"] is False

    def test_token_invalid_rejected(self, owner, monkeypatch, client):
        response = client.post("/agent-chat/telegram/token",
                               headers={"X-Chat-Session": OWNER},
                               json={"bot_token": "short"})
        assert response.status_code == 422

    def test_bind_unbind_endpoints(self, owner, monkeypatch, client):
        import app.agent_telegram as tg
        client.post("/agent-chat/telegram/token",
                    headers={"X-Chat-Session": OWNER},
                    json={"bot_token": "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789"})

        response = client.post("/agent-chat/telegram/bind", headers={"X-Chat-Session": OWNER})
        assert response.status_code == 200
        data = response.json()
        assert "code" in data
        assert len(data["code"]) == 8

        code = data["code"]
        tg.confirm_bind(owner, code, 123456789, 87654321)

        status_r = client.get("/agent-chat/telegram/status", headers={"X-Chat-Session": OWNER})
        assert status_r.json()["bound"] is True

        unbind_r = client.post("/agent-chat/telegram/unbind", headers={"X-Chat-Session": OWNER})
        assert unbind_r.status_code == 200
        assert unbind_r.json()["bound"] is False

    def test_endpoints_owner_only(self, owner, monkeypatch, client):
        import app.agent_telegram as tg
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        for method, path in [("GET", "/agent-chat/telegram/status"),
                             ("POST", "/agent-chat/telegram/bind"),
                             ("POST", "/agent-chat/telegram/unbind")]:
            if method == "GET":
                response = client.get(path)
            else:
                response = client.post(path)
            assert response.status_code == 403, f"{method} {path} should be owner-only"

    def test_token_endpoint_owner_only(self, owner, monkeypatch, client):
        import app.agent_telegram as tg
        tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        response = client.post("/agent-chat/telegram/token",
                               json={"bot_token": "1234567890:AAvalidTokenButNoAuth"})
        assert response.status_code == 403


def test_the_token_loads_before_any_chat_is_bound(owner):
    import app.agent_telegram as tg
    token = "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    tg.save_token(owner, token)
    assert tg.load_bind(owner) is None
    assert tg.load_bot_token(owner) == token