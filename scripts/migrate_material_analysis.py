"""Inspect or apply the additive two-table material-analysis schema."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import engine
from app.forecast_v2_models import EvidenceEventVersionV2 as _EvidenceEventVersionV2  # register the FK target in Base metadata
from app.material_analysis_models import MATERIAL_ANALYSIS_TABLES


TABLE_NAMES = tuple(table.name for table in MATERIAL_ANALYSIS_TABLES)
SCHEMA_LOCK_KEY = 6_214_238_417


def schema_state(db_engine: Engine) -> dict[str, list[str]]:
    inspector = inspect(db_engine)
    names = set(inspector.get_table_names())
    missing = [name for name in TABLE_NAMES if name not in names]
    prerequisites = [name for name in ("evidence_event_versions_v2",) if name not in names]
    return {"present": [name for name in TABLE_NAMES if name in names], "missing": missing,
            "missing_prerequisites": prerequisites}


def apply_schema(db_engine: Engine) -> dict[str, list[str]]:
    with db_engine.begin() as connection:
        if connection.dialect.name != "postgresql":
            raise RuntimeError("material analysis migration requires PostgreSQL")
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": SCHEMA_LOCK_KEY})
        inspector = inspect(connection)
        names = set(inspector.get_table_names())
        state = {"missing_prerequisites": [name for name in ("evidence_event_versions_v2",) if name not in names]}
        if state["missing_prerequisites"]:
            raise RuntimeError("forecast V2 evidence schema must be applied before material analysis")
        MATERIAL_ANALYSIS_TABLES[0].metadata.create_all(bind=connection, tables=list(MATERIAL_ANALYSIS_TABLES), checkfirst=True)
    return schema_state(db_engine)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or apply the additive material analysis schema")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only schema check (default)")
    action.add_argument("--apply", action="store_true", help="create the two additive tables under a transaction lock")
    args = parser.parse_args(argv)
    result = apply_schema(engine) if args.apply else schema_state(engine)
    print(json.dumps({"action": "apply" if args.apply else "check", **result}, sort_keys=True))
    return 0 if not result["missing_prerequisites"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
