"""Add research-brief and experimental Jev fields to V2 forecast versions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import engine


SCHEMA_LOCK_KEY = 6_214_238_418
TABLE = "forecast_versions_v2"
REQUIRED_COLUMNS = ("decision_probabilities", "research_brief")


def schema_state(db_engine: Engine) -> dict[str, object]:
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    if TABLE not in tables:
        return {"table_present": False, "missing_columns": list(REQUIRED_COLUMNS), "jev_status_enabled": False}
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    with db_engine.connect() as connection:
        constraint = connection.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = to_regclass(:table) AND conname = 'ck_forecast_versions_v2_model_status'"),
            {"table": TABLE},
        ).scalar_one_or_none()
    return {
        "table_present": True,
        "missing_columns": [name for name in REQUIRED_COLUMNS if name not in columns],
        "jev_status_enabled": bool(constraint and "experimental_jev" in constraint),
    }


def apply_schema(db_engine: Engine) -> dict[str, object]:
    with db_engine.begin() as connection:
        if connection.dialect.name != "postgresql":
            raise RuntimeError("agent forecast migration requires PostgreSQL")
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": SCHEMA_LOCK_KEY})
        if TABLE not in inspect(connection).get_table_names():
            raise RuntimeError("forecast V2 schema must be applied before the agent forecast migration")
        connection.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS decision_probabilities JSONB"))
        connection.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS research_brief JSONB"))
        constraint = connection.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'forecast_versions_v2'::regclass AND conname = 'ck_forecast_versions_v2_model_status'")
        ).scalar_one_or_none()
        if constraint is None or "experimental_jev" not in constraint:
            connection.execute(text("ALTER TABLE forecast_versions_v2 DROP CONSTRAINT IF EXISTS ck_forecast_versions_v2_model_status"))
            connection.execute(
                text(
                    "ALTER TABLE forecast_versions_v2 ADD CONSTRAINT ck_forecast_versions_v2_model_status "
                    "CHECK (model_status IN ('research_only', 'experimental_joint', 'experimental_jev', 'baseline_only', 'blocked_data'))"
                )
            )
    return schema_state(db_engine)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or add the V3 agent forecast fields")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only schema check (default)")
    action.add_argument("--apply", action="store_true", help="add nullable fields and experimental_jev under a lock")
    args = parser.parse_args(argv)
    state = apply_schema(engine) if args.apply else schema_state(engine)
    print(json.dumps({"action": "apply" if args.apply else "check", **state}, sort_keys=True))
    return 0 if state["table_present"] and not state["missing_columns"] and state["jev_status_enabled"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
