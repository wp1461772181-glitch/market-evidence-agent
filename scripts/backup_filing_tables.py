"""Dump the tables that the full-text SEC refactor will delete or rewrite.

The repository has no pg_dump on this machine (Postgres runs inside OrbStack),
so this script streams each table to a gzipped CSV with the same column order
that Postgres reports, which is enough to inspect or reload the data by hand.

Usage:
    PUBLIC_API_MODE=false PYTHONPATH=. .venv-rag/bin/python scripts/backup_filing_tables.py
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import DATABASE_URL

# (table, where-clause or None for the whole table)
TABLES: tuple[tuple[str, str | None], ...] = (
    ("sec_filing_inventory", None),
    ("knowledge_documents", "source_type in ('official_filing','ai_analysis')"),
    ("knowledge_extraction_runs", None),
    ("material_analysis_versions", None),
    ("material_analysis_jobs", None),
    (
        "forecast_versions_v2",
        "job_id in (select id from forecast_jobs_v2 where time_mode = 'historical_research')",
    ),
    ("forecast_jobs_v2", None),
    ("forecast_evaluations_v2", None),
    ("evidence_event_versions_v2", None),
    ("official_monitor_runs_v2", None),
    ("market_prices", "symbol in ('AVGO','AMD','INTC','MU')"),
    ("market_price_revisions", "symbol in ('AVGO','AMD','INTC','MU')"),
)

EXTRA_COLUMNS = ("knowledge_chunks", "knowledge_index_tasks")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def dump_table(conn, table: str, where: str | None, target: Path) -> tuple[int, int]:
    columns = [
        row[0]
        for row in conn.execute(
            text(
                "select column_name from information_schema.columns "
                "where table_schema='public' and table_name=:t order by ordinal_position"
            ),
            {"t": table},
        )
    ]
    if not columns:
        return 0, 0
    selected = ", ".join('"' + column + '"' for column in columns)
    statement = f'select {selected} from "{table}"'
    if where:
        statement += f" where {where}"
    rows = 0
    with gzip.open(target, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        result = conn.execution_options(stream_results=True).execute(text(statement))
        for record in result:
            writer.writerow([_cell(value) for value in record])
            rows += 1
    return rows, target.stat().st_size


def main(argv: list[str] | None = None) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path("data/backups") / f"fulltext-refactor-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(DATABASE_URL)
    summary: dict[str, dict[str, int | str]] = {}
    with engine.connect() as conn:
        for table, where in TABLES:
            target = root / f"{table}.csv.gz"
            rows, size = dump_table(conn, table, where, target)
            summary[table] = {"rows": rows, "bytes": size, "where": where or "*"}
            print(f"{table:<32} {rows:>7} rows  {size/1_048_576:6.1f} MB")
        for table in EXTRA_COLUMNS:
            count = conn.execute(text(f'select count(*) from "{table}"')).scalar() or 0
            summary[table] = {"rows": count, "bytes": 0, "where": "not dumped (rebuildable)"}
            print(f"{table:<32} {count:>7} rows  (skipped: rebuildable from filings)")
    (root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nbackup written to {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
