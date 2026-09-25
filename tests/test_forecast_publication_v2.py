from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.database import Base, SessionLocal, engine
from app.forecast_contract import create_root_contract
from app.forecast_jobs import claim_next_job, enqueue_job
from app.forecast_v2 import ForecastDraft, ForecastPublicationError, publish_forecast_version
from app.forecast_v2_models import ForecastJobV2, ForecastVersionV2


@pytest.fixture(autouse=True)
def v2_tables(disposable_database):
    Base.metadata.create_all(bind=engine)


def _draft(*, evidence=None, market=None, target=None, now=None):
    instant = now or datetime.now(UTC)
    anchor = date(2026, 9, 23)
    valid_target = create_root_contract(
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
    return ForecastDraft(
        target_contract=target or valid_target,
        decision_at=instant - timedelta(minutes=1),
        market_cutoff_at=instant - timedelta(hours=1),
        price_input_manifest=market or {"close": 100.0, "source": "fixture"},
        evidence_version_manifest=evidence or [],
        feature_snapshot={"momentum_5d": 0.01},
        baseline_probabilities={"bearish": 0.2, "neutral": 0.3, "bullish": 0.5},
        joint_probabilities=None,
        model_status="research_only",
        model_manifest={"status": "test_stub", "not_for_ui": True},
    )


def _claim(db, *, kind, symbol="AAPL", root=None, parent=None):
    job = enqueue_job(
        db=db, symbol=symbol, kind=kind, idempotency_key=f"publication-{uuid4()}",
        root_version_id=root, parent_version_id=parent,
    )
    claimed = claim_next_job(db=db, worker_id="test-worker", job_id=job.id)
    assert claimed is not None
    return claimed


def test_root_and_branch_publish_atomically_without_changing_parent():
    with SessionLocal() as db:
        root_job = _claim(db, kind="new")
        root, created = publish_forecast_version(
            db=db, job_id=root_job.id, worker_id="test-worker", lease_epoch=root_job.lease_epoch,
            draft=_draft(),
        )
        assert created and root.root_id == root.id and root.version_no == 1
        assert db.get(ForecastJobV2, root_job.id).result_version_id == root.id
        original_contract = dict(root.target_contract)

        first_child_job = _claim(db, kind="manual_revision", root=root.id, parent=root.id)
        child, child_created = publish_forecast_version(
            db=db, job_id=first_child_job.id, worker_id="test-worker",
            lease_epoch=first_child_job.lease_epoch,
            draft=_draft(evidence=[{"id": "event-1"}]),
        )
        assert child_created and child.version_no == 2
        assert child.parent_version_id == root.id and child.root_id == root.id
        assert child.target_contract == original_contract
        assert db.get(ForecastVersionV2, root.id).target_contract == original_contract

        branch_job = _claim(db, kind="manual_revision", root=root.id, parent=root.id)
        branch, branch_created = publish_forecast_version(
            db=db, job_id=branch_job.id, worker_id="test-worker",
            lease_epoch=branch_job.lease_epoch,
            draft=_draft(evidence=[{"id": "event-2"}]),
        )
        assert branch_created and branch.version_no == 3
        assert branch.parent_version_id == root.id


def test_same_root_and_same_effective_input_returns_existing_version():
    with SessionLocal() as db:
        root_job = _claim(db, kind="new", symbol="MSFT")
        root, _ = publish_forecast_version(
            db=db, job_id=root_job.id, worker_id="test-worker", lease_epoch=root_job.lease_epoch,
            draft=_draft(),
        )
        first_job = _claim(db, kind="manual_revision", symbol="MSFT", root=root.id, parent=root.id)
        first, _ = publish_forecast_version(
            db=db, job_id=first_job.id, worker_id="test-worker", lease_epoch=first_job.lease_epoch,
            draft=_draft(evidence=[{"id": "event-3"}]),
        )
        repeated_job = _claim(db, kind="manual_revision", symbol="MSFT", root=root.id, parent=root.id)
        repeated, created = publish_forecast_version(
            db=db, job_id=repeated_job.id, worker_id="test-worker", lease_epoch=repeated_job.lease_epoch,
            draft=_draft(evidence=[{"id": "event-3"}]),
        )
        assert not created and repeated.id == first.id
        assert db.get(ForecastJobV2, repeated_job.id).status == "succeeded_no_change"


def test_automatic_revision_window_is_measured_from_root_decision_time():
    instant = datetime.now(UTC)
    with SessionLocal() as db:
        root_job = _claim(db, kind="new", symbol="AAPL")
        root, _ = publish_forecast_version(
            db=db, job_id=root_job.id, worker_id="test-worker", lease_epoch=root_job.lease_epoch,
            draft=_draft(now=instant), now=instant,
        )
        root.created_at = instant - timedelta(hours=80)
        root.decision_at = instant - timedelta(hours=71)
        db.commit()
        child_job = _claim(db, kind="automatic_revision", symbol="AAPL", root=root.id, parent=root.id)
        child, created = publish_forecast_version(
            db=db, job_id=child_job.id, worker_id="test-worker", lease_epoch=child_job.lease_epoch,
            draft=_draft(evidence=[{"id": "event-auto"}], now=instant), now=instant,
        )

    assert created and child.parent_version_id == root.id


def test_changed_target_is_rejected_without_publishing_a_child():
    with SessionLocal() as db:
        root_job = _claim(db, kind="new", symbol="NVDA")
        root, _ = publish_forecast_version(
            db=db, job_id=root_job.id, worker_id="test-worker", lease_epoch=root_job.lease_epoch,
            draft=_draft(),
        )
        child_job = _claim(db, kind="manual_revision", symbol="NVDA", root=root.id, parent=root.id)
        changed = dict(root.target_contract, anchor_close=101.0)
        with pytest.raises(ForecastPublicationError, match="fixed target") as exc:
            publish_forecast_version(
                db=db, job_id=child_job.id, worker_id="test-worker", lease_epoch=child_job.lease_epoch,
                draft=_draft(target=changed),
            )
        assert exc.value.code == "target_mismatch"
        assert db.query(ForecastVersionV2).filter_by(root_id=root.id).count() == 1
