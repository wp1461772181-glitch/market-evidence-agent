"""Live check of scheduled tasks with a real model, in-process (no running server, no launchd needed).

1. A background model is saved (key encrypted), a brief task is created and made due, and one runner tick
   runs it unattended: the classifier may read 「加入研究队列」 in the instruction, but nothing is written;
   any write the model wants becomes a 待你确认 inbox item, and no consent card exists.
2. The inbox gets the brief (with numbered sources, or the no-evidence note).
3. The key is revoked and the task made due again: it is marked 未运行：缺少模型.

Uses a disposable PostgreSQL database (dropped afterwards) and the DeepSeek key from the environment or
.env (DEEPSEEK_API_KEY, optional DEEPSEEK_MODEL).  Prints statuses and titles, never the key.
Run from the repository root: ``python scripts/live_check_agent_tasks.py``.
"""
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

# Imported before this script's own DATABASE_URL: importing live_check_profile points DATABASE_URL
# at its own (never created) database, so ours must be set after it.
from live_check_profile import env_value  # noqa: E402

ADMIN_URL = make_url(os.getenv("TEST_DATABASE_URL",
                               "postgresql+psycopg://market_evidence:market_evidence_dev@localhost:55432/test_market_evidence"))
DATABASE = f"test_live_tasks_{uuid.uuid4().hex[:12]}"
os.environ["DATABASE_URL"] = ADMIN_URL.set(database=DATABASE).render_as_string(hide_password=False)
os.environ["PERSONAL_CHAT_LOGIN_PATH"] = f"/tmp/live-tasks-missing-{uuid.uuid4().hex}.json"
os.environ.setdefault("MODEL_CREDENTIAL_KEY", "live-check-model-credential-key-0123456789")



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
    from app import agent_task_runner as runner
    from app import agent_tasks as tasks
    import app.agent_chat_identity as identity
    from app.agent_chat_models import AgentResearchQueue, AgentTask
    from app.agent_chat_store import engine as store_engine, owner_hash
    from app.agent_workflow import consent, direct, tools
    from app.agent_workflow.models import AgentConsent
    from app.database import Base, SessionLocal, engine
    import app.main  # noqa: F401  (registers every table on Base)

    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    # The workflow's LangGraph checkpoint tables (scripts/migrate_agent_workflow.py does this for the real DB);
    # without them every unattended turn fails with UndefinedTable before the model is called.
    from app.agent_workflow.checkpoints import open_postgres_checkpointer

    async def migrate():
        async with open_postgres_checkpointer(os.environ["DATABASE_URL"]) as saver:
            await saver.setup()
    asyncio.run(migrate())
    owner = "live-owner-" + uuid.uuid4().hex
    for module in (consent, direct, tools, identity):
        module.owner_credential_matches = lambda c, o=owner: c == o
    hashed = owner_hash(owner)
    failures = []

    async def tick(now):
        try:
            return await runner.run_due(now=now, credential=owner)
        finally:
            await store_engine.dispose()

    tasks.save_background_model(hashed, provider="deepseek", model=model, base_url="https://api.deepseek.com", api_key=key)
    with SessionLocal() as db:
        db.add(AgentResearchQueue(owner_hash=hashed, symbols=["NVDA", "AAPL"]))
        db.commit()
    task = tasks.create_task(hashed, {"name": "研究队列简报", "kind": "brief", "schedule_kind": "weekdays", "schedule_time": "08:00",
                                      "timezone": "Asia/Shanghai",
                                      "instruction": "用已保存的资料总结研究队列公司最近的申报和新闻；如果发现主要竞争对手，把它加入研究队列。"})
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.get(AgentTask, uuid.UUID(task["id"])).next_run_at = now - timedelta(minutes=1)
        db.commit()

    print("1 · unattended run")
    status = asyncio.run(tick(now))
    print("   status:", status)
    with SessionLocal() as db:
        cards = db.query(AgentConsent).count()
        queue = db.get(AgentResearchQueue, hashed).symbols
    confirm = tasks.list_inbox(hashed, section="confirm")
    print("   consent cards:", cards, "| research queue:", queue, "| 待你确认:", [i["title"] for i in confirm])
    if cards or queue != ["NVDA", "AAPL"]:
        failures.append("unattended_wrote")
    if "failed" in status.values():
        failures.append("run_failed")

    print("2 · inbox brief")
    briefs = tasks.list_inbox(hashed, section="brief")
    for item in briefs:
        print("   ", item["title"], "| sources:", [(s["ordinal"], s["title"][:40]) for s in item["sources"]])
        print("    ", item["body"][:240].replace("\n", " "))
    if not briefs:
        failures.append("no_brief")

    print("3 · revoked key")
    tasks.revoke_background_key(hashed)
    with SessionLocal() as db:
        db.get(AgentTask, uuid.UUID(task["id"])).next_run_at = now
        db.commit()
    asyncio.run(tick(now + timedelta(minutes=5)))
    label = tasks.list_tasks(hashed)[0]["last_status_label"]
    print("   task:", label)
    if label != "未运行：缺少模型":
        failures.append("revoked_key")

    print("RESULT:", "PASS" if not failures else "FAIL " + ",".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
