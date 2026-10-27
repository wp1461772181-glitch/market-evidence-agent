"""Live check of the owner profile with a real model, in-process (no running server needed).

1. "以后金额都用亿美元表示" -> the model proposes ``remember`` -> the card button is pressed -> one explicit item.
2. The background extractor reads a message that states lasting preferences -> inferred items whose quotes
   are the message's own words.
3. A new conversation carries the profile block -> the first answer uses 亿美元.

Uses a disposable PostgreSQL database (dropped afterwards) and the DeepSeek key from the environment or
.env (DEEPSEEK_API_KEY, optional DEEPSEEK_MODEL).  Prints card titles, profile texts and the answer, never
the key.  Run from the repository root: ``python scripts/live_check_profile.py``.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

ADMIN_URL = make_url(os.getenv("TEST_DATABASE_URL",
                               "postgresql+psycopg://market_evidence:market_evidence_dev@localhost:55432/test_market_evidence"))
DATABASE = f"test_live_profile_{uuid.uuid4().hex[:12]}"
os.environ["DATABASE_URL"] = ADMIN_URL.set(database=DATABASE).render_as_string(hide_password=False)
os.environ["PERSONAL_CHAT_LOGIN_PATH"] = f"/tmp/live-profile-missing-{uuid.uuid4().hex}.json"
os.environ.setdefault("MODEL_CREDENTIAL_KEY", "live-check-model-credential-key-0123456789")


def env_value(name):
    if os.getenv(name):
        return os.environ[name]
    try:
        for line in open(".env", encoding="utf-8"):
            key, _, value = line.strip().partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


async def quiet(_event):
    pass


def main() -> int:
    key = env_value("DEEPSEEK_API_KEY")
    if not key:
        print("DEEPSEEK_API_KEY is not configured; nothing was run.")
        return 2
    model = env_value("DEEPSEEK_MODEL") or "deepseek-chat"
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{DATABASE}"'))
    try:
        return run(key, model)
    finally:
        from app.database import engine
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
        admin.dispose()


def run(key: str, model: str) -> int:
    from app import agent_profile as profile
    from app import agent_profile_extract as extract
    import app.agent_chat_identity as identity
    from app.agent_chat_store import owner_hash
    from app.agent_workflow import consent, direct, tools
    from app.agent_workflow.state import AgentRuntimeContext
    from app.database import Base, engine
    import app.main  # noqa: F401  (registers every table on Base)

    # The same tables the API creates at startup (app/main.py).
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    owner = "live-owner-" + uuid.uuid4().hex
    for module in (consent, direct, tools, identity):
        module.owner_credential_matches = lambda c, o=owner: c == o
    hashed = owner_hash(owner)
    provider = dict(provider="deepseek", model=model, api_key=key, base_url="https://api.deepseek.com")
    failures = []

    def context(conversation, block=None):
        return AgentRuntimeContext(provider["provider"], model, key, base_url=provider["base_url"], owner_hash=hashed,
                                   conversation_id=conversation, identity_credential=owner, profile_block=block)

    def turn(words, ctx, **kwargs):
        return asyncio.run(direct.run_direct_turn(words, ctx, emit=quiet, **kwargs))

    print("1 · explicit: 以后金额都用亿美元表示")
    ctx = context(str(uuid.uuid4()))
    first = turn("以后金额都用亿美元表示", ctx)
    card = first.get("pending_request") or {}
    print("   card:", (card.get("display") or {}).get("title"), "| targets:",
          [t.get("title") for t in (card.get("display") or {}).get("targets", [])])
    if not card:
        print("   no card · status:", first.get("status"), "reason:", first.get("reason"))
    if card.get("reason") == "write_confirmation":
        turn(card["choices"][0]["value"], ctx, state={"pending_request": card, "requested_pending_id": card["id"]})
    items = profile.list_items(hashed, include_archived=False)
    print("   profile:", [(i["origin"], i["text"], i["quote"]) for i in items])
    if not any("亿美元" in i["text"] and i["origin"] == "explicit" for i in items):
        failures.append("explicit_remember")

    print("2 · inferred: background extractor")
    message = "我主要关注半导体公司，以后回答请先给结论。顺便问下英伟达最近怎么样？"
    added = asyncio.run(extract.extract_from_turn(hashed, message, conversation_id=str(uuid.uuid4()),
                                                  turn_id=str(uuid.uuid4()), **provider))
    inferred = [i for i in profile.list_items(hashed, include_archived=False) if i["origin"] == "inferred"]
    print("   added:", added, "|", [(i["text"], i["quote"]) for i in inferred])
    if not all(profile.quote_in_message(i["quote"], message) for i in inferred):
        failures.append("inferred_quote")

    print("3 · new conversation, first answer")
    block, _ids = profile.render_block(hashed)
    result = turn("如果一家公司年营收是 391,035,000,000 美元，按我的偏好单位怎么写？只回答数字和单位。", context(str(uuid.uuid4()), block))
    answer = str(result.get("answer") or "")
    print("   answer:", answer[:240].replace("\n", " "))
    if "亿美元" not in answer:
        failures.append("unit_not_applied")

    print("RESULT:", "PASS" if not failures else "FAIL " + ",".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
