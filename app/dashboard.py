"""Read-only helpers for the Week 9 dashboard."""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .forecast_refresh import PROJECT_ROOT
from .market_data import YAHOO_SOURCE
from .models import ForecastRevision, ForecastSnapshot, MarketPrice
from .schemas import (
    DashboardCandle,
    DashboardEvaluation,
    DashboardPriceHistory,
    ForecastSnapshotResponse,
    ForecastSnapshotTimelineEntry,
)


TRUSTED_EVALUATION_DIRECTORY = PROJECT_ROOT / "artifacts" / "week4-2026-09-10"
_METRIC_NAMES = ("accuracy", "balanced_accuracy", "brier_multiclass", "log_loss", "macro_f1")
_EVALUATION_MODELS = ("logistic_calibrated", "logistic_raw", "baseline_class_prior")
_DASHBOARD_CANDLE_LIMIT = 250


def dashboard_price_history(symbol: str, db: Session) -> DashboardPriceHistory:
    """Return a bounded, display-only daily-price history from trusted rows.

    This deliberately reads the database's current historical research
    backfill. It does not claim that the rows were visible at a forecast's
    original ``as_of_time`` and does not fetch market data.
    """
    latest_first = (
        db.query(MarketPrice)
        .filter(MarketPrice.symbol == symbol, MarketPrice.source == YAHOO_SOURCE)
        .order_by(MarketPrice.trading_date.desc())
        .limit(_DASHBOARD_CANDLE_LIMIT)
        .all()
    )
    prices = list(reversed(latest_first))
    if not prices:
        return DashboardPriceHistory(source=YAHOO_SOURCE, latest_trading_date=None, candles=[])

    benchmark_by_date = {
        row.trading_date: row.close
        for row in (
            db.query(MarketPrice)
            .filter(
                MarketPrice.symbol == "SPY",
                MarketPrice.source == YAHOO_SOURCE,
                MarketPrice.trading_date.in_([price.trading_date for price in prices]),
            )
            .all()
        )
    }
    return DashboardPriceHistory(
        source=YAHOO_SOURCE,
        latest_trading_date=prices[-1].trading_date,
        candles=[
            DashboardCandle(
                trading_date=price.trading_date,
                open=price.open,
                high=price.high,
                low=price.low,
                close=price.close,
                volume=price.volume,
                benchmark_close=benchmark_by_date.get(price.trading_date),
            )
            for price in prices
        ],
    )


def dashboard_snapshot_entries(
    snapshots: list[ForecastSnapshot], db: Session
) -> list[ForecastSnapshotTimelineEntry]:
    """Annotate a symbol's saved snapshots with their own chain identity.

    A dashboard must show separate roots distinctly.  Parent links outside the
    selected symbol or a cycle are corrupt data and fail visibly rather than
    silently being combined into one timeline.
    """
    if not snapshots:
        return []

    snapshots_by_id = {snapshot.id: snapshot for snapshot in snapshots}
    revisions = db.query(ForecastRevision).filter(ForecastRevision.snapshot_id.in_(snapshots_by_id)).all()
    revision_by_snapshot = {revision.snapshot_id: revision for revision in revisions}

    entries: list[ForecastSnapshotTimelineEntry] = []
    for snapshot in snapshots:
        root_id, version = _chain_position(
            snapshot.id, snapshots_by_id=snapshots_by_id, revision_by_snapshot=revision_by_snapshot
        )
        revision = revision_by_snapshot.get(snapshot.id)
        entries.append(
            ForecastSnapshotTimelineEntry(
                **ForecastSnapshotResponse.model_validate(snapshot).model_dump(),
                version=version,
                root_snapshot_id=root_id,
                parent_snapshot_id=revision.parent_snapshot_id if revision else None,
                revision_reason=revision.reason if revision else None,
            )
        )
    return entries


def _chain_position(
    snapshot_id: UUID,
    *,
    snapshots_by_id: dict[UUID, ForecastSnapshot],
    revision_by_snapshot: dict[UUID, ForecastRevision],
) -> tuple[UUID, int]:
    current_id = snapshot_id
    seen: set[UUID] = set()
    traversed: list[ForecastRevision] = []
    version = 1
    while (revision := revision_by_snapshot.get(current_id)) is not None:
        if current_id in seen:
            raise HTTPException(status_code=500, detail="forecast revision history contains a cycle")
        seen.add(current_id)
        parent = snapshots_by_id.get(revision.parent_snapshot_id)
        if parent is None:
            raise HTTPException(status_code=500, detail="forecast revision history has a missing or cross-symbol parent")
        traversed.append(revision)
        current_id = parent.id
        version += 1
    if any(revision.root_snapshot_id != current_id for revision in traversed):
        raise HTTPException(status_code=500, detail="forecast revision history has an inconsistent root")
    return current_id, version


def dashboard_evaluation() -> DashboardEvaluation | None:
    """Read a whitelist from the fixed local Week 4 evaluation artifacts."""
    try:
        report = json.loads((TRUSTED_EVALUATION_DIRECTORY / "report.json").read_text(encoding="utf-8"))
        manifest = json.loads((TRUSTED_EVALUATION_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
        experiment = _required_dict(report, "experiment")
        data = _required_dict(report, "data_metadata")
        folds = report["folds"]
        if not isinstance(folds, list) or not folds:
            return None
        pooled = _required_dict(report, "pooled_oos")
        models = {
            model_name: {
                metric_name: _number(_required_dict(pooled, model_name)[metric_name])
                for metric_name in _METRIC_NAMES
            }
            for model_name in _EVALUATION_MODELS
        }
        test_rows = sum(_required_int(_required_dict(fold, "row_counts"), "test") for fold in folds)
        return DashboardEvaluation(
            artifact_version=_required_text(manifest, "artifact_version"),
            model_name="logistic_calibrated",
            scope="Pooled out-of-sample metrics across five stocks and three walk-forward folds; not symbol-specific.",
            data_as_of_time=_required_text(data, "as_of_time"),
            feature_version=_required_text(data, "feature_version"),
            snapshot_mode=_required_text(data, "snapshot_mode"),
            fold_count=len(folds),
            test_rows=test_rows,
            models=models,
            limitations=[
                _required_text(experiment, "purpose"),
                "Historical research data are not evidence of a live observed-data feed.",
            ],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _required_dict(value: dict, key: str) -> dict:
    result = value[key]
    if not isinstance(result, dict):
        raise TypeError(key)
    return result


def _required_text(value: dict, key: str) -> str:
    result = value[key]
    if not isinstance(result, str) or not result:
        raise TypeError(key)
    return result


def _required_int(value: dict, key: str) -> int:
    result = value[key]
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise TypeError(key)
    return result


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("metric")
    result = float(value)
    if not math.isfinite(result):
        raise TypeError("metric")
    return result
