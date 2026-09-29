"""Wait for the queued monthly history campaign, then fit its research artifact."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.forecast_v2_models import ForecastJobV2


TERMINAL = {"succeeded", "succeeded_no_change", "blocked_data", "failed"}
PREFIX = "jev-history-monthly-v1-%"


def _snapshot() -> tuple[int, Counter]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ForecastJobV2).where(ForecastJobV2.idempotency_key.like(PREFIX))
        ).all()
    return len(rows), Counter(row.status for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-jobs", type=int, default=822)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.expected_jobs < 1 or args.poll_seconds < 5:
        parser.error("expected-jobs must be positive and poll-seconds must be at least 5")

    previous = None
    while True:
        total, states = _snapshot()
        summary = (total, tuple(sorted(states.items())))
        if summary != previous:
            finished = sum(states[state] for state in TERMINAL)
            print(f"History campaign: {finished}/{args.expected_jobs} terminal; {dict(states)}", flush=True)
            previous = summary
        if total >= args.expected_jobs and sum(states[state] for state in TERMINAL) == total:
            break
        time.sleep(args.poll_seconds)

    command = [sys.executable, "-m", "scripts.train_jev_historical_research"]
    if args.output:
        command.extend(["--output", str(args.output)])
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(command, cwd=project_root, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
