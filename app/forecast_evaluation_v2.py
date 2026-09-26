"""Append-only, point-in-time evaluation for V2 forecast versions.

This module deliberately does not publish a forecast or calculate a new model
probability.  It records what the system could know at the supplied evaluation
instant: the fixed target-day close, its observed price revision, and (only for
a scored model) the resulting proper scores.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .forecast_contract import (
    ForecastContractError,
    PriceBasisError,
    target_contract_from_dict,
    target_expired,
    validate_price_basis,
    xnys_session_close_at,
)
from .forecast_v2_models import ForecastEvaluationV2, ForecastVersionV2
from .market_data import MarketDataError, MarketDataFetchResult, YahooFinanceProvider, YAHOO_SOURCE
from .market_data_ingestion import _content_hash, ingest_market_data
from .models import MarketPrice, MarketPriceRevision

_CLASSES = ("bearish", "neutral", "bullish")


@dataclass(frozen=True)
class EvaluationBatchSummary:
    """A durable caller-facing summary of one evaluation attempt.

    The evaluator itself does not persist a run table.  A scheduler can store
    this summary beside its own run record, while still letting a failed SEC
    scan call this independent batch.
    """

    evaluated_at: datetime
    examined: int
    inserted: int
    unchanged: int
    pending: int
    succeeded: int
    blocked_price: int
    failed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": _iso(self.evaluated_at),
            "examined": self.examined,
            "inserted": self.inserted,
            "unchanged": self.unchanged,
            "pending": self.pending,
            "succeeded": self.succeeded,
            "blocked_price": self.blocked_price,
            "failed": self.failed,
        }


@dataclass(frozen=True)
class _VisiblePrice:
    close: float
    manifest: dict[str, Any]
    label_available_at: datetime


@dataclass(frozen=True)
class _TargetRefresh:
    """Result of one root-level, post-close Yahoo price-basis recheck."""

    status: Literal["safe", "pending", "blocked_price"]
    visible: _VisiblePrice | None = None
    error_message: str | None = None
    observed_at: datetime | None = None


class _MarketProvider(Protocol):
    def fetch_daily_prices_with_metadata(
        self, symbol: str, start_date: Any, end_date: Any
    ) -> MarketDataFetchResult: ...


def run_evaluation_batch(
    *,
    db: Session,
    evaluated_at: datetime | None = None,
    market_provider_factory: Callable[[], _MarketProvider] = YahooFinanceProvider,
    market_ingester: Callable[..., object] = ingest_market_data,
    now_factory: Callable[[], datetime] | None = None,
) -> EvaluationBatchSummary:
    """Evaluate each V2 version from price information visible by ``evaluated_at``.

    A repeated call with the same visible target quote is a no-op.  A new price
    revision which was observed after an earlier result creates a new,
    append-only ``result_version``.  Forecast versions are never changed.
    """

    received_clock = now_factory or (lambda: datetime.now(UTC))
    # An explicit instant is a historical cutoff: a response received after it
    # cannot be used. A live batch instead advances its result cutoff to the
    # actual receipt time of each safe target refresh.
    strict_cutoff = evaluated_at is not None
    instant = _utc(evaluated_at, name="evaluated_at") if strict_cutoff else _utc(received_clock(), name="evaluated_at")
    versions = list(db.scalars(select(ForecastVersionV2).order_by(ForecastVersionV2.created_at, ForecastVersionV2.id)))
    counts = {"inserted": 0, "unchanged": 0, "pending": 0, "succeeded": 0, "blocked_price": 0, "failed": 0}
    refreshed = _refresh_mature_yahoo_targets(
        db=db,
        versions=versions,
        evaluated_at=instant,
        market_provider_factory=market_provider_factory,
        market_ingester=market_ingester,
        received_clock=received_clock,
        strict_cutoff=strict_cutoff,
    )
    effective_instant = max(
        [instant, *(item.observed_at for item in refreshed.values() if item.observed_at is not None)]
    )

    for version in versions:
        result, created = _evaluate_one(
            db=db,
            version=version,
            evaluated_at=effective_instant,
            target_refresh=refreshed.get(version.root_id),
        )
        if created:
            counts["inserted"] += 1
        else:
            counts["unchanged"] += 1
        counts[result.status] += 1

    db.commit()
    return EvaluationBatchSummary(evaluated_at=effective_instant, examined=len(versions), **counts)


def _evaluate_one(
    *,
    db: Session,
    version: ForecastVersionV2,
    evaluated_at: datetime,
    target_refresh: _TargetRefresh | None,
) -> tuple[ForecastEvaluationV2, bool]:
    try:
        contract = target_contract_from_dict(version.target_contract)
    except ForecastContractError as exc:
        # A holiday/non-session target is invalid rather than a reason to use a
        # neighbouring day's bar.  Keeping it visible as a failure avoids
        # silently manufacturing a target outcome.
        return _append_if_changed(
            db=db,
            version=version,
            status="failed",
            actual_target_close=None,
            price_input_version=None,
            actual_label=None,
            label_available_at=None,
            scores=(None, None, None),
            error_message=f"invalid target contract: {exc}",
        )

    if not target_expired(contract, as_of_time=evaluated_at):
        return _append_if_changed(
            db=db,
            version=version,
            status="pending",
            actual_target_close=None,
            price_input_version=None,
            actual_label=None,
            label_available_at=None,
            scores=(None, None, None),
            error_message="target session has not closed",
        )

    if contract.price_source == YAHOO_SOURCE and target_refresh is not None:
        if target_refresh.status == "blocked_price":
            return _append_if_changed(
                db=db,
                version=version,
                status="blocked_price",
                actual_target_close=None,
                price_input_version=None,
                actual_label=None,
                label_available_at=None,
                scores=(None, None, None),
                error_message=target_refresh.error_message,
            )
        if target_refresh.status == "pending":
            return _append_if_changed(
                db=db,
                version=version,
                status="pending",
                actual_target_close=None,
                price_input_version=None,
                actual_label=None,
                label_available_at=None,
                scores=(None, None, None),
                error_message=target_refresh.error_message,
            )
        visible = target_refresh.visible
    elif contract.price_source == YAHOO_SOURCE:
        # A due production V2 target must be checked against a fresh Yahoo
        # interval.  Do not turn an older, unreviewed stored quote into a label.
        visible = None
    else:
        # Fixture and non-Yahoo contracts predate P7's provider-specific safe
        # maturity path. They keep their explicit, observable test semantics.
        visible = _visible_target_price(
            db=db,
            symbol=version.symbol,
            target_date=contract.target_end_date,
            source=contract.price_source,
            evaluated_at=evaluated_at,
        )
    if visible is None:
        # Absence is temporary: an ingestion job may receive the completed bar
        # on the next run.  It must not be represented as a terminal result.
        return _append_if_changed(
            db=db,
            version=version,
            status="pending",
            actual_target_close=None,
            price_input_version=None,
            actual_label=None,
            label_available_at=None,
            scores=(None, None, None),
            error_message="target-day price is not yet observed",
        )

    try:
        actual_label = contract.classify(visible.close)
        scores = _scores_for(version=version, actual_label=actual_label)
        return _append_if_changed(
            db=db,
            version=version,
            status="succeeded",
            actual_target_close=visible.close,
            price_input_version=visible.manifest,
            actual_label=actual_label,
            label_available_at=visible.label_available_at,
            scores=scores,
            error_message=None,
        )
    except (ForecastContractError, ValueError) as exc:
        return _append_if_changed(
            db=db,
            version=version,
            status="failed",
            actual_target_close=visible.close,
            price_input_version=visible.manifest,
            actual_label=None,
            label_available_at=visible.label_available_at,
            scores=(None, None, None),
            error_message=f"evaluation failed: {exc}",
        )


def _refresh_mature_yahoo_targets(
    *,
    db: Session,
    versions: list[ForecastVersionV2],
    evaluated_at: datetime,
    market_provider_factory: Callable[[], _MarketProvider],
    market_ingester: Callable[..., object],
    received_clock: Callable[[], datetime],
    strict_cutoff: bool,
) -> dict[Any, _TargetRefresh]:
    """Re-fetch each due root once before accepting its target-day close.

    Yahoo's response is requested from the frozen anchor through the target,
    so ``validate_price_basis`` can reject a split or unknown action anywhere
    in that fixed interval. Only the already-closed target-day row is written
    as a fresh price observation; the root forecast itself remains unchanged.
    """

    roots = {version.root_id: version for version in versions if version.id == version.root_id}
    results: dict[Any, _TargetRefresh] = {}
    provider: _MarketProvider | None = None
    for root_id, root in roots.items():
        try:
            contract = target_contract_from_dict(root.target_contract)
        except ForecastContractError:
            continue
        if contract.price_source != YAHOO_SOURCE or not target_expired(contract, as_of_time=evaluated_at):
            continue
        if provider is None:
            provider = market_provider_factory()
        results[root_id] = _refresh_one_target(
            db=db,
            root_version_id=root_id,
            symbol=root.symbol,
            contract=contract,
            market_provider=provider,
            market_ingester=market_ingester,
            received_clock=received_clock,
            evaluation_cutoff=evaluated_at,
            strict_cutoff=strict_cutoff,
        )
    return results


def _refresh_one_target(
    *,
    db: Session,
    root_version_id: Any,
    symbol: str,
    contract: Any,
    market_provider: _MarketProvider,
    market_ingester: Callable[..., object],
    received_clock: Callable[[], datetime],
    evaluation_cutoff: datetime,
    strict_cutoff: bool,
) -> _TargetRefresh:
    try:
        fetched = market_provider.fetch_daily_prices_with_metadata(
            symbol, contract.anchor_date, contract.target_end_date
        )
        basis_check = validate_price_basis(
            metadata=fetched.price_basis,
            anchor_date=contract.anchor_date,
            target_end_date=contract.target_end_date,
        )
    except PriceBasisError as exc:
        return _TargetRefresh(
            status="blocked_price",
            error_message=f"target interval price-basis review blocked: {exc.code}",
        )
    except (MarketDataError, OSError, TimeoutError, ValueError) as exc:
        return _TargetRefresh(
            status="blocked_price",
            error_message=f"target-day Yahoo refresh failed: {exc}",
        )

    if not bool(getattr(fetched.price_basis, "corporate_actions_available", False)):
        return _TargetRefresh(
            status="blocked_price",
            error_message="target interval price-basis review blocked: corporate actions unavailable",
        )

    anchor_rows = [row for row in fetched.prices if row.trading_date == contract.anchor_date]
    if len(anchor_rows) != 1:
        return _TargetRefresh(
            status="blocked_price",
            error_message="target interval price-basis review blocked: anchor-day quote is missing or ambiguous",
        )
    # The contract is an absolute-return question with a frozen numerator and
    # denominator. A later provider rewrite of the anchor quote would change
    # its denominator even without a split marker, so equality is deliberate.
    if float(anchor_rows[0].close) != float(contract.anchor_close):
        return _TargetRefresh(
            status="blocked_price",
            error_message="target interval price-basis review blocked: anchor-day close differs from the frozen contract",
        )

    target_rows = [row for row in fetched.prices if row.trading_date == contract.target_end_date]
    if len(target_rows) != 1:
        return _TargetRefresh(
            status="pending",
            error_message="target-day Yahoo quote is not yet available",
        )

    cached = _cached_safe_visible(
        db=db,
        root_version_id=root_version_id,
        target_row=target_rows[0],
        price_basis_check=basis_check.as_dict(),
        visible_cutoff=evaluation_cutoff,
    )
    if cached is not None:
        return _TargetRefresh(status="safe", visible=cached)

    # The response has returned, so this is the actual system receipt time.
    # It is intentionally not the original evaluation-start timestamp.
    observed_at = _utc(received_clock(), name="target-day Yahoo receipt time")
    if strict_cutoff and observed_at > evaluation_cutoff:
        return _TargetRefresh(
            status="pending",
            error_message="target-day Yahoo quote was received after the evaluation cutoff",
        )
    try:
        summary = market_ingester(
            [symbol],
            contract.target_end_date,
            contract.target_end_date,
            observed_at,
            source=YAHOO_SOURCE,
            fetcher=lambda *_args: target_rows,
            now_factory=lambda: observed_at,
            # A same-valued response is still a new, audited observation whose
            # source response passed this exact anchor-to-target action review.
            record_observation=True,
        )
        revision = db.scalar(
            select(MarketPriceRevision)
            .where(
                MarketPriceRevision.ingestion_run_id == summary.run_id,
                MarketPriceRevision.symbol == symbol,
                MarketPriceRevision.trading_date == contract.target_end_date,
                MarketPriceRevision.source == YAHOO_SOURCE,
            )
            .order_by(MarketPriceRevision.revision_number.desc())
            .limit(1)
        )
    except (MarketDataError, OSError, TimeoutError, ValueError, RuntimeError) as exc:
        return _TargetRefresh(
            status="blocked_price",
            error_message=f"target-day Yahoo ingestion failed: {exc}",
        )
    if revision is None:
        return _TargetRefresh(
            status="pending",
            error_message="target-day Yahoo quote was not persisted",
        )

    visible = _VisiblePrice(
        close=float(revision.close),
        manifest={
            "kind": "safe_maturity_yahoo_refresh_v1",
            "target_quote": {
                "kind": "market_price_revision",
                "id": str(revision.id),
                "source": revision.source,
                "trading_date": revision.trading_date.isoformat(),
                "revision_number": revision.revision_number,
                "content_hash": revision.content_hash,
                "available_at": _iso(revision.available_at),
                "observed_at": _iso(revision.observed_at),
            },
            "price_basis_check": basis_check.as_dict(),
            "refresh_observed_at": _iso(observed_at),
        },
        label_available_at=max(
            _utc(revision.available_at, name="price.available_at"),
            _utc(revision.observed_at, name="price.observed_at"),
        ),
    )
    return _TargetRefresh(status="safe", visible=visible, observed_at=observed_at)


def _cached_safe_visible(
    *,
    db: Session,
    root_version_id: Any,
    target_row: Any,
    price_basis_check: dict[str, Any],
    visible_cutoff: datetime,
) -> _VisiblePrice | None:
    """Reuse an identical, previously audited maturity result.

    The provider is still queried each cycle. Reusing only a result whose
    exact OHLCV hash and basis check match prevents a no-change recheck from
    inflating either price-revision or evaluation-version counts.
    """

    previous = db.scalar(
        select(ForecastEvaluationV2)
        .where(
            ForecastEvaluationV2.forecast_version_id == root_version_id,
            ForecastEvaluationV2.status == "succeeded",
        )
        .order_by(ForecastEvaluationV2.result_version.desc())
        .limit(1)
    )
    if previous is None or previous.actual_target_close is None or previous.label_available_at is None:
        return None
    manifest = previous.price_input_version
    if not isinstance(manifest, dict) or manifest.get("kind") != "safe_maturity_yahoo_refresh_v1":
        return None
    target_quote = manifest.get("target_quote")
    if not isinstance(target_quote, dict):
        return None
    if (
        target_quote.get("content_hash") != _content_hash(target_row)
        or manifest.get("price_basis_check") != price_basis_check
        or float(previous.actual_target_close) != float(target_row.close)
    ):
        return None
    label_available_at = _utc(previous.label_available_at, name="cached label_available_at")
    if label_available_at > visible_cutoff:
        return None
    return _VisiblePrice(
        close=float(previous.actual_target_close),
        manifest=manifest,
        label_available_at=label_available_at,
    )


def _visible_target_price(
    *, db: Session, symbol: str, target_date: Any, source: str, evaluated_at: datetime
) -> _VisiblePrice | None:
    """Return the newest target quote that was actually received by this time."""

    target_close_at = xnys_session_close_at(target_date)
    revisions = list(
        db.scalars(
            select(MarketPriceRevision)
            .where(
                MarketPriceRevision.symbol == symbol,
                MarketPriceRevision.trading_date == target_date,
                MarketPriceRevision.source == source,
                # A target-day daily bar cannot be available or observed before
                # the target session actually closes.  Reject anomalous early
                # rows instead of producing a prematurely known label.
                MarketPriceRevision.available_at >= target_close_at,
                MarketPriceRevision.observed_at >= target_close_at,
                MarketPriceRevision.available_at <= evaluated_at,
                MarketPriceRevision.observed_at <= evaluated_at,
            )
            .order_by(MarketPriceRevision.revision_number.desc(), MarketPriceRevision.observed_at.desc())
        )
    )
    if revisions:
        quote = revisions[0]
        return _VisiblePrice(
            close=float(quote.close),
            manifest={
                "kind": "market_price_revision",
                "id": str(quote.id),
                "source": quote.source,
                "trading_date": quote.trading_date.isoformat(),
                "revision_number": quote.revision_number,
                "content_hash": quote.content_hash,
                "available_at": _iso(quote.available_at),
                "observed_at": _iso(quote.observed_at),
            },
            label_available_at=max(
                _utc(quote.available_at, name="price.available_at"),
                _utc(quote.observed_at, name="price.observed_at"),
            ),
        )

    # Legacy prices have no independent availability timestamp.  Treat their
    # fetch time as both receipt and availability, rather than leaking a row
    # fetched later into a historical evaluation.
    prices = list(
        db.scalars(
            select(MarketPrice)
            .where(
                MarketPrice.symbol == symbol,
                MarketPrice.trading_date == target_date,
                MarketPrice.source == source,
                # A malformed/backfilled legacy row with an earlier fetch time
                # cannot establish a target close before that session closes.
                MarketPrice.fetched_at >= target_close_at,
                MarketPrice.fetched_at <= evaluated_at,
            )
            .order_by(MarketPrice.fetched_at.desc(), MarketPrice.id.desc())
        )
    )
    if not prices:
        return None
    quote = prices[0]
    observed_at = _utc(quote.fetched_at, name="market_price.fetched_at")
    return _VisiblePrice(
        close=float(quote.close),
        manifest={
            "kind": "legacy_market_price",
            "id": str(quote.id),
            "source": quote.source,
            "trading_date": quote.trading_date.isoformat(),
            "fetched_at": _iso(observed_at),
        },
        label_available_at=observed_at,
    )


def _scores_for(
    *, version: ForecastVersionV2, actual_label: str
) -> tuple[float | None, float | None, bool | None]:
    """Return scores only when this version has a scoreable probability vector."""

    probabilities: dict[str, Any] | None
    if version.model_status == "experimental_joint":
        probabilities = version.joint_probabilities
    elif version.model_status == "baseline_only":
        probabilities = version.baseline_probabilities
    else:
        # ``research_only`` is intentionally a conclusion/evidence artifact,
        # not a calibrated numerical forecast.  Persist the outcome, but never
        # invent a numerical score from its display probabilities.
        return None, None, None

    if probabilities is None:
        raise ValueError("scoreable model status has no probability vector")
    values = _validated_probabilities(probabilities)
    actual_index = _CLASSES.index(actual_label)
    brier = sum((value - (1.0 if index == actual_index else 0.0)) ** 2 for index, value in enumerate(values))
    probability_of_actual = values[actual_index]
    log_loss = -math.log(probability_of_actual)
    predicted = _CLASSES[max(range(len(values)), key=lambda index: values[index])]
    return brier, log_loss, predicted == actual_label


def _validated_probabilities(value: dict[str, Any]) -> tuple[float, float, float]:
    if set(value) != set(_CLASSES):
        raise ValueError("probability vector must contain bearish, neutral, and bullish")
    values = tuple(float(value[label]) for label in _CLASSES)
    if any(not math.isfinite(item) or item <= 0.0 or item > 1.0 for item in values):
        raise ValueError("probability vector must contain finite values in (0, 1]")
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("probability vector must sum to 1")
    return values  # type: ignore[return-value]


def _append_if_changed(
    *,
    db: Session,
    version: ForecastVersionV2,
    status: Literal["pending", "succeeded", "blocked_price", "failed"],
    actual_target_close: float | None,
    price_input_version: dict[str, Any] | None,
    actual_label: str | None,
    label_available_at: datetime | None,
    scores: tuple[float | None, float | None, bool | None],
    error_message: str | None,
) -> tuple[ForecastEvaluationV2, bool]:
    latest = db.scalar(
        select(ForecastEvaluationV2)
        .where(ForecastEvaluationV2.forecast_version_id == version.id)
        .order_by(ForecastEvaluationV2.result_version.desc())
        .limit(1)
    )
    expected = {
        "status": status,
        "actual_target_close": actual_target_close,
        "price_input_version": price_input_version,
        "actual_label": actual_label,
        "label_available_at": label_available_at,
        "brier_score": scores[0],
        "log_loss": scores[1],
        "direction_correct": scores[2],
        "error_message": error_message,
    }
    if latest is not None and _same_result(latest, expected):
        return latest, False

    result = ForecastEvaluationV2(
        forecast_version_id=version.id,
        target_contract_hash=version.target_contract_hash,
        result_version=(latest.result_version + 1 if latest is not None else 1),
        **expected,
    )
    db.add(result)
    db.flush()
    return result, True


def _same_result(result: ForecastEvaluationV2, expected: dict[str, Any]) -> bool:
    return all(getattr(result, field) == value for field, value in expected.items())


def _utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value, name="timestamp").isoformat().replace("+00:00", "Z")
