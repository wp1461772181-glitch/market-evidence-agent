"""File earlier agent web saves into 资料库 (UX batch 2, P1).

Before UX batch 2 a confirmed ``ingest_web_source`` wrote only ``agent_web_sources`` and a vector-store
document, so the page never showed in 资料库 › 材料库.  This script gives every such row that names a stock
its 资料库 copy through the same ``save_to_library`` the agent uses now (``create_uploaded_evidence`` plus the
owner mapping) and withdraws the web-only search copy, so search keeps one copy.

Read-only by default: it prints what it would file.  Pass ``--apply`` to write.  Rows without a stock are
listed and left alone (资料库 is per stock).  Safe to run again: filed rows are skipped.

    python -m scripts.backfill_web_sources_to_materials            # dry run
    python -m scripts.backfill_web_sources_to_materials --apply
"""
from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select

from app.agent_workflow.models import AgentWebSource
from app.agent_workflow.web_sources import ensure_columns, save_to_library
from app.database import SessionLocal
from app.manual_evidence import ManualEvidenceError


def pending(db):
    return db.scalars(select(AgentWebSource).where(AgentWebSource.uploaded_evidence_id.is_(None))
                      .order_by(AgentWebSource.fetched_at)).all()


def run(*, apply: bool, session_factory=SessionLocal) -> dict:
    ensure_columns()
    counts = Counter()
    by_symbol = Counter()
    with session_factory() as db:
        rows = pending(db)
        for row in rows:
            if not row.symbol:
                counts["no_symbol"] += 1
                continue
            by_symbol[row.symbol] += 1
            if not apply:
                counts["would_file"] += 1
                continue
            try:
                _, created = save_to_library(db, row.owner_hash, row, row.symbol)
                db.commit()
                counts["filed" if created else "linked_existing"] += 1
            except (ManualEvidenceError, ValueError) as exc:
                db.rollback()
                counts["refused"] += 1
                print(f"refused {row.id} ({row.symbol}): {exc}")
    return {"apply": apply, "scanned": len(rows), **counts, "by_symbol": dict(by_symbol)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the 资料库 rows (default: dry run)")
    args = parser.parse_args(argv)
    report = run(apply=args.apply)
    print(("APPLIED" if args.apply else "DRY RUN") + f": scanned {report['scanned']} web saves without a 资料库 row")
    for key in ("would_file", "filed", "linked_existing", "no_symbol", "refused"):
        if report.get(key):
            print(f"  {key}: {report[key]}")
    for symbol, count in sorted(report["by_symbol"].items()):
        print(f"  {symbol}: {count}")


if __name__ == "__main__":
    main()
