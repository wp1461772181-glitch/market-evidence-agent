from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.database import Base, SessionLocal, engine
from app.forecast_contract import create_root_contract
from app.forecast_jobs import enqueue_job
from app.forecast_v2 import ForecastDraft
from app.forecast_v2_models import ForecastJobV2, ForecastVersionV2
from app.forecast_worker import run_once


def test_worker_without_processor_records_visible_blocked_state(disposable_database):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol="AAPL", kind="new", idempotency_key=f"worker-blocked-{uuid4()}")
        job_id = job.id

    result = run_once(job_id=job_id, worker_id="blocked-test")

    assert result == {"status": "blocked_data", "job_id": str(job_id), "result_version_id": None}
    with SessionLocal() as db:
        stored = db.get(ForecastJobV2, job_id)
        assert stored.status == "blocked_data"
        assert stored.error_type == "v2_processor_not_configured"
        assert stored.result_version_id is None


def test_worker_injected_processor_publishes_one_research_only_version(disposable_database):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol="MSFT", kind="new", idempotency_key=f"worker-stub-{uuid4()}")
        job_id = job.id
    instant = datetime.now(UTC)
    anchor = date(2026, 9, 23)
    target = create_root_contract(
        anchor_date=anchor,
        anchor_close=100.0,
        price_source="fixture",
        price_version="fixture-v1",
        price_hash="a" * 64,
        price_basis_metadata={
            "basis": "provider_quote_close_v1",
            "provider_behavior_verified": True,
            "adjusted_close_present": False,
            "corporate_actions": (),
        },
    ).as_dict()

    def fixture_processor(claimed):
        assert claimed.id == job_id
        return ForecastDraft(
            target_contract=target,
            decision_at=instant - timedelta(minutes=1),
            market_cutoff_at=instant - timedelta(hours=1),
            price_input_manifest={"fixture": True},
            evidence_version_manifest=[],
            feature_snapshot={},
            baseline_probabilities=None,
            joint_probabilities=None,
            model_status="research_only",
            model_manifest={"fixture_only": True},
        )

    result = run_once(job_id=job_id, worker_id="stub-test", processor=fixture_processor)

    assert result["status"] == "succeeded"
    with SessionLocal() as db:
        stored = db.get(ForecastJobV2, job_id)
        version = db.get(ForecastVersionV2, stored.result_version_id)
        assert version is not None
        assert version.root_id == version.id
        assert version.model_status == "research_only"
        assert version.joint_probabilities is None
