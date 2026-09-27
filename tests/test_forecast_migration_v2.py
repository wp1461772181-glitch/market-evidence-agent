import json
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.forecast_v2_models import ForecastJobV2, V2_TABLES
from app.models import Forecast


_MIGRATION_PATH = Path(__file__).parents[1] / "scripts" / "migrate_forecast_v2.py"
_MIGRATION_SPEC = importlib.util.spec_from_file_location("migrate_forecast_v2", _MIGRATION_PATH)
assert _MIGRATION_SPEC is not None and _MIGRATION_SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_MIGRATION_SPEC)
_MIGRATION_SPEC.loader.exec_module(_MIGRATION)

V2_TABLE_NAMES = _MIGRATION.V2_TABLE_NAMES
apply_schema = _MIGRATION.apply_schema
main = _MIGRATION.main
schema_state = _MIGRATION.schema_state


def test_v2_migration_check_is_read_only_and_apply_is_idempotent(capsys, monkeypatch):
    """The V2 migration only creates new tables and can safely be re-run."""
    disposable_name = f"test_forecast_migration_{uuid4().hex[:12]}"
    admin_engine = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    isolated_engine = create_engine(engine.url.set(database=disposable_name))
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{disposable_name}"'))
        created = True
        Forecast.__table__.create(bind=isolated_engine)
        with Session(isolated_engine) as db:
            db.add(Forecast(symbol="AAPL", bullish_probability=0.4, neutral_probability=0.3,
                            bearish_probability=0.3, model_version="migration-fixture"))
            db.commit()

        monkeypatch.setattr(_MIGRATION, "engine", isolated_engine)
        import app.database as app_database
        import app.main as app_main
        import app.manual_evidence as manual_evidence
        import app.sec_filings as sec_filings

        monkeypatch.setattr(app_database, "engine", isolated_engine)
        monkeypatch.setattr(app_main, "engine", isolated_engine)
        monkeypatch.setattr(manual_evidence, "engine", isolated_engine)
        monkeypatch.setattr(sec_filings, "engine", isolated_engine)
        app_main.create_tables()
        legacy_columns_before = [column["name"] for column in inspect(isolated_engine).get_columns("forecasts")]
        legacy_count_before = isolated_engine.connect().execute(text("SELECT count(*) FROM forecasts")).scalar_one()
        assert schema_state(isolated_engine)["missing"] == list(V2_TABLE_NAMES)

        assert main(["--check"]) == 0
        check_output = json.loads(capsys.readouterr().out)
        assert check_output == {"action": "check", "missing": list(V2_TABLE_NAMES),
                                "missing_columns": [], "present": []}
        assert schema_state(isolated_engine)["missing"] == list(V2_TABLE_NAMES)

        assert main(["--apply"]) == 0
        first_apply = json.loads(capsys.readouterr().out)
        assert first_apply == {"action": "apply", "missing": [], "missing_columns": [],
                               "present": list(V2_TABLE_NAMES)}

        assert main(["--apply"]) == 0
        assert json.loads(capsys.readouterr().out) == first_apply
        assert schema_state(isolated_engine)["missing"] == []
        assert [column["name"] for column in inspect(isolated_engine).get_columns("forecasts")] == legacy_columns_before
        assert isolated_engine.connect().execute(text("SELECT count(*) FROM forecasts")).scalar_one() == legacy_count_before
    finally:
        isolated_engine.dispose()
        if created:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{disposable_name}" WITH (FORCE)'))
        admin_engine.dispose()


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


def test_apply_adds_monitor_evaluation_summary_to_an_old_table_without_losing_runs(client):
    apply_schema(engine)
    run_id = "d0f4ab42-3d26-4e81-a3c0-fc3d1180ee2d"
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE official_monitor_runs_v2 DROP COLUMN evaluation_summary"))
        connection.execute(
            text(
                "INSERT INTO official_monitor_runs_v2 "
                "(id, status, started_at, per_symbol_results) "
                "VALUES (:id, 'succeeded', now(), CAST(:results AS jsonb))"
            ),
            {"id": run_id, "results": '{"AAPL": {"status": "succeeded"}}'},
        )

    assert schema_state(engine)["missing_columns"] == ["official_monitor_runs_v2.evaluation_summary"]
    apply_schema(engine)
    apply_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("official_monitor_runs_v2")}
    assert "evaluation_summary" in columns
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT status, per_symbol_results, evaluation_summary FROM official_monitor_runs_v2 WHERE id = :id"),
            {"id": run_id},
        ).mappings().one()
    assert stored["status"] == "succeeded"
    assert stored["per_symbol_results"] == {"AAPL": {"status": "succeeded"}}
    assert stored["evaluation_summary"] is None
