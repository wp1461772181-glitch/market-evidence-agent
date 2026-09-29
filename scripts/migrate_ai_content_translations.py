"""Inspect or apply the additive AI-content translation cache table."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import engine
from app.localization_models import LOCALIZATION_TABLES


TABLE_NAMES = tuple(table.name for table in LOCALIZATION_TABLES)
PREREQUISITES = ("evidence_event_versions_v2", "material_analysis_versions", "forecast_versions_v2")
SCHEMA_LOCK_KEY = 6_214_238_811


def schema_state(db_engine: Engine) -> dict[str, list[str]]:
    inspector = inspect(db_engine)
    names = set(inspector.get_table_names())
    return {
        "present": [name for name in TABLE_NAMES if name in names],
        "missing": [name for name in TABLE_NAMES if name not in names],
        "missing_prerequisites": [name for name in PREREQUISITES if name not in names],
    }


def apply_schema(db_engine: Engine) -> dict[str, list[str]]:
    with db_engine.begin() as connection:
        if connection.dialect.name != "postgresql":
            raise RuntimeError("AI content localization migration requires PostgreSQL")
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": SCHEMA_LOCK_KEY})
        names = set(inspect(connection).get_table_names())
        missing_prerequisites = [name for name in PREREQUISITES if name not in names]
        if missing_prerequisites:
            raise RuntimeError("forecast V2 and material analysis schemas must be applied before AI content localization")
        LOCALIZATION_TABLES[0].metadata.create_all(bind=connection, tables=list(LOCALIZATION_TABLES), checkfirst=True)
    return schema_state(db_engine)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or apply the additive AI content localization schema")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only schema check (default)")
    action.add_argument("--apply", action="store_true", help="create the additive translation cache table")
    args = parser.parse_args(argv)
    try:
        result = apply_schema(engine) if args.apply else schema_state(engine)
    except RuntimeError as exc:
        print(json.dumps({"action": "apply" if args.apply else "check", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"action": "apply" if args.apply else "check", **result}, sort_keys=True))
    return 0 if not result["missing_prerequisites"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
