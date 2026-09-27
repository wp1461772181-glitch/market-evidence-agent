from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.database import Base, SessionLocal, engine
from app.forecast_contract import create_root_contract
from app.forecast_evaluation_v2 import JEV_LOG_LOSS_EPSILON, run_evaluation_batch
from app.forecast_v2_api import _evaluations_payload
from app.forecast_v2_models import ForecastEvaluationV2, ForecastJobV2, ForecastVersionV2
from app.models import MarketPriceRevision

NOW = datetime(2026, 9, 25, 22, tzinfo=UTC)
PRICE_SOURCE = "jev-evaluation-fixture"


def _forecast_rows(symbol: str, *, status: str, probabilities: dict | None):
    target = date(2026, 8, 31)
    contract = create_root_contract(
        anchor_date=date(2026, 8, 3),
        anchor_close=100.0,
        price_source=PRICE_SOURCE,
        price_version="fixture-v1",
        price_hash="c" * 64,
        price_basis_metadata={
            "basis": "provider_quote_close_v1",
            "provider_behavior_verified": True,
            "adjusted_close_present": False,
            "corporate_actions": (),
        },
    ).as_dict()
    assert contract["target_end_date"] == target.isoformat()
    job_id = uuid4()
    version_id = uuid4()
    job = ForecastJobV2(
        id=job_id,
        symbol=symbol,
        kind="new",
        root_version_id=None,
        parent_version_id=None,
        source_refs=[],
        status="succeeded",
        current_stage="complete",
        attempts=[],
        idempotency_key=f"jev-evaluation-{uuid4()}",
        request_fingerprint="d" * 64,
        lease_epoch=0,
    )
    version = ForecastVersionV2(
        id=version_id,
        root_id=version_id,
        parent_version_id=None,
        job_id=job_id,
        version_no=1,
        symbol=symbol,
        target_contract=contract,
        target_contract_hash="e" * 64,
        decision_at=NOW - timedelta(days=30),
        market_cutoff_at=NOW - timedelta(days=30, minutes=1),
        price_input_manifest={},
        evidence_version_manifest=[],
        feature_snapshot={},
        baseline_probabilities=None,
        joint_probabilities=None,
        model_status=status,
        model_manifest={
            "time_mode": "observed",
            "decision_provider": {
                "provider": "openrouter",
                "actual_model": "typesafe/jev-1.13",
                "question_version": "jev-direction-v1",
            },
        } if status == "experimental_jev" else {"time_mode": "observed"},
        decision_probabilities=probabilities,
        research_brief={"schema_version": "research-brief-v1"} if status == "experimental_jev" else None,
        trigger_type="manual",
        created_at=NOW - timedelta(days=30),
    )
    price = MarketPriceRevision(
        symbol=symbol,
        trading_date=target,
        open=103.0,
        high=103.0,
        low=103.0,
        close=103.0,
        volume=100,
        source=PRICE_SOURCE,
        revision_number=1,
        content_hash="f" * 64,
        available_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        observed_at=datetime(2026, 9, 1, 22, tzinfo=UTC),
        is_initial_backfill=False,
    )
    return job, version, price


@pytest.fixture
def jev_rows(disposable_database):
    Base.metadata.create_all(bind=engine)
    version_ids: list = []
    job_ids: list = []
    symbols: list = []
    try:
        yield version_ids, job_ids, symbols
    finally:
        with SessionLocal() as db:
            db.execute(delete(MarketPriceRevision).where(MarketPriceRevision.symbol.in_(symbols)))
            db.execute(delete(ForecastEvaluationV2).where(ForecastEvaluationV2.forecast_version_id.in_(version_ids)))
            db.execute(
                delete(ForecastVersionV2)
                .where(ForecastVersionV2.id.in_(version_ids))
                .execution_options(synchronize_session=False)
            )
            db.execute(delete(ForecastJobV2).where(ForecastJobV2.id.in_(job_ids)))
            db.commit()


def _insert(jev_rows, rows):
    version_ids, job_ids, symbols = jev_rows
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()
        for row in rows:
            if isinstance(row, ForecastVersionV2):
                version_ids.append(row.id)
            elif isinstance(row, ForecastJobV2):
                job_ids.append(row.id)
            elif isinstance(row, MarketPriceRevision):
                symbols.append(row.symbol)


def _outcome(version_id):
    with SessionLocal() as db:
        return db.query(ForecastEvaluationV2).filter_by(forecast_version_id=version_id).one()


def test_jev_scores_decision_probabilities_and_zero_actual_probability(jev_rows):
    job, version, price = _forecast_rows(
        "JEVZERO",
        status="experimental_jev",
        probabilities={"bearish": 0.2, "neutral": 0.8, "bullish": 0.0},
    )
    _insert(jev_rows, [job, version, price])

    with SessionLocal() as db:
        summary = run_evaluation_batch(db=db, symbol="JEVZERO", evaluated_at=NOW)

    result = _outcome(version.id)
    assert summary.inserted == 1
    assert result.status == "succeeded" and result.actual_label == "bullish"
    assert result.brier_score == pytest.approx(1.68)
    assert result.log_loss == pytest.approx(-math.log(JEV_LOG_LOSS_EPSILON))
    assert result.direction_correct is False


@pytest.mark.parametrize(
    "probabilities",
    [
        {"bearish": True, "neutral": 0.2, "bullish": 0.8},
        {"bearish": "0.1", "neutral": 0.2, "bullish": 0.7},
        {"bearish": 0.1, "neutral": 0.2, "bullish": 0.700002},
    ],
)
def test_jev_rejects_boolean_strings_and_probability_sum_outside_tolerance(jev_rows, probabilities):
    job, version, price = _forecast_rows("JEVBAD", status="experimental_jev", probabilities=probabilities)
    _insert(jev_rows, [job, version, price])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)

    result = _outcome(version.id)
    assert result.status == "failed"
    assert result.brier_score is None and result.log_loss is None and result.direction_correct is None


def test_jev_accepts_provider_sum_tolerance_and_research_only_stays_unscored(jev_rows):
    job, version, price = _forecast_rows(
        "JEVTOL",
        status="experimental_jev",
        probabilities={"bearish": 0.1, "neutral": 0.2, "bullish": 0.7000005},
    )
    _insert(jev_rows, [job, version, price])
    research_job, research, research_price = _forecast_rows(
        "JEVRES", status="research_only", probabilities=None
    )
    _insert(jev_rows, [research_job, research, research_price])

    with SessionLocal() as db:
        run_evaluation_batch(db=db, evaluated_at=NOW)

    scored = _outcome(version.id)
    unscored = _outcome(research.id)
    assert scored.status == "succeeded" and scored.log_loss is not None
    assert unscored.status == "succeeded" and unscored.actual_label == "bullish"
    assert (unscored.brier_score, unscored.log_loss, unscored.direction_correct) == (None, None, None)


def test_model_cohorts_separate_question_versions_and_count_one_root_per_cohort():
    job, root, _ = _forecast_rows(
        "JEVCOHORT", status="experimental_jev", probabilities={"bearish": 0.2, "neutral": 0.3, "bullish": 0.5}
    )
    _, same_cohort_revision, _ = _forecast_rows(
        "JEVCOHORT", status="experimental_jev", probabilities={"bearish": 0.2, "neutral": 0.4, "bullish": 0.4}
    )
    _, other_question_revision, _ = _forecast_rows(
        "JEVCOHORT", status="experimental_jev", probabilities={"bearish": 0.3, "neutral": 0.4, "bullish": 0.3}
    )
    same_cohort_revision.root_id = root.id
    same_cohort_revision.parent_version_id = root.id
    same_cohort_revision.version_no = 2
    other_question_revision.root_id = root.id
    other_question_revision.parent_version_id = same_cohort_revision.id
    other_question_revision.version_no = 3
    other_question_revision.model_manifest["decision_provider"]["question_version"] = "jev-direction-v2"
    evaluations = [
        ForecastEvaluationV2(
            forecast_version_id=version.id,
            target_contract_hash=version.target_contract_hash,
            actual_target_close=103.0,
            actual_label="bullish",
            brier_score=0.5,
            log_loss=0.7,
            direction_correct=True,
            status="succeeded",
            result_version=1,
        )
        for version in (root, same_cohort_revision, other_question_revision)
    ]

    payload = _evaluations_payload(
        symbol="JEVCOHORT",
        versions=[root, same_cohort_revision, other_question_revision],
        evaluations=evaluations,
    )

    assert payload["cohorts"]["prospective"]["sample"]["root_denominator"] == 1
    assert len(payload["model_cohorts"]) == 2
    first, second = payload["model_cohorts"]
    assert first["actual_model"] == "typesafe/jev-1.13"
    assert first["question_version"] == "jev-direction-v1"
    assert first["sample"]["root_denominator"] == 1
    assert first["roots"][0]["versions"][0]["id"] == str(same_cohort_revision.id)
    assert second["question_version"] == "jev-direction-v2"
    assert second["sample"]["root_denominator"] == 1
    assert second["roots"][0]["versions"][0]["id"] == str(other_question_revision.id)
    assert "highest version_no" in payload["model_cohort_selection_rule"]
