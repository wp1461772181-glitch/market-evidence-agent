"""Run the owner's due agent tasks once (launchd ``com.market-evidence-agent.agent-tasks``, every 5 minutes).

A task due while the Mac slept runs once when it wakes (launchd fires a missed interval on wake, and the
runner collapses every missed slot into one run).  Only one runner works at a time.  The background
model's key is read from the database (encrypted, settings page); nothing secret is printed.

    python scripts/run_agent_tasks.py            # run what is due
    python scripts/run_agent_tasks.py --dry-run  # list what is due, run nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list the due tasks without running them")
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    # DATABASE_URL is read when app.database creates its engine, so the imports come after .env.
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    from app import agent_tasks as tasks
    from app.agent_chat_models import AgentTask
    from app.agent_task_runner import tick
    from app.database import SessionLocal
    now = datetime.now(UTC)
    try:
        if args.dry_run:
            with SessionLocal() as db:
                rows = db.scalars(select(AgentTask).where(AgentTask.enabled.is_(True))).all()
                due = [{"id": str(t.id), "name": t.name, "kind": t.kind, "next_run_at": t.next_run_at.isoformat(),
                        "missed_slots": tasks.missed_slots(t, now)} for t in rows if tasks.is_due(t, now)]
            print(json.dumps({"checked_at": now.isoformat(), "due": due}, ensure_ascii=False))
            return 0
        print(json.dumps({"checked_at": now.isoformat(), **tick()}, ensure_ascii=False))
    except OperationalError:
        # Postgres is not up yet (just woke, container starting): the next tick tries again.
        print(json.dumps({"checked_at": now.isoformat(), "status": "database_unavailable"}))
        return 0
    return 0


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    raise SystemExit(main())
