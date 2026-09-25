from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from app.database import Base, SessionLocal, engine
from app.forecast_contract import create_root_contract, xnys_session_close_at
from app.forecast_evaluation_v2 import run_evaluation_batch
from app.forecast_v2_models import ForecastEvaluationV2, ForecastJobV2, ForecastVersionV2
from app.models import MarketPrice, MarketPriceRevision

NOW = datetime(2026, 9, 25, 22, tzinfo=UTC)
SOURCE = "evaluation-v2-fixture"


def _contract(*, anchor_date: date, target_date: date) -> dict:
    contract = create_root_contract(
        anchor_date=anchor_date,
        anchor_close=100.0,
        price_source=SOURCE,
        price_version="fixture-v1",
        price_hash="a" * 64,
        price_basis_metadata={
            "basis": "provider_quote_close_v1",
            "provider_behavior_verified": True,
            "adjusted_close_present": False,
            "corporate_actions": (),
        },
    ).as_dict()
    assert contract["target_end_date"] == target_date.isoformat()
    return contract


def _version(
    *,
    symbol: str,
    contract: dict,
    status: str = "research_only",
    probabilities: dict | None = None,
    root: ForecastVersionV2 | None = None,
) -> tuple[ForecastJobV2, ForecastVersionV2]:
    job = ForecastJobV2(
        id=uuid4(), symbol=symbol, kind="new" if root is None else "manual_revision",
        root_version_id=None if root is None else root.id,
        parent_version_id=None if root is None else root.id,
        source_refs=[], status="succeeded", current_stage="complete", attempts=[],
        idempotency_key=f"evaluation-{uuid4()}", request_fingerprint="a" * 64, lease_epoch=0,
    )
    version = ForecastVersionV2(
        id=uuid4(), root_id=uuid4() if root is None else root.id,
        parent_version_id=None if root is None else root.id, job_id=job.id,
        version_no=1 if root is None else root.version_no + 1, symbol=symbol,
        target_contract=contract, target_contract_hash=hashlib.sha256(repr(contract).encode()).hexdigest(),
        decision_at=NOW - timedelta(days=30), market_cutoff_at=NOW - timedelta(days=30, minutes=1),
        price_input_manifest={}, evidence_version_manifest=[], feature_snapshot={},
        baseline_probabilities=probabilities if status == "baseline_only" else None,
        joint_probabilities=probabilities if status == "experimental_joint" else None,
        model_status=status, model_manifest={}, trigger_type="manual", created_at=NOW - timedelta(days=30),
    )
    if root is None:
        version.root_id = version.id
    return job, version


def _price(*, symbol: str, target_date: date, close: float, revision: int = 1, observed_at: datetime = NOW):
    return MarketPriceRevision(
        symbol=symbol, trading_date=target_date, open=close, high=close, low=close, close=close,
        volume=100, source=SOURCE, revision_number=revision,
        content_hash=hashlib.sha256(f"{symbol}-{target_date}-{revision}-{close}".encode()).hexdigest(),
        available_at=datetime.combine(target_date, datetime.min.time(), UTC) + timedelta(hours=21),
        observed_at=observed_at, is_initial_backfill=False,
    )


def _clean(version_ids: list) -> None:
    with SessionLocal() as db:
        job_ids = list(db.scalars(select(ForecastVersionV2.job_id).where(ForecastVersionV2.id.in_(version_ids))))
        db.execute(delete(ForecastEvaluationV2).where(ForecastEvaluationV2.forecast_version_id.in_(version_ids)))
        # A revision job points back to its parent/root; clear those test-only
        # references before removing the otherwise immutable version records.
        db.execute(
            update(ForecastJobV2)
            .where(ForecastJobV2.id.in_(job_ids))
            .values(root_version_id=None, parent_version_id=None, result_version_id=None)
        )
        db.execute(delete(ForecastVersionV2).where(ForecastVersionV2.id.in_(version_ids)))
        db.execute(delete(ForecastJobV2).where(ForecastJobV2.id.in_(job_ids)))
        db.commit()


@pytest.fixture
def evaluation_rows(disposable_database):
    Base.metadata.create_all(bind=engine)
    versions: list = []
    try:
        yield versions
    finally:
        _clean(versions)


def _insert(*, versions: list, rows: list) -> list:
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()
        saved = [row.id for row in rows if isinstance(row, ForecastVersionV2)]
        versions.extend(saved)
        return saved


def _outcomes(version_id):
    with SessionLocal() as db:
        return list(db.scalars(select(ForecastEvaluationV2).where(ForecastEvaluationV2.forecast_version_id == version_id).order_by(ForecastEvaluationV2.result_version)))


def test_unexpired_target_stays_pending_without_any_score(evaluation_rows):
    anchor = date(2026, 9, 1)
    target = date(2026, 9, 30)
    # Build the valid contract with a 20-session target, then evaluate before it closes.
    contract = _contract(anchor_date=anchor, target_date=date(2026, 9, 30))
    job, version = _version(symbol="EVUNX", contract=contract)
    _insert(versions=evaluation_rows, rows=[job, version])

    with SessionLocal() as db:
        summary = run_evaluation_batch(db=db, evaluated_at=datetime(2026, 9, 30, 19, tzinfo=UTC))
    outcome = _outcomes(version.id)[0]
    assert summary.pending >= 1
    assert outcome.status == "pending"
    assert outcome.actual_label is outcome.brier_score is outcome.log_loss is outcome.direction_correct is None


def test_missing_target_price_remains_pending_until_late_observation(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    contract = _contract(anchor_date=anchor, target_date=target)
    job, version = _version(symbol="EVLATE", contract=contract)
    _insert(versions=evaluation_rows, rows=[job, version])

    after_close = datetime(2026, 9, 1, 22, tzinfo=UTC)
    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=after_close)
    assert _outcomes(version.id)[0].status == "pending"

    late = _price(symbol="EVLATE", target_date=target, close=103.0, observed_at=after_close + timedelta(hours=2))
    _insert(versions=evaluation_rows, rows=[late])
    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=after_close + timedelta(hours=1))
    assert len(_outcomes(version.id)) == 1  # later row is not yet visible
    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=after_close + timedelta(hours=3))
    outcomes = _outcomes(version.id)
    assert [row.status for row in outcomes] == ["pending", "succeeded"]
    assert outcomes[-1].actual_label == "bullish"


def test_holiday_target_fails_closed_without_using_neighbouring_price(evaluation_rows):
    contract = _contract(anchor_date=date(2026, 8, 3), target_date=date(2026, 8, 31))
    contract["target_end_date"] = "2026-09-07"  # Labor Day, not an XNYS session.
    job, version = _version(symbol="EVHOL", contract=contract)
    _insert(versions=evaluation_rows, rows=[job, version, _price(symbol="EVHOL", target_date=date(2026, 9, 4), close=110.0)])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "failed"
    assert outcome.actual_target_close is None
    assert "invalid target contract" in (outcome.error_message or "")


@pytest.mark.parametrize(("close", "label"), [(97.0, "bearish"), (102.0, "neutral"), (103.0, "bullish")])
def test_three_outcomes_and_research_only_never_create_numeric_scores(evaluation_rows, close, label):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol=f"EV{label[:2].upper()}{int(close)}", contract=_contract(anchor_date=anchor, target_date=target))
    _insert(versions=evaluation_rows, rows=[job, version, _price(symbol=version.symbol, target_date=target, close=close)])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "succeeded"
    assert outcome.actual_label == label
    assert (outcome.brier_score, outcome.log_loss, outcome.direction_correct) == (None, None, None)


def test_repeat_is_idempotent_and_visible_price_correction_appends_result_version(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    probabilities = {"bearish": 0.1, "neutral": 0.2, "bullish": 0.7}
    job, version = _version(symbol="EVCOR", contract=_contract(anchor_date=anchor, target_date=target), status="experimental_joint", probabilities=probabilities)
    first = _price(symbol="EVCOR", target_date=target, close=103.0, observed_at=datetime(2026, 9, 1, 22, tzinfo=UTC))
    _insert(versions=evaluation_rows, rows=[job, version, first])

    with SessionLocal() as db:
        one = run_evaluation_batch(db=db, evaluated_at=NOW)
    with SessionLocal() as db:
        two = run_evaluation_batch(db=db, evaluated_at=NOW)
    assert one.inserted == 1 and two.inserted == 0 and two.unchanged >= 1
    assert len(_outcomes(version.id)) == 1

    correction = _price(symbol="EVCOR", target_date=target, close=97.0, revision=2, observed_at=NOW + timedelta(hours=1))
    _insert(versions=evaluation_rows, rows=[correction])
    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW + timedelta(hours=2))
    outcomes = _outcomes(version.id)
    assert [row.result_version for row in outcomes] == [1, 2]
    assert [row.actual_label for row in outcomes] == ["bullish", "bearish"]
    assert outcomes[-1].brier_score is not None
    assert outcomes[-1].direction_correct is False


def test_root_and_child_same_target_evaluate_independently_without_forecast_mutation(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    contract = _contract(anchor_date=anchor, target_date=target)
    root_job, root = _version(symbol="EVCHAIN", contract=contract)
    child_job, child = _version(symbol="EVCHAIN", contract=contract, root=root)
    price = _price(symbol="EVCHAIN", target_date=target, close=102.0)
    _insert(versions=evaluation_rows, rows=[root_job, root])
    _insert(versions=evaluation_rows, rows=[child_job, child, price])

    before = (root.target_contract, root.baseline_probabilities, child.target_contract, child.joint_probabilities)
    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)
        loaded_root = db.get(ForecastVersionV2, root.id)
        loaded_child = db.get(ForecastVersionV2, child.id)
        assert (loaded_root.target_contract, loaded_root.baseline_probabilities, loaded_child.target_contract, loaded_child.joint_probabilities) == before
    assert [_outcomes(item.id)[0].actual_label for item in (root, child)] == ["neutral", "neutral"]


def test_future_price_revision_is_not_used_until_its_observed_at(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol="EVFUT", contract=_contract(anchor_date=anchor, target_date=target))
    old = _price(symbol="EVFUT", target_date=target, close=103.0, observed_at=datetime(2026, 9, 1, 22, tzinfo=UTC))
    future = _price(symbol="EVFUT", target_date=target, close=97.0, revision=2, observed_at=NOW + timedelta(days=1))
    _insert(versions=evaluation_rows, rows=[job, version, old, future])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)
    first = _outcomes(version.id)[0]
    assert first.actual_label == "bullish"
    assert first.price_input_version["revision_number"] == 1
    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW + timedelta(days=2))
    assert [row.actual_label for row in _outcomes(version.id)] == ["bullish", "bearish"]


def test_experimental_version_never_scores_from_legacy_baseline_probabilities(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(
        symbol="EVNOJOINT",
        contract=_contract(anchor_date=anchor, target_date=target),
        status="experimental_joint",
        probabilities={"bearish": 0.1, "neutral": 0.2, "bullish": 0.7},
    )
    # A historical baseline does not prove the V2 joint forecast was scoreable.
    version.joint_probabilities = None
    version.baseline_probabilities = {"bearish": 0.1, "neutral": 0.2, "bullish": 0.7}
    _insert(
        versions=evaluation_rows,
        rows=[job, version, _price(symbol="EVNOJOINT", target_date=target, close=103.0)],
    )

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "failed"
    assert (outcome.brier_score, outcome.log_loss, outcome.direction_correct) == (None, None, None)
    assert "no probability vector" in (outcome.error_message or "")


def test_revision_label_is_not_available_before_its_provider_availability(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol="EVRECV", contract=_contract(anchor_date=anchor, target_date=target))
    close_at = xnys_session_close_at(target)
    revision = _price(
        symbol="EVRECV", target_date=target, close=103.0, observed_at=close_at + timedelta(minutes=1)
    )
    revision.available_at = close_at + timedelta(minutes=5)
    _insert(versions=evaluation_rows, rows=[job, version, revision])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=close_at + timedelta(hours=1))
    outcome = _outcomes(version.id)[0]
    assert outcome.label_available_at == close_at + timedelta(minutes=5)


def test_legacy_price_fetched_before_target_close_does_not_mature_target(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol="EVLEG", contract=_contract(anchor_date=anchor, target_date=target))
    close_at = xnys_session_close_at(target)
    legacy = MarketPrice(
        symbol="EVLEG", trading_date=target, open=103.0, high=103.0, low=103.0, close=103.0,
        volume=100, source=SOURCE, fetched_at=close_at - timedelta(seconds=1),
    )
    _insert(versions=evaluation_rows, rows=[job, version, legacy])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "pending"
    assert outcome.actual_target_close is None


def test_revision_written_before_target_close_does_not_mature_target(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol="EVEARLY", contract=_contract(anchor_date=anchor, target_date=target))
    close_at = xnys_session_close_at(target)
    early = _price(
        symbol="EVEARLY", target_date=target, close=103.0, observed_at=close_at - timedelta(seconds=1)
    )
    early.available_at = close_at - timedelta(seconds=1)
    _insert(versions=evaluation_rows, rows=[job, version, early])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "pending"
    assert outcome.actual_target_close is None
    assert outcome.actual_label is None
