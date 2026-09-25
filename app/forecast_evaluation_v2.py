"""Append-only, point-in-time evaluation for V2 forecast versions.

This module deliberately does not publish a forecast or calculate a new model
probability.  It records what the system could know at the supplied evaluation
instant: the fixed target-day close, its observed price revision, and (only for
a scored model) the resulting proper scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .forecast_contract import (
    ForecastContractError,
    target_contract_from_dict,
    target_expired,
    xnys_session_close_at,
)
from .forecast_v2_models import ForecastEvaluationV2, ForecastVersionV2
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


def run_evaluation_batch(*, db: Session, evaluated_at: datetime | None = None) -> EvaluationBatchSummary:
    """Evaluate each V2 version from price information visible by ``evaluated_at``.

    A repeated call with the same visible target quote is a no-op.  A new price
    revision which was observed after an earlier result creates a new,
    append-only ``result_version``.  Forecast versions are never changed.
    """

    instant = _utc(evaluated_at or datetime.now(UTC), name="evaluated_at")
    versions = list(db.scalars(select(ForecastVersionV2).order_by(ForecastVersionV2.created_at, ForecastVersionV2.id)))
    counts = {"inserted": 0, "unchanged": 0, "pending": 0, "succeeded": 0, "blocked_price": 0, "failed": 0}

    for version in versions:
        result, created = _evaluate_one(db=db, version=version, evaluated_at=instant)
        if created:
            counts["inserted"] += 1
        else:
            counts["unchanged"] += 1
        counts[result.status] += 1

    db.commit()
    return EvaluationBatchSummary(evaluated_at=instant, examined=len(versions), **counts)


def _evaluate_one(
    *, db: Session, version: ForecastVersionV2, evaluated_at: datetime
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
