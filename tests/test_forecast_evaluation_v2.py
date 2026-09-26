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
from app.market_data import CorporateAction, DailyPrice, MarketDataError, MarketDataFetchResult, PriceBasisMetadata, YAHOO_SOURCE
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


def _yahoo_contract(*, anchor_date: date, target_date: date) -> dict:
    contract = create_root_contract(
        anchor_date=anchor_date,
        anchor_close=100.0,
        price_source=YAHOO_SOURCE,
        price_version="fixture-yahoo-v1",
        price_hash="b" * 64,
        price_basis_metadata={
            "basis": "provider_quote_close_v1",
            "provider_behavior_verified": True,
            "adjusted_close_present": True,
            "corporate_actions": (),
        },
    ).as_dict()
    assert contract["target_end_date"] == target_date.isoformat()
    return contract


class _YahooProvider:
    def __init__(self, result: MarketDataFetchResult | Exception):
        self.result = result
        self.calls: list[tuple[str, date, date]] = []

    def fetch_daily_prices_with_metadata(self, symbol: str, start_date: date, end_date: date):
        self.calls.append((symbol, start_date, end_date))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _yahoo_result(
    *,
    symbol: str,
    target_date: date,
    close: float,
    actions=(),
    actions_available: bool = True,
    include_anchor: bool = True,
    anchor_close: float = 100.0,
):
    target_row = DailyPrice(
        symbol=symbol,
        trading_date=target_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
    )
    anchor_date = date(2026, 8, 3)
    anchor_rows = (
        DailyPrice(
            symbol=symbol,
            trading_date=anchor_date,
            open=anchor_close,
            high=anchor_close,
            low=anchor_close,
            close=anchor_close,
            volume=100,
        ),
    ) if include_anchor else ()
    return MarketDataFetchResult(
        prices=(*anchor_rows, target_row),
        price_basis=PriceBasisMetadata(
            adjusted_close_present=True,
            provider_behavior_verified=True,
            corporate_actions_available=actions_available,
            corporate_actions_response_shape="events_object" if actions_available else "events_omitted",
            corporate_actions=tuple(actions),
            verification_notes=("fixture price-basis response",),
        ),
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


def test_due_yahoo_target_rechecks_interval_and_never_uses_old_unreviewed_row(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    contract = _yahoo_contract(anchor_date=anchor, target_date=target)
    job, version = _version(symbol="YSAFE", contract=contract)
    # This old row is deliberately bearish. The fresh Yahoo response is
    # bullish, proving the evaluator does not directly trust pre-P7 storage.
    old = MarketPriceRevision(
        symbol="YSAFE", trading_date=target, open=97.0, high=97.0, low=97.0, close=97.0,
        volume=100, source=YAHOO_SOURCE, revision_number=1,
        content_hash="f" * 64, available_at=xnys_session_close_at(target), observed_at=NOW,
        is_initial_backfill=False,
    )
    _insert(versions=evaluation_rows, rows=[job, version, old])
    provider = _YahooProvider(_yahoo_result(symbol="YSAFE", target_date=target, close=103.0))

    with SessionLocal() as db:
        summary = run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "succeeded"
    assert outcome.actual_target_close == 103.0
    assert outcome.actual_label == "bullish"
    assert outcome.label_available_at <= summary.evaluated_at
    assert outcome.price_input_version["kind"] == "safe_maturity_yahoo_refresh_v1"
    quote = outcome.price_input_version["target_quote"]
    assert quote["source"] == YAHOO_SOURCE
    assert quote["content_hash"] != old.content_hash
    with SessionLocal() as db:
        stored = db.get(MarketPriceRevision, quote["id"])
    assert stored is not None and (stored.close, stored.content_hash) == (103.0, quote["content_hash"])
    assert provider.calls == [("YSAFE", anchor, target)]


def test_historical_cutoff_rejects_a_yahoo_receipt_that_arrives_later(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol="YLATE", contract=_yahoo_contract(anchor_date=anchor, target_date=target))
    _insert(versions=evaluation_rows, rows=[job, version])
    provider = _YahooProvider(_yahoo_result(symbol="YLATE", target_date=target, close=103.0))

    with SessionLocal() as db:
        summary = run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW + timedelta(minutes=1),
        )
    outcome = _outcomes(version.id)[0]
    assert summary.evaluated_at == NOW
    assert outcome.status == "pending"
    assert outcome.actual_target_close is None
    assert "after the evaluation cutoff" in (outcome.error_message or "")
    with SessionLocal() as db:
        assert db.scalar(
            select(MarketPriceRevision).where(
                MarketPriceRevision.symbol == "YLATE",
                MarketPriceRevision.trading_date == target,
                MarketPriceRevision.source == YAHOO_SOURCE,
            )
        ) is None


def test_due_yahoo_split_blocks_the_target_interval(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    split_job, split_version = _version(symbol="YSPLT", contract=_yahoo_contract(anchor_date=anchor, target_date=target))
    _insert(versions=evaluation_rows, rows=[split_job, split_version])
    split_provider = _YahooProvider(
        _yahoo_result(
            symbol="YSPLT",
            target_date=target,
            close=103.0,
            actions=(CorporateAction(kind="split", effective_date=date(2026, 8, 17), known=True),),
        )
    )
    with SessionLocal() as db:
        run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: split_provider,
            now_factory=lambda: NOW,
        )
    split_outcome = _outcomes(split_version.id)[0]
    assert split_outcome.status == "blocked_price"
    assert "corporate_action_unsupported" in (split_outcome.error_message or "")


def test_due_yahoo_missing_action_metadata_blocks_the_target_interval(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol="YNOMD", contract=_yahoo_contract(anchor_date=anchor, target_date=target))
    _insert(versions=evaluation_rows, rows=[job, version])
    provider = _YahooProvider(_yahoo_result(symbol="YNOMD", target_date=target, close=103.0, actions_available=False))

    with SessionLocal() as db:
        run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "blocked_price"
    assert "corporate actions unavailable" in (outcome.error_message or "")


@pytest.mark.parametrize(
    ("include_anchor", "anchor_close", "message"),
    [
        (False, 100.0, "anchor-day quote is missing or ambiguous"),
        (True, 99.99, "anchor-day close differs from the frozen contract"),
    ],
)
def test_due_yahoo_anchor_quote_must_match_the_frozen_contract(
    evaluation_rows, include_anchor, anchor_close, message
):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(
        symbol=f"YANCH{int(include_anchor)}{str(anchor_close).replace('.', '')}",
        contract=_yahoo_contract(anchor_date=anchor, target_date=target),
    )
    _insert(versions=evaluation_rows, rows=[job, version])
    provider = _YahooProvider(
        _yahoo_result(
            symbol=version.symbol,
            target_date=target,
            close=103.0,
            include_anchor=include_anchor,
            anchor_close=anchor_close,
        )
    )

    with SessionLocal() as db:
        run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    outcome = _outcomes(version.id)[0]
    assert outcome.status == "blocked_price"
    assert message in (outcome.error_message or "")


def test_due_yahoo_refresh_failure_stays_blocked_until_a_later_safe_retry(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    job, version = _version(symbol="YRETRY", contract=_yahoo_contract(anchor_date=anchor, target_date=target))
    _insert(versions=evaluation_rows, rows=[job, version])
    provider = _YahooProvider(MarketDataError("fixture timeout"))

    with SessionLocal() as db:
        run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    assert _outcomes(version.id)[0].status == "blocked_price"

    provider.result = _yahoo_result(symbol="YRETRY", target_date=target, close=103.0)
    with SessionLocal() as db:
        run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    assert [item.status for item in _outcomes(version.id)] == ["blocked_price", "succeeded"]


def test_same_root_children_share_one_safe_yahoo_review_and_repeat_does_not_append(evaluation_rows):
    anchor = date(2026, 8, 3)
    target = date(2026, 8, 31)
    contract = _yahoo_contract(anchor_date=anchor, target_date=target)
    root_job, root = _version(symbol="YROOT", contract=contract)
    child_job, child = _version(symbol="YROOT", contract=contract, root=root)
    _insert(versions=evaluation_rows, rows=[root_job, root])
    _insert(versions=evaluation_rows, rows=[child_job, child])
    provider = _YahooProvider(_yahoo_result(symbol="YROOT", target_date=target, close=103.0))

    with SessionLocal() as db:
        first = run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    outcomes = [_outcomes(item.id) for item in (root, child)]
    assert first.inserted == 2
    assert len(provider.calls) == 1
    assert outcomes[0][0].price_input_version == outcomes[1][0].price_input_version

    with SessionLocal() as db:
        second = run_evaluation_batch(
            db=db,
            evaluated_at=NOW,
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    assert second.inserted == 0
    assert all(len(_outcomes(item.id)) == 1 for item in (root, child))
    assert len(provider.calls) == 2


def test_unexpired_yahoo_target_never_requests_provider(evaluation_rows):
    anchor = date(2026, 9, 1)
    target = date(2026, 9, 30)
    job, version = _version(symbol="YFUT", contract=_yahoo_contract(anchor_date=anchor, target_date=target))
    _insert(versions=evaluation_rows, rows=[job, version])
    provider = _YahooProvider(_yahoo_result(symbol="YFUT", target_date=target, close=103.0))

    with SessionLocal() as db:
        run_evaluation_batch(
            db=db,
            evaluated_at=datetime(2026, 9, 30, 19, tzinfo=UTC),
            market_provider_factory=lambda: provider,
            now_factory=lambda: NOW,
        )
    assert provider.calls == []
    assert _outcomes(version.id)[0].status == "pending"
