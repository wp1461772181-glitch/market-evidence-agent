from datetime import UTC, datetime, timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.database import Base, SessionLocal, engine
from app.forecast_jobs import (
    ForecastJobError,
    claim_next_job,
    enqueue_job,
    finish_job,
    heartbeat_job,
)
from app.forecast_v2_models import ForecastJobV2


@pytest.fixture(autouse=True)
def v2_tables(disposable_database):
    Base.metadata.create_all(bind=engine)


def test_idempotency_returns_same_job_and_conflicting_body_is_rejected():
    key = f"new-{uuid4()}"
    with SessionLocal() as db:
        first = enqueue_job(db=db, symbol=" aapl ", kind="new", idempotency_key=key)
        repeated = enqueue_job(db=db, symbol="AAPL", kind="new", idempotency_key=key)
        assert first.id == repeated.id
        assert first.status == "queued"
        with pytest.raises(ForecastJobError, match="another request") as exc:
            enqueue_job(db=db, symbol="MSFT", kind="new", idempotency_key=key)
        assert exc.value.code == "conflict"


def test_concurrent_duplicate_admission_creates_only_one_durable_job():
    key = f"parallel-{uuid4()}"
    barrier = Barrier(2)

    def submit():
        with SessionLocal() as db:
            barrier.wait(timeout=5)
            return enqueue_job(db=db, symbol="GOOGL", kind="new", idempotency_key=key).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0] == results[1]
    with SessionLocal() as db:
        assert db.query(ForecastJobV2).filter_by(idempotency_key=key).count() == 1


def test_expired_claim_is_recovered_and_old_worker_cannot_finish():
    start = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        created = enqueue_job(db=db, symbol="MSFT", kind="new", idempotency_key=f"lease-{uuid4()}")
        job_id = created.id
        first = claim_next_job(db=db, worker_id="worker-1", job_id=job_id, now=start)
        assert first is not None and first.id == job_id
        old_epoch = first.lease_epoch

    with SessionLocal() as db:
        reclaimed = claim_next_job(db=db, worker_id="worker-2", job_id=job_id, now=start + timedelta(minutes=6))
        assert reclaimed is not None and reclaimed.id == job_id
        assert reclaimed.lease_epoch == old_epoch + 1
        assert not heartbeat_job(
            db=db, job_id=job_id, worker_id="worker-1", lease_epoch=old_epoch,
            stage="publishing", now=start + timedelta(minutes=6),
        )
        assert not finish_job(
            db=db, job_id=job_id, worker_id="worker-1", lease_epoch=old_epoch,
            status="failed", now=start + timedelta(minutes=6),
        )
        assert finish_job(
            db=db, job_id=job_id, worker_id="worker-2", lease_epoch=reclaimed.lease_epoch,
            status="blocked_data", error_type="model_unavailable",
            now=start + timedelta(minutes=6),
        )
        stored = db.get(ForecastJobV2, job_id)
        assert stored.status == "blocked_data"
        assert stored.error_type == "model_unavailable"


def test_retry_wait_is_durable_and_respects_due_time():
    start = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)
    with SessionLocal() as db:
        created = enqueue_job(db=db, symbol="NVDA", kind="new", idempotency_key=f"retry-{uuid4()}")
        job_id = created.id
        claimed = claim_next_job(db=db, worker_id="worker-1", job_id=job_id, now=start)
        assert claimed is not None and claimed.id == job_id
        assert finish_job(
            db=db, job_id=job_id, worker_id="worker-1", lease_epoch=claimed.lease_epoch,
            status="failed", error_type="temporary_network", retryable=True, now=start,
        )
    with SessionLocal() as db:
        assert claim_next_job(db=db, worker_id="worker-2", job_id=job_id, now=start + timedelta(seconds=59)) is None
        due = claim_next_job(db=db, worker_id="worker-2", job_id=job_id, now=start + timedelta(minutes=1))
        assert due is not None and due.id == job_id
        assert due.lease_epoch == claimed.lease_epoch + 1
