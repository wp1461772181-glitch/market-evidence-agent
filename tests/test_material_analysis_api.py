from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from app.database import Base, SessionLocal, engine
from app.forecast_v2_models import EvidenceEventVersionV2
from app.material_analysis_models import MaterialAnalysisJob, MaterialAnalysisVersion
from app.models import SecFilingInventory, UploadedEvidence


@pytest.fixture(autouse=True)
def material_tables(disposable_database):
    _CREATED_SOURCES.clear()
    Base.metadata.create_all(bind=engine)
    yield
    with SessionLocal() as db:
        for source_type, source_id in _CREATED_SOURCES:
            db.execute(update(MaterialAnalysisJob).where(MaterialAnalysisJob.source_type == source_type,
                                                          MaterialAnalysisJob.source_id == source_id)
                       .values(result_analysis_id=None))
            db.execute(delete(MaterialAnalysisVersion).where(MaterialAnalysisVersion.source_type == source_type,
                                                               MaterialAnalysisVersion.source_id == source_id))
            db.execute(delete(MaterialAnalysisJob).where(MaterialAnalysisJob.source_type == source_type,
                                                           MaterialAnalysisJob.source_id == source_id))
            db.execute(delete(EvidenceEventVersionV2).where(EvidenceEventVersionV2.source_type == source_type,
                                                              EvidenceEventVersionV2.source_id == source_id))
            if source_type == "uploaded_media":
                db.execute(delete(UploadedEvidence).where(UploadedEvidence.id == source_id))
            else:
                db.execute(delete(SecFilingInventory).where(SecFilingInventory.id == source_id))
        db.commit()


_CREATED_SOURCES = []


def _upload(symbol="AAPL", title="API material"):
    content = f"Material for {title}".encode()
    with SessionLocal() as db:
        row = UploadedEvidence(
            symbol=symbol, title=title, source_url="https://example.test/material",
            published_at=datetime(2026, 9, 10, tzinfo=UTC), observed_at=datetime(2026, 9, 10, 1, tzinfo=UTC),
            credibility_stars=3, credibility_reason="fixture", impact_severity="low", filename="source.txt",
            content_sha256=hashlib.sha256(content).hexdigest(), raw_content=content,
            content_text=content.decode(), status="unconfirmed",
        )
        db.add(row)
        db.commit()
        _CREATED_SOURCES.append(("uploaded_media", row.id))
        return row.id


def test_api_catalog_pagination_and_get_routes_are_read_only(client):
    first = _upload(symbol="ZZTST", title="First")
    _upload(symbol="ZZTST", title="Second")
    with SessionLocal() as db:
        before = (db.scalar(select(func.count()).select_from(MaterialAnalysisJob)),
                  db.scalar(select(func.count()).select_from(MaterialAnalysisVersion)))
    response = client.get("/v3/materials", params={"symbol": "ZZTST", "source_type": "uploaded_media", "limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 1
    assert body["items"][0]["symbol"] == "ZZTST"
    assert "latest_job" in body["items"][0]
    assert client.get(f"/v3/materials/uploaded_media/{first}/analyses").status_code == 200
    assert client.get(f"/v3/material-analyses/{uuid4()}").status_code == 404
    with SessionLocal() as db:
        after = (db.scalar(select(func.count()).select_from(MaterialAnalysisJob)),
                 db.scalar(select(func.count()).select_from(MaterialAnalysisVersion)))
    assert after == before


def test_api_job_is_queued_and_idempotency_conflict_is_409(client):
    source_id = _upload(title="Queue me")
    response = client.post(f"/v3/materials/uploaded_media/{source_id}/analysis-jobs",
                           json={"idempotency_key": "api-idempotency", "force": False})
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"
    replay = client.post(f"/v3/materials/uploaded_media/{source_id}/analysis-jobs",
                         json={"idempotency_key": "api-idempotency", "force": False})
    assert replay.status_code == 202
    assert replay.json()["job_id"] == job["job_id"]
    conflict = client.post(f"/v3/materials/uploaded_media/{source_id}/analysis-jobs",
                           json={"idempotency_key": "api-idempotency", "force": True})
    assert conflict.status_code == 409
    assert client.get(f"/v3/material-analysis-jobs/{job['job_id']}").json()["status"] == "queued"


def test_api_returns_actionable_blocked_status_for_sec_listing_without_text(client):
    with SessionLocal() as db:
        filing = SecFilingInventory(
            symbol="AAPL", cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
            form="10-Q", filed_at=datetime(2026, 9, 10, tzinfo=UTC).date(), accepted_at="2026-09-10T12:00:00Z",
            primary_document="q.htm", source_url="https://www.sec.gov/example/no-text.htm", source="sec-edgar",
            review_status="pending_review", human_review_note=None, reviewed_at=None,
            observed_at=datetime(2026, 9, 10, tzinfo=UTC), content_status="not_fetched", content_observed_at=None,
            content_excerpt=None, content_excerpt_sha256=None, content_truncated=False, content_error=None,
        )
        db.add(filing)
        db.commit()
        _CREATED_SOURCES.append(("official_filing", filing.id))
        filing_id = filing.id
    response = client.post(f"/v3/materials/official_filing/{filing_id}/analysis-jobs",
                           json={"idempotency_key": "blocked-no-text"})
    assert response.status_code == 202
    assert response.json()["status"] == "blocked_data"
    assert response.json()["analysis_id"] is None
    status_response = client.get(f"/v3/material-analysis-jobs/{response.json()['job_id']}")
    assert status_response.json()["safe_error_code"] == "no_content"


def test_material_routes_report_required_migration_without_mutating_startup(client):
    from app.material_analysis_models import MATERIAL_ANALYSIS_TABLES

    MATERIAL_ANALYSIS_TABLES[0].metadata.drop_all(bind=engine, tables=list(MATERIAL_ANALYSIS_TABLES), checkfirst=True)
    try:
        from app.forecast_worker import run_once

        assert run_once(worker_id="pre-migration-worker", job_id=uuid4())["status"] == "idle"
        response = client.get("/v3/materials")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "migration_required"
    finally:
        MATERIAL_ANALYSIS_TABLES[0].metadata.create_all(bind=engine, tables=list(MATERIAL_ANALYSIS_TABLES), checkfirst=True)
