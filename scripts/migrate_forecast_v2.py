"""Create and inspect the additive V2 persistence schema.

The default ``--check`` is read-only.  ``--apply`` takes a PostgreSQL
transaction-scoped advisory lock and creates only the five V2 tables.  It
never drops, truncates, or alters legacy forecast records.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import engine
from app.forecast_v2_models import V2_TABLES


V2_TABLE_NAMES = tuple(table.name for table in V2_TABLES)
SCHEMA_LOCK_KEY = 6_214_237_902


def schema_state(db_engine: Engine) -> dict[str, list[str]]:
    """Return a read-only description of present and missing V2 tables."""

    table_names = set(inspect(db_engine).get_table_names())
    present = [name for name in V2_TABLE_NAMES if name in table_names]
    return {"present": present, "missing": [name for name in V2_TABLE_NAMES if name not in table_names]}


def apply_schema(db_engine: Engine) -> dict[str, list[str]]:
    """Create missing V2 tables atomically and safely repeat the migration."""

    with db_engine.begin() as connection:
        if connection.dialect.name != "postgresql":
            raise RuntimeError("forecast V2 migration requires PostgreSQL")
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": SCHEMA_LOCK_KEY})
        # Restrict SQLAlchemy to this migration's tables, so legacy metadata
        # registered by an importing process can never be changed here.
        V2_TABLES[0].metadata.create_all(bind=connection, tables=list(V2_TABLES), checkfirst=True)
        _upgrade_v2_review_snapshot_key(connection)
        _drop_obsolete_job_result_uniqueness(connection)
    return schema_state(db_engine)


def _upgrade_v2_review_snapshot_key(connection) -> None:
    """Allow a corrected review snapshot to append beside its prior state.

    The initial V2 schema keyed evidence only by content, review decision and
    extraction schema. A corrected reviewer note or star rating can keep the
    same decision, so it gets a frozen review fingerprint. This change touches
    only the additive V2 evidence table and preserves its existing rows.
    """

    definition = connection.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'uq_evidence_event_versions_v2_source_state'"
        )
    ).scalar_one_or_none()
    if definition is not None and "review_fingerprint" in definition:
        return
    connection.execute(
        text(
            "ALTER TABLE evidence_event_versions_v2 "
            "ADD COLUMN IF NOT EXISTS review_fingerprint VARCHAR(64) NOT NULL DEFAULT ''"
        )
    )
    connection.execute(
        text("ALTER TABLE evidence_event_versions_v2 DROP CONSTRAINT IF EXISTS uq_evidence_event_versions_v2_source_state")
    )
    connection.execute(
        text(
            "ALTER TABLE evidence_event_versions_v2 "
            "ADD CONSTRAINT uq_evidence_event_versions_v2_source_state "
            "UNIQUE (source_type, source_id, content_sha256, review_status, "
            "extraction_schema_version, review_fingerprint)"
        )
    )


def _drop_obsolete_job_result_uniqueness(connection) -> None:
    """Relax the initial V2 result pointer key without touching stored jobs.

    A ``succeeded_no_change`` job may legitimately point at an already saved
    version. The version table's unique ``job_id`` still guarantees that a job
    publishes at most one newly-created version.
    """

    names = connection.execute(
        text(
            "SELECT con.conname FROM pg_constraint AS con "
            "WHERE con.conrelid = 'forecast_jobs_v2'::regclass "
            "AND con.contype = 'u' "
            "AND con.conkey = ARRAY["
            "(SELECT attnum FROM pg_attribute "
            "WHERE attrelid = 'forecast_jobs_v2'::regclass "
            "AND attname = 'result_version_id' AND NOT attisdropped)"
            "]::smallint[]"
        )
    ).scalars()
    for name in names:
        quoted_name = name.replace('"', '""')
        connection.execute(text(f'ALTER TABLE forecast_jobs_v2 DROP CONSTRAINT "{quoted_name}"'))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or apply the additive forecast V2 schema")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only schema check (default)")
    action.add_argument("--apply", action="store_true", help="create missing V2 tables under a transaction lock")
    args = parser.parse_args(argv)

    state = apply_schema(engine) if args.apply else schema_state(engine)
    print(json.dumps({"action": "apply" if args.apply else "check", **state}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
