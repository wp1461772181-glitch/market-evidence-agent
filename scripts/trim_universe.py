"""Trim the research universe down to the seven mega-caps plus the SPY benchmark.

Everything the app treats as an active symbol is derived from data: DEFAULT_STOCKS
plus whatever symbols the price, forecast, filing and evidence tables still mention
(``app/active_symbols.py``).  After the 2026 full-text refactor two extra tickers
(AMD, AVGO) survived only because their live forecasts were kept, and the hourly
monitors then re-fetched their price history -- which is why they kept showing up
in the research list.

This script deletes those leftovers in foreign-key order, then rewrites the owner's
saved research queue to the trimmed set.  Safe by default: it only reports unless
``--apply`` is given.

    .venv-rag/bin/python scripts/trim_universe.py            # dry run
    .venv-rag/bin/python scripts/trim_universe.py --apply    # delete
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from app.database import engine  # noqa: E402

KEEP = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "SPY")
DROP = ("AMD", "AVGO")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the deletions (default: report only)")
    args = parser.parse_args()

    statements = [
        ("evaluations", "delete from forecast_evaluations_v2 where forecast_version_id in (select id from forecast_versions_v2 where symbol = any(:drop))"),
        ("detach jobs", "update forecast_jobs_v2 set result_version_id = null, root_version_id = null, parent_version_id = null where symbol = any(:drop)"),
        ("versions", "delete from forecast_versions_v2 where symbol = any(:drop)"),
        ("jobs", "delete from forecast_jobs_v2 where symbol = any(:drop)"),
        ("material analyses", "delete from material_analysis_versions where source_type = 'uploaded_evidence' and source_id::text in (select id::text from uploaded_evidence where symbol = any(:drop))"),
        ("material jobs", "delete from material_analysis_jobs where source_type = 'uploaded_evidence' and source_id::text in (select id::text from uploaded_evidence where symbol = any(:drop))"),
        ("index tasks", "delete from knowledge_index_tasks where chunk_id in (select c.id from knowledge_chunks c join knowledge_documents d on d.id = c.document_id where d.source_id::text in (select id::text from uploaded_evidence where symbol = any(:drop)))"),
        ("chunks", "delete from knowledge_chunks where document_id in (select id from knowledge_documents where source_id::text in (select id::text from uploaded_evidence where symbol = any(:drop)))"),
        ("documents", "delete from knowledge_documents where source_id::text in (select id::text from uploaded_evidence where symbol = any(:drop))"),
        ("evidence", "delete from uploaded_evidence where symbol = any(:drop)"),
        ("price revisions", "delete from market_price_revisions where symbol = any(:drop)"),
        ("prices", "delete from market_prices where symbol = any(:drop)"),
    ]

    with engine.begin() as conn:
        counts = []
        for label, sql in statements:
            result = conn.execute(text(sql), {"drop": list(DROP)})
            counts.append((label, result.rowcount))
        for label, count in counts:
            print(f"{'apply' if args.apply else 'dry  '} {label:16s} {count:>6d}")
        if not args.apply:
            conn.rollback()
            print("\nno changes written; rerun with --apply to delete")
            return 0

        queue = conn.execute(text("select owner_hash, symbols from agent_research_queue")).all()
        for owner_hash, symbols in queue:
            kept = [symbol for symbol in symbols if symbol in KEEP]
            conn.execute(
                text("update agent_research_queue set symbols = cast(:symbols as json) where owner_hash = :owner"),
                {"symbols": json.dumps(kept), "owner": owner_hash},
            )
            print(f"research queue {owner_hash[:8]}… -> {kept}")

    print("\nremaining symbols:",
          [row[0] for row in engine.connect().execute(text(
              "select distinct symbol from market_prices where symbol = any(:keep) order by 1"), {"keep": list(KEEP)}).all()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
