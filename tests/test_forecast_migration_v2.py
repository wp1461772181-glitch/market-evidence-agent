import json
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, engine
from app.forecast_v2_models import ForecastJobV2, V2_TABLES


_MIGRATION_PATH = Path(__file__).parents[1] / "scripts" / "migrate_forecast_v2.py"
_MIGRATION_SPEC = importlib.util.spec_from_file_location("migrate_forecast_v2", _MIGRATION_PATH)
assert _MIGRATION_SPEC is not None and _MIGRATION_SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_MIGRATION_SPEC)
_MIGRATION_SPEC.loader.exec_module(_MIGRATION)

V2_TABLE_NAMES = _MIGRATION.V2_TABLE_NAMES
apply_schema = _MIGRATION.apply_schema
main = _MIGRATION.main
schema_state = _MIGRATION.schema_state


def test_v2_migration_check_is_read_only_and_apply_is_idempotent(client, capsys):
    """The V2 migration only creates new tables and can safely be re-run."""

    legacy_columns_before = [column["name"] for column in inspect(engine).get_columns("forecasts")]
    legacy_count_before = engine.connect().execute(text("SELECT count(*) FROM forecasts")).scalar_one()

    # The FastAPI startup hook may have registered V2 metadata in this test
    # process.  Remove only the disposable V2 tables to prove --check writes
    # nothing and --apply creates only its own schema.
    V2_TABLES[0].metadata.drop_all(bind=engine, tables=list(V2_TABLES), checkfirst=True)
    assert schema_state(engine)["missing"] == list(V2_TABLE_NAMES)

    from app.main import create_tables

    create_tables()
    assert schema_state(engine)["missing"] == list(V2_TABLE_NAMES)

    assert main(["--check"]) == 0
    check_output = json.loads(capsys.readouterr().out)
    assert check_output == {"action": "check", "missing": list(V2_TABLE_NAMES), "present": []}
    assert schema_state(engine)["missing"] == list(V2_TABLE_NAMES)

    assert main(["--apply"]) == 0
    first_apply = json.loads(capsys.readouterr().out)
    assert first_apply == {"action": "apply", "missing": [], "present": list(V2_TABLE_NAMES)}

    assert main(["--apply"]) == 0
    second_apply = json.loads(capsys.readouterr().out)
    assert second_apply == first_apply
    assert schema_state(engine)["missing"] == []

    assert [column["name"] for column in inspect(engine).get_columns("forecasts")] == legacy_columns_before
    assert engine.connect().execute(text("SELECT count(*) FROM forecasts")).scalar_one() == legacy_count_before


def test_v2_schema_has_job_guards_and_event_snapshot_uniqueness(client):
    apply_schema(engine)
    inspector = inspect(engine)

    job_checks = {item["name"] for item in inspector.get_check_constraints("forecast_jobs_v2")}
    assert {"ck_forecast_jobs_v2_kind", "ck_forecast_jobs_v2_status", "ck_forecast_jobs_v2_lease_epoch"} <= job_checks
    event_uniques = {item["name"] for item in inspector.get_unique_constraints("evidence_event_versions_v2")}
    assert "uq_evidence_event_versions_v2_source_state" in event_uniques
    version_uniques = {item["name"] for item in inspector.get_unique_constraints("forecast_versions_v2")}
    assert {"uq_forecast_versions_v2_job", "uq_forecast_versions_v2_root_version_no"} <= version_uniques

    db = SessionLocal()
    try:
        db.add(
            ForecastJobV2(
                symbol="AAPL",
                kind="invalid-kind",
                idempotency_key="migration-v2-invalid-kind",
                request_fingerprint="0" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_apply_removes_obsolete_result_pointer_uniqueness(client):
    apply_schema(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE forecast_jobs_v2 "
                "ADD CONSTRAINT forecast_jobs_v2_result_version_id_key UNIQUE (result_version_id)"
            )
        )

    apply_schema(engine)
    unique_columns = {
        tuple(item["column_names"])
        for item in inspect(engine).get_unique_constraints("forecast_jobs_v2")
    }
    assert ("idempotency_key",) in unique_columns
    assert ("result_version_id",) not in unique_columns
