from datetime import UTC, datetime
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, text

from app.database import Base, SessionLocal, engine
from app.material_analysis_models import MaterialAnalysisJob
from app.models import UploadedEvidence
from scripts.migrate_material_analysis import apply_schema, schema_state


@pytest.fixture(autouse=True)
def db_tables(disposable_database):
    Base.metadata.create_all(bind=engine)
    yield
    with SessionLocal() as db:
        jobs = list(db.scalars(select(MaterialAnalysisJob)))
        source_ids = [job.source_id for job in jobs if job.source_type == "uploaded_media"]
        db.execute(delete(MaterialAnalysisJob).where(MaterialAnalysisJob.id.in_([job.id for job in jobs])))
        if source_ids:
            db.execute(delete(UploadedEvidence).where(UploadedEvidence.id.in_(source_ids)))
        db.commit()


def test_material_schema_apply_is_idempotent_and_preserves_operational_rows():
    source_id = uuid4()
    with SessionLocal() as db:
        source = UploadedEvidence(
            symbol="AAPL", title="Migration fixture", source_url="https://example.test/migration",
            published_at=datetime(2026, 9, 10, tzinfo=UTC), observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            credibility_stars=3, credibility_reason="fixture", impact_severity="low", filename="m.txt",
            content_sha256="a" * 64, raw_content=b"x", content_text="x", status="unconfirmed",
        )
        db.add(source)
        db.flush()
        job = MaterialAnalysisJob(
            source_type="uploaded_media", source_id=source.id, evidence_version_id=None,
            status="blocked_data", current_stage="blocked_data", idempotency_key=f"migration-{uuid4()}",
            request_fingerprint="b" * 64, input_fingerprint="c" * 64, requested_model="test-model",
            schema_version="material-analysis-v1", prompt_version="material-analysis-prompt-v1",
            force=False, cache_hit=False, attempts=0, safe_error_code="no_content",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    assert schema_state(engine)["missing"] == []
    apply_schema(engine)
    apply_schema(engine)
    with SessionLocal() as db:
        retained = db.scalar(select(MaterialAnalysisJob).where(MaterialAnalysisJob.id == job_id))
    assert retained is not None
    assert retained.safe_error_code == "no_content"


def test_standalone_migration_command_registers_v2_fk_and_applies_twice_in_disposable_database():
    from app.forecast_v2_models import EvidenceEventVersionV2

    disposable_name = f"test_material_migration_{uuid4().hex[:12]}"
    source_url = engine.url
    admin_engine = create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    migration_url = source_url.set(database=disposable_name)
    target_engine = create_engine(migration_url)
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{disposable_name}"'))
        created = True
        EvidenceEventVersionV2.__table__.create(bind=target_engine)
        environment = os.environ.copy()
        environment["DATABASE_URL"] = migration_url.render_as_string(hide_password=False)
        script = Path(__file__).parents[1] / "scripts" / "migrate_material_analysis.py"
        python = Path(__file__).parents[1] / ".venv" / "bin" / "python"

        check_before = subprocess.run([str(python), str(script), "--check"], env=environment,
                                      capture_output=True, text=True, check=False)
        assert check_before.returncode == 0, check_before.stderr
        assert json.loads(check_before.stdout)["missing"] == ["material_analysis_jobs", "material_analysis_versions"]
        for _ in range(2):
            applied = subprocess.run([str(python), str(script), "--apply"], env=environment,
                                     capture_output=True, text=True, check=False)
            assert applied.returncode == 0, applied.stderr
        check_after = subprocess.run([str(python), str(script), "--check"], env=environment,
                                     capture_output=True, text=True, check=False)
        assert check_after.returncode == 0, check_after.stderr
        assert json.loads(check_after.stdout)["missing"] == []
    finally:
        target_engine.dispose()
        if created:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{disposable_name}" WITH (FORCE)'))
        admin_engine.dispose()
