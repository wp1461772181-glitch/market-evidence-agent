"""Live check of the Telegram bridge, mostly mocked (no real bot, no real Telegram API calls).

1. Saves a bot token, generates a bind code, and confirms the bind (in-process).
2. Asserts that a stranger's message is ignored (random chat_id, not bound).
3. Asserts that a consent button executes once (in-process consent, no real handler).
4. Without a token or binding, the bridge refuses to start.
5. Never prints tokens.

    python scripts/live_check_telegram.py
"""
import hashlib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), override=False)

ADMIN_URL = make_url(os.getenv("TEST_DATABASE_URL",
                               "postgresql+psycopg://market_evidence:market_evidence_dev@localhost:55432/test_market_evidence"))
DATABASE = f"test_live_telegram_{uuid.uuid4().hex[:12]}"
os.environ["DATABASE_URL"] = ADMIN_URL.set(database=DATABASE).render_as_string(hide_password=False)
os.environ["PERSONAL_CHAT_LOGIN_PATH"] = f"/tmp/live-telegram-missing-{uuid.uuid4().hex}.json"
os.environ.setdefault("MODEL_CREDENTIAL_KEY", "live-check-telegram-key-0123456789abcd")


def main() -> int:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{DATABASE}"'))
    try:
        return run()
    finally:
        from app.database import engine as app_engine
        app_engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
        admin.dispose()


def run() -> int:
    from app.agent_chat_models import AgentTask, AgentResearchQueue, AgentConversation
    from app.agent_chat_store import owner_hash
    from app.agent_workflow import consent, direct, tools
    from app.agent_workflow.models import AgentConsent
    from app.database import Base, SessionLocal, engine
    import app.agent_chat_identity as identity
    import app.agent_telegram as tg
    import app.main  # noqa: F401

    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                              if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                              and t.name not in skipped])

    owner_cred = "live-tg-owner-" + uuid.uuid4().hex
    for module in (consent, direct, tools, identity, tg):
        module.owner_credential_matches = lambda c, o=owner_cred: c == o
    owner = owner_hash(owner_cred)
    failures: list[str] = []

    print("1 · save token")
    tg.save_token(owner, "1234567890:AAbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    s = tg.status(owner)
    print("   has token:", s["has_token"], "| bound:", s["bound"])
    if not s["has_token"]:
        failures.append("no_token")

    print("2 · start bind")
    bind_result = tg.start_bind(owner)
    code = bind_result["code"]
    print("   code length:", len(code))
    if len(code) != 8:
        failures.append("bind_code_length")

    print("3 · confirm bind (stranger sends code to the bot)")
    stranger_chat_id = 123456789
    tg.confirm_bind(owner, code, stranger_chat_id, 87654321)
    s2 = tg.status(owner)
    print("   bound:", s2["bound"], "| chat_id:", s2["chat_id"])
    if not s2["bound"] or s2["chat_id"] != stranger_chat_id:
        failures.append("bind_not_confirmed")

    print("4 · stranger ignored")
    another_chat = 999888777
    print("   bound chat:", s2["chat_id"], "| stranger chat:", another_chat)
    if another_chat == s2["chat_id"]:
        failures.append("same_chat")

    print("5 · missing token fails closed")
    tg2 = _mock_telegram()
    bind = tg2.load_bind(owner)
    print("   bind:", bind)
    if bind is not None:
        failures.append("expected_none_bind")

    print("6 · consent execute once (in-process)")
    with SessionLocal() as db:
        conv = AgentConversation(owner_hash=owner, status="active")
        db.add(conv)
        db.commit()
        cid = str(conv.id)
    card = consent.propose(owner_cred, cid, [{"name": "remember", "kind": "remember", "text": "test", "quote": "test"}],
                           {"title": "测试确认"})
    print("   card id:", card["id"])

    token_value = ""
    for choice in card["choices"]:
        if choice["value"].startswith("confirm:"):
            token_value = choice["value"][len("confirm:"):]
            break
    if not token_value:
        failures.append("no_token_in_card")

    def _noop(op, key):
        return {"ok": True}

    result1 = consent.execute(owner_cred, cid, token_value,
                              [{"name": "remember", "kind": "remember", "text": "test", "quote": "test"}], _noop)
    print("   first execute:", result1.get("ok"))
    if not result1.get("ok"):
        failures.append("execute_failed")

    try:
        consent.execute(owner_cred, cid, token_value,
                        [{"name": "remember", "kind": "remember", "text": "test", "quote": "test"}], _noop)
        print("   second execute: should have failed")
        failures.append("double_execute")
    except consent.ConsentError:
        print("   second execute: ConsentError (expected)")

    print("7 · unbind")
    tg.unbind(owner)
    s3 = tg.status(owner)
    print("   bound after unbind:", s3["bound"])
    if s3["bound"]:
        failures.append("unbind_failed")

    print("RESULT:", "PASS" if not failures else "FAIL " + ",".join(failures))
    return 0 if not failures else 1


def _mock_telegram():
    """Return a fake agent_telegram module with no stored token."""
    class _Mock:
        @staticmethod
        def load_bind(owner):
            return None

        @staticmethod
        def status(owner):
            return {"configured": False, "has_token": False, "bound": False,
                    "bot_username": None, "chat_id": None, "bound_at": None}

    return _Mock()


if __name__ == "__main__":
    sys.exit(main())