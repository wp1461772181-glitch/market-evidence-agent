"""Create one user-requested forecast from the latest completed observed bar.

The numeric model is the checked local Week 4 artifact.  A request first asks
the existing Yahoo ingestion path to refresh the requested stock and SPY, then
uses only bars that this system has observed by the request time.  It never
uses an LLM or the legacy deterministic ``mock-v1`` endpoint.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from .features import BENCHMARK_SYMBOL, build_features
from .forecast_archive import load_trusted_model_artifact
from .market_data import MarketDataError, YAHOO_SOURCE
from .market_data_ingestion import ingest_market_data
from .market_data_snapshots import get_market_data
from .market_time import _xnys_calendar_for_year, normalize_utc, xnys_session_close_at
from .models import ForecastSnapshot
from .services import is_valid_symbol, normalize_symbol
from .training import _probabilities_in_order
from .training_data import FEATURE_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRUSTED_MODEL_DIRECTORY = PROJECT_ROOT / "artifacts" / "week4-2026-09-10"
# The fixed artifact was produced from the Week 4 experiment on September 10.
# Its final calibration data alone must not be mistaken for a date at which the
# serialized artifact was available to serve an on-demand forecast.
MODEL_PUBLICATION_BOUND = datetime(2026, 9, 11, tzinfo=UTC)
MAX_MARKET_DATA_AGE = timedelta(days=7)
INITIAL_REFRESH_LOOKBACK = timedelta(days=90)


class OnDemandForecastError(ValueError):
    """A user-actionable reason why a forecast could not be created."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_on_demand_forecast(
    *,
    symbol: str,
    db: Session,
    now: datetime | None = None,
    model_directory: Path | None = None,
) -> ForecastSnapshot:
    """Refresh market data, calculate the latest eligible row, and archive it.

    ``feature_as_of_time`` is when this system observed the input bars.  The
    feature row itself is cut at the latest *completed* XNYS session, so a
    request during a market session cannot use an unfinished daily candle.
    """
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise OnDemandForecastError("symbol must contain 1-5 ASCII letters")
    if normalized_symbol == BENCHMARK_SYMBOL:
        raise OnDemandForecastError("SPY is the benchmark and cannot receive a stock forecast")

    request_time = normalize_utc(now or _utc_now(), name="request_time")
    if request_time < MODEL_PUBLICATION_BOUND:
        raise OnDemandForecastError(
            "the fixed Week 4 model artifact was not published until "
            f"{MODEL_PUBLICATION_BOUND.isoformat()}; retrospective on-demand forecasts are refused"
        )
    completed_date = _latest_completed_session_date(request_time)
    refresh_error = _refresh_requested_market_data(
        normalized_symbol,
        completed_date=completed_date,
        run_time=request_time,
    )

    # Ingestion records ``observed_at`` after its provider call.  A cutoff
    # captured before that call would hide the rows we just ingested.  Keep the
    # completed-session boundary fixed from request_time, while taking a fresh
    # post-ingestion observation cutoff for the visible-input snapshot.
    observed_at = normalize_utc(_utc_now(), name="observed_at") if now is None else request_time

    stock_rows = get_market_data(
        normalized_symbol,
        as_of_time=observed_at,
        source=YAHOO_SOURCE,
        mode="observed",
    )
    benchmark_rows = get_market_data(
        BENCHMARK_SYMBOL,
        as_of_time=observed_at,
        source=YAHOO_SOURCE,
        mode="observed",
    )
    feature_date = _latest_common_date(stock_rows, benchmark_rows, completed_date)
    if feature_date is None:
        detail = "missing observed stock or SPY daily bars"
        if refresh_error:
            detail += f" after market refresh failed: {refresh_error}"
        raise OnDemandForecastError(detail)
    if completed_date - feature_date > MAX_MARKET_DATA_AGE:
        detail = (
            f"market data are stale: latest common completed bar is {feature_date.isoformat()} "
            f"(expected no earlier than {(completed_date - MAX_MARKET_DATA_AGE).isoformat()})"
        )
        if refresh_error:
            detail += f"; market refresh failed: {refresh_error}"
        raise OnDemandForecastError(detail)

    selected_stock_rows = [row for row in stock_rows if row.trading_date <= feature_date]
    selected_benchmark_rows = [row for row in benchmark_rows if row.trading_date <= feature_date]
    report = build_features(
        {normalized_symbol: selected_stock_rows, BENCHMARK_SYMBOL: selected_benchmark_rows},
        benchmark_symbol=BENCHMARK_SYMBOL,
        _latest_only=True,
    )
    rows = [row for row in report.rows if row.symbol == normalized_symbol and row.trading_date == feature_date]
    if len(rows) != 1:
        reasons = ", ".join(sorted({skip.reason for skip in report.skips})) or "unknown feature validation failure"
        raise OnDemandForecastError(
            f"cannot build a current feature row for {normalized_symbol} on {feature_date.isoformat()}: {reasons}"
        )

    try:
        artifact = load_trusted_model_artifact(model_directory or TRUSTED_MODEL_DIRECTORY)
    except (OSError, ValueError) as exc:
        raise OnDemandForecastError("trusted local numeric model artifact is unavailable or invalid") from exc
    if feature_date < artifact.available_from:
        raise OnDemandForecastError(
            "latest feature date predates this model's conservative availability bound "
            f"({artifact.available_from.isoformat()})"
        )

    # A browser retry must never append indistinguishable root snapshots.  A
    # later new market date, or a different checked model digest, remains a
    # separate immutable record.
    feature_values = {name: float(getattr(rows[0], name)) for name in FEATURE_COLUMNS}
    existing_candidates = (
        db.query(ForecastSnapshot)
        .filter(
            ForecastSnapshot.symbol == normalized_symbol,
            ForecastSnapshot.feature_trading_date == feature_date,
            ForecastSnapshot.model_sha256 == artifact.model_sha256,
            ForecastSnapshot.feature_snapshot_mode == "observed",
        )
        .order_by(ForecastSnapshot.created_at.desc(), ForecastSnapshot.id.desc())
        .all()
    )
    for existing in existing_candidates:
        if existing.feature_values == feature_values:
            return existing

    probabilities = _probabilities_in_order(
        artifact.model,
        pd.DataFrame([feature_values], columns=list(FEATURE_COLUMNS)),
    )[0]
    if not all(math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0 for value in probabilities):
        raise OnDemandForecastError("numeric model produced probabilities outside [0, 1]")
    if not math.isclose(float(sum(probabilities)), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise OnDemandForecastError("numeric model probabilities do not sum to one")

    snapshot = ForecastSnapshot(
        symbol=normalized_symbol,
        feature_trading_date=feature_date,
        feature_as_of_time=observed_at,
        model_version=artifact.model_version,
        model_sha256=artifact.model_sha256,
        model_manifest_sha256=artifact.manifest_sha256,
        feature_export_sha256=_feature_input_sha256(
            symbol=normalized_symbol,
            feature_date=feature_date,
            observed_at=observed_at,
            feature_values=feature_values,
        ),
        feature_version=artifact.feature_version,
        feature_source=YAHOO_SOURCE,
        # The model was trained from a historical-research export, but this
        # request's input bars are explicitly the rows observed by run_time.
        feature_snapshot_mode="observed",
        feature_values=feature_values,
        bearish_probability=float(probabilities[0]),
        neutral_probability=float(probabilities[1]),
        bullish_probability=float(probabilities[2]),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def _refresh_requested_market_data(symbol: str, *, completed_date: date, run_time: datetime) -> str | None:
    """Run the existing idempotent ingestion path and retain a safe error string."""
    try:
        prior_rows = {
            ticker: get_market_data(ticker, as_of_time=run_time, source=YAHOO_SOURCE, mode="observed")
            for ticker in (symbol, BENCHMARK_SYMBOL)
        }
        latest_dates = [rows[-1].trading_date for rows in prior_rows.values() if rows]
        if len(latest_dates) == 2:
            start_date = min(latest_dates) + timedelta(days=1)
        else:
            start_date = completed_date - INITIAL_REFRESH_LOOKBACK
        # A small overlap makes corrected late bars visible without turning a
        # normal click into a historical full reimport.
        start_date = min(start_date, completed_date - timedelta(days=7))
        ingest_market_data(
            [symbol, BENCHMARK_SYMBOL],
            start_date=start_date,
            end_date=completed_date,
            as_of_time=xnys_session_close_at(completed_date),
        )
    except (MarketDataError, OSError, ValueError) as exc:
        return str(exc)
    return None


def _latest_completed_session_date(run_time: datetime) -> date:
    calendar = _xnys_calendar_for_year(run_time.year)
    sessions = calendar.sessions_in_range(run_time.date() - timedelta(days=10), run_time.date())
    for session in reversed(sessions):
        candidate = session.date()
        if xnys_session_close_at(candidate) <= run_time:
            return candidate
    raise OnDemandForecastError("no completed XNYS session is available for this request")


def _latest_common_date(stock_rows: list[object], benchmark_rows: list[object], completed_date: date) -> date | None:
    stock_dates = {row.trading_date for row in stock_rows if row.trading_date <= completed_date}
    benchmark_dates = {row.trading_date for row in benchmark_rows if row.trading_date <= completed_date}
    shared = stock_dates & benchmark_dates
    return max(shared) if shared else None


def _feature_input_sha256(
    *,
    symbol: str,
    feature_date: date,
    observed_at: datetime,
    feature_values: dict[str, float],
) -> str:
    """Hash the exact in-memory feature input because no temporary export exists."""
    payload = {
        "feature_date": feature_date.isoformat(),
        "feature_snapshot_mode": "observed",
        "feature_source": YAHOO_SOURCE,
        "feature_values": feature_values,
        "feature_version": "market-features-v1",
        "observed_at": observed_at.isoformat(),
        "symbol": symbol,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
