"""Inventory and prepare a narrowly scoped, backup-gated legacy cleanup.

Running this file without arguments is read-only.  The apply path only accepts
the current development database name, an exact manifest fingerprint, and a
locally verified full-database dump.  No tables are dropped or truncated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

from app.database import engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
CONTAINER = "market-evidence-postgres"
DB_USER = "market_evidence"
DB_NAME = "market_evidence"
OLD_PROCESSOR = "v2-research-only-worker-v1"

SNAPSHOT_TABLES = ("forecast_snapshots", "forecast_revisions", "forecast_revision_evidence", "evidence_revisions")
V2_TABLES = ("forecast_jobs_v2", "forecast_versions_v2", "forecast_evaluations_v2")
LEGACY_TABLES = (
    "forecasts", *SNAPSHOT_TABLES, *V2_TABLES, "research_runs", "event_extractions",
)
class CleanupError(RuntimeError):
    """A safe, user-readable refusal that contains no row payloads."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _rows(conn: Connection, query: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(text(query), params).mappings()]


def is_legacy_v2_chain(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(
        row["model_status"] == "research_only"
        and not row["has_brief"]
        and row["processor_version"] == OLD_PROCESSOR
        for row in rows
    )


def _assert_no_kept_json_reference(
    conn: Connection, candidate_ids: set[str], candidate_tables: set[str], label: str
) -> None:
    catalog = inspect(conn)
    for table in catalog.get_table_names():
        if table in candidate_tables:
            continue
        quoted_table = '"' + table.replace('"', '""') + '"'
        for column in catalog.get_columns(table):
            if not str(column["type"]).lower().startswith(("json", "jsonb")):
                continue
            quoted_column = '"' + column["name"].replace('"', '""') + '"'
            values = conn.execute(text(
                f"SELECT {quoted_column}::text FROM {quoted_table} WHERE {quoted_column} IS NOT NULL"
            )).scalars()
            if any(any(value in (raw or "") for value in candidate_ids) for raw in values):
                raise RuntimeError(f"a kept JSON field references a candidate {label}")


def build_plan(conn: Connection, *, expected_db: str = DB_NAME) -> dict[str, Any]:
    """Return the fixed allowlist and row identities; never return source text."""
    db_name = conn.execute(text("SELECT current_database()")).scalar_one()
    if db_name != expected_db:
        raise CleanupError("connected database name does not match the expected database")

    forecast_rows = _rows(conn, "SELECT id::text, model_version FROM forecasts WHERE model_version = 'mock-v1'")
    snapshot_ids = [r["id"] for r in _rows(conn, "SELECT id::text FROM forecast_snapshots ORDER BY id")]
    revision_rows = _rows(conn, "SELECT snapshot_id::text, parent_snapshot_id::text, root_snapshot_id::text FROM forecast_revisions")
    revision_evidence_rows = _rows(conn, "SELECT snapshot_id::text, parent_snapshot_id::text FROM forecast_revision_evidence")
    evidence_revision_rows = _rows(conn, "SELECT id::text, parent_snapshot_id::text, revised_snapshot_id::text FROM evidence_revisions")

    versions = _rows(conn, """SELECT id::text, root_id::text, job_id::text, model_status,
        research_brief IS NOT NULL AS has_brief, model_manifest->>'processor_version' AS processor_version
        FROM forecast_versions_v2 ORDER BY root_id, version_no""")
    jobs = _rows(conn, "SELECT id::text, root_version_id::text, parent_version_id::text, result_version_id::text FROM forecast_jobs_v2")
    evaluations = _rows(conn, "SELECT id::text, forecast_version_id::text FROM forecast_evaluations_v2")

    versions_by_root: dict[str, list[dict[str, Any]]] = {}
    for version in versions:
        versions_by_root.setdefault(version["root_id"], []).append(version)
    if not versions_by_root:
        raise CleanupError("no V2 roots found; refusing to create an ambiguous cleanup plan")
    for chain in versions_by_root.values():
        if not is_legacy_v2_chain(chain):
            raise CleanupError("a V2 chain does not match the exact legacy processor allowlist")

    version_ids = [row["id"] for row in versions]
    job_ids = [row["id"] for row in jobs]
    version_set = set(version_ids)
    job_set = set(job_ids)
    if len(versions) != len(jobs) or any(row["job_id"] not in job_set for row in versions):
        raise CleanupError("V2 version/job closure is incomplete")
    if any(row[column] not in (None, *version_set) for row in jobs for column in ("root_version_id", "parent_version_id", "result_version_id")):
        raise CleanupError("a V2 job points outside the candidate version set")
    if any(row["forecast_version_id"] not in version_set for row in evaluations):
        raise CleanupError("a V2 evaluation points outside the candidate version set")

    _assert_no_kept_json_reference(
        conn, version_set | job_set,
        {"forecast_versions_v2", "forecast_jobs_v2", "forecast_evaluations_v2"},
        "V2 forecast or job",
    )

    # Every old research run is linked only by the candidate revision record or
    # legacy report JSON. Any reference from a kept row blocks the whole set.
    run_ids = [r["id"] for r in _rows(conn, "SELECT id::text FROM research_runs ORDER BY id")]
    cache_keys = [r["cache_key"] for r in _rows(conn, "SELECT cache_key FROM event_extractions ORDER BY cache_key")]
    for ids, owner_tables, label in (
        (set(run_ids), {"research_runs", "forecast_versions_v2"}, "research-run"),
        (set(cache_keys), {"event_extractions", "research_runs", "forecast_versions_v2"}, "extraction-cache"),
    ):
        for table in inspect(conn).get_table_names():
            if table in owner_tables:
                continue
            for column in inspect(conn).get_columns(table):
                if not str(column["type"]).lower().startswith(("json", "jsonb")):
                    continue
                quoted_table = '"' + table.replace('"', '""') + '"'
                quoted_column = '"' + column["name"].replace('"', '""') + '"'
                values = conn.execute(text(f"SELECT {quoted_column}::text FROM {quoted_table} WHERE {quoted_column} IS NOT NULL")).scalars()
                if any(any(value in (raw or "") for value in ids) for raw in values):
                    raise RuntimeError(f"a kept JSON field references a candidate {label}")

    _assert_no_kept_json_reference(
        conn, set(snapshot_ids), set(SNAPSHOT_TABLES), "forecast snapshot"
    )

    # Event cache-key fields are retained as provenance; do not delete a cache
    # row if any preserved event version actually resolves to it.
    cache_join_count = conn.execute(text("""SELECT count(DISTINCT e.cache_key)
        FROM event_extractions e JOIN evidence_event_versions_v2 v
        ON v.extraction_cache_key = e.cache_key""")).scalar_one()
    if cache_join_count:
        raise CleanupError("a preserved evidence version resolves to an extraction cache row")

    candidate = {
        "forecasts": [r["id"] for r in forecast_rows],
        "forecast_snapshots": snapshot_ids,
        "forecast_revisions": [r["snapshot_id"] for r in revision_rows],
        "forecast_revision_evidence": [r["snapshot_id"] for r in revision_evidence_rows],
        "evidence_revisions": [r["id"] for r in evidence_revision_rows],
        "forecast_evaluations_v2": [r["id"] for r in evaluations],
        "forecast_versions_v2": version_ids,
        "forecast_jobs_v2": job_ids,
        "research_runs": run_ids,
        "event_extractions": cache_keys,
    }
    counts = {table: len(ids) for table, ids in candidate.items()}
    payload = {"database": DB_NAME, "candidate_ids": candidate, "counts": counts, "policy": "legacy-cleanup-v1"}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**payload, "manifest_fingerprint": fingerprint}


def table_digests(conn: Connection) -> dict[str, dict[str, Any]]:
    """Hash each row of each table without returning row contents."""
    inspector = inspect(conn)
    digests: dict[str, dict[str, Any]] = {}
    for table in inspector.get_table_names():
        pk = inspector.get_pk_constraint(table).get("constrained_columns") or []
        order = ", ".join('"' + name.replace('"', '""') + '"' for name in pk)
        quoted = '"' + table.replace('"', '""') + '"'
        query = f"SELECT to_jsonb(t)::text FROM {quoted} AS t" + (f" ORDER BY {order}" if order else "")
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(text(query)).scalars():
            data = row.encode("utf-8")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
            count += 1
        digests[table] = {"rows": count, "sha256": digest.hexdigest()}
    return digests


def _write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    encoded = json.dumps(value, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == encoded:
            return
        raise CleanupError("refusing to replace an existing private cleanup artifact")
    fd, temporary = tempfile.mkstemp(prefix=".legacy-cleanup-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _docker(*args: str, input_stream: Any = None, output_stream: Any = subprocess.PIPE) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", *args], stdin=input_stream, stdout=output_stream,
                          stderr=subprocess.PIPE, check=True)


def _engine_for_database(database: str) -> Engine:
    return create_engine(engine.url.set(database=database), pool_pre_ping=True)


def _delete_candidates(conn: Connection, plan: dict[str, Any]) -> None:
    ids = plan["candidate_ids"]

    def delete(table: str, column: str = "id", values: list[str] | None = None) -> None:
        values = ids.get(table, []) if values is None else values
        if values:
            stmt = text(f'DELETE FROM "{table}" WHERE "{column}" IN :values').bindparams(bindparam("values", expanding=True))
            conn.execute(stmt, {"values": values})

    # Break only the nullable back-pointers belonging to the fully approved
    # candidate jobs. Version-to-job and intra-version roots are then removed
    # together; no retained row is updated or cascaded.
    delete("forecast_revision_evidence", "snapshot_id", ids["forecast_revision_evidence"])
    delete("evidence_revisions", values=ids["evidence_revisions"])
    delete("forecast_revisions", "snapshot_id", ids["forecast_revisions"])
    delete("forecast_snapshots", values=ids["forecast_snapshots"])
    delete("forecasts", values=ids["forecasts"])
    delete("forecast_evaluations_v2", values=ids["forecast_evaluations_v2"])
    if ids["forecast_jobs_v2"]:
        conn.execute(text("""UPDATE forecast_jobs_v2 SET root_version_id=NULL,
            parent_version_id=NULL, result_version_id=NULL WHERE id::text IN :job_ids""").bindparams(bindparam("job_ids", expanding=True)),
            {"job_ids": ids["forecast_jobs_v2"]})
    delete("forecast_versions_v2", values=ids["forecast_versions_v2"])
    delete("forecast_jobs_v2", values=ids["forecast_jobs_v2"])
    delete("research_runs", values=ids["research_runs"])
    delete("event_extractions", "cache_key", ids["event_extractions"])


def _verify_removed(conn: Connection, plan: dict[str, Any]) -> None:
    for table, expected in plan["counts"].items():
        if not expected:
            continue
        count = conn.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
        if count:
            raise RuntimeError(f"isolated cleanup left rows in {table}")


def prepare_backup(plan: dict[str, Any]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BACKUP_DIR, 0o700)
    fingerprint = plan["manifest_fingerprint"]
    stem = f"development-{DB_NAME}-{fingerprint[:16]}"
    dump_path = BACKUP_DIR / f"{stem}.dump"
    plan_path = BACKUP_DIR / f"legacy-cleanup-plan-{DB_NAME}-{fingerprint[:16]}.json"
    verification_path = BACKUP_DIR / f"{stem}.verification.json"
    if verification_path.is_file() and dump_path.is_file():
        report = json.loads(verification_path.read_text(encoding="utf-8"))
        if (report.get("manifest_fingerprint") == fingerprint
                and report.get("restore_verified") is True
                and report.get("isolated_cleanup_verified") is True
                and hashlib.sha256(dump_path.read_bytes()).hexdigest() == report.get("dump_sha256")):
            return verification_path
        raise CleanupError("existing backup artifacts are incomplete or do not match the plan")
    if dump_path.exists() or verification_path.exists():
        raise CleanupError("refusing to overwrite an existing partial backup")
    _write_private(plan_path, plan)

    with engine.connect() as live:
        before = table_digests(live)
    with dump_path.open("wb") as dump:
        os.chmod(dump_path, 0o600)
        _docker(CONTAINER, "pg_dump", "-U", DB_USER, "-d", DB_NAME, "--format=custom", output_stream=dump)
    os.chmod(dump_path, 0o600)
    dump_sha = hashlib.sha256(dump_path.read_bytes()).hexdigest()

    with dump_path.open("rb") as source:
        _docker("-i", CONTAINER, "pg_restore", "--list", input_stream=source)

    temp_db = f"legacy_restore_{fingerprint[:8]}_{uuid4().hex[:8]}"
    created = False
    restored_engine: Engine | None = None
    try:
        _docker(CONTAINER, "createdb", "-U", DB_USER, temp_db)
        created = True
        with dump_path.open("rb") as source:
            _docker("-i", CONTAINER, "pg_restore", "-U", DB_USER, "--no-owner", "--role", DB_USER,
                    "-d", temp_db, input_stream=source)
        restored_engine = _engine_for_database(temp_db)
        with restored_engine.connect() as restored:
            restored_hashes = table_digests(restored)
            restored_plan = build_plan(restored, expected_db=temp_db)
            if restored_hashes != before:
                raise CleanupError("restored table hashes do not match the live pre-dump hashes")
            if restored_plan["manifest_fingerprint"] != fingerprint:
                raise CleanupError("restored candidate manifest differs from the live plan")
        # Exercise the real FK closure only in the disposable restored DB.
        with restored_engine.begin() as trial:
            _delete_candidates(trial, restored_plan)
            for table in restored_plan["candidate_ids"]:
                remaining = trial.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
                if remaining:
                    raise CleanupError(f"isolated delete left rows in {table}")
        with restored_engine.connect() as restored:
            after_delete = table_digests(restored)
            retained_tables = set(after_delete) - set(restored_plan["candidate_ids"])
            changed_retained = [t for t in retained_tables if after_delete[t] != before[t]]
            if changed_retained:
                raise CleanupError("isolated delete changed retained table content")
    finally:
        if restored_engine is not None:
            restored_engine.dispose()
        if created:
            _docker(CONTAINER, "dropdb", "-U", DB_USER, "--if-exists", temp_db)

    report = {
        "database": DB_NAME,
        "manifest_fingerprint": fingerprint,
        "dump_file": dump_path.name,
        "dump_sha256": dump_sha,
        "restore_verified": True,
        "isolated_cleanup_verified": True,
        "table_hashes": before,
        "candidate_counts": plan["counts"],
    }
    _write_private(verification_path, report)
    return verification_path


def apply_cleanup(fingerprint: str, database_name: str) -> None:
    if database_name != DB_NAME:
        raise CleanupError("explicit development database name does not match")
    plan_path = BACKUP_DIR / f"legacy-cleanup-plan-{DB_NAME}-{fingerprint[:16]}.json"
    verify_path = BACKUP_DIR / f"development-{DB_NAME}-{fingerprint[:16]}.verification.json"
    if not plan_path.is_file() or not verify_path.is_file():
        raise CleanupError("exact private plan and verified full-database backup are required")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    verification = json.loads(verify_path.read_text(encoding="utf-8"))
    if plan["manifest_fingerprint"] != fingerprint or verification.get("manifest_fingerprint") != fingerprint:
        raise CleanupError("manifest fingerprint does not match the requested cleanup")
    if verification.get("restore_verified") is not True or verification.get("isolated_cleanup_verified") is not True:
        raise CleanupError("backup restore and isolated cleanup verification are required")
    dump_path = BACKUP_DIR / verification["dump_file"]
    if not dump_path.is_file() or hashlib.sha256(dump_path.read_bytes()).hexdigest() != verification["dump_sha256"]:
        raise CleanupError("verified backup is missing or its digest changed")

    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        tables = sorted(inspect(conn).get_table_names())
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            conn.execute(text(f"LOCK TABLE {quoted} IN SHARE ROW EXCLUSIVE MODE"))
        current = build_plan(conn)
        if current["manifest_fingerprint"] != fingerprint:
            raise CleanupError("live database no longer matches the reviewed manifest")
        before = table_digests(conn)
        if before != verification["table_hashes"]:
            raise CleanupError("live database content changed after backup; prepare a new backup")
        _delete_candidates(conn, current)
        for table in current["candidate_ids"]:
            if conn.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one():
                raise CleanupError(f"cleanup did not empty candidate table {table}")
        after = table_digests(conn)
        retained = set(after) - set(current["candidate_ids"])
        if any(after[table] != before[table] for table in retained):
            raise CleanupError("cleanup changed retained table content; transaction rolled back")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-backup", action="store_true", help="create an ignored full backup and verify restore/cleanup in a temporary database")
    parser.add_argument("--apply", action="store_true", help="delete exactly the private manifest rows (requires explicit database and fingerprint)")
    parser.add_argument("--confirm-development-database")
    parser.add_argument("--manifest-fingerprint")
    args = parser.parse_args()
    if args.apply:
        if not args.confirm_development_database or not args.manifest_fingerprint:
            parser.error("--apply requires --confirm-development-database and --manifest-fingerprint")
        apply_cleanup(args.manifest_fingerprint, args.confirm_development_database)
        print(json.dumps({"applied": True, "database": DB_NAME, "manifest_fingerprint": args.manifest_fingerprint}))
        return 0
    if args.confirm_development_database or args.manifest_fingerprint:
        parser.error("database confirmation and fingerprint are only valid with --apply")
    with engine.connect() as conn:
        plan = build_plan(conn)
    if args.prepare_backup:
        report_path = prepare_backup(plan)
        print(json.dumps({"dry_run": True, "candidate_counts": plan["counts"],
                          "manifest_fingerprint": plan["manifest_fingerprint"],
                          "backup_verification": str(report_path)}))
    else:
        print(json.dumps({"dry_run": True, "database": plan["database"],
                          "candidate_counts": plan["counts"],
                          "manifest_fingerprint": plan["manifest_fingerprint"]}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CleanupError as exc:
        print(f"legacy cleanup stopped: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        # Deliberately avoid propagating SQL/provider payloads or row contents.
        print(f"legacy cleanup stopped safely ({type(exc).__name__})", file=sys.stderr)
        sys.exit(2)
