"""Leakage-safe daily market features built from historical price snapshots.

Every value on a :class:`FeatureRow` uses the row's trading date or earlier.
The 20-return features therefore need 21 daily bars: the start close plus the
twenty closes used to calculate those returns.  Market dates are matched
exactly; the SPY series is never forward- or backward-filled.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from .market_data import YAHOO_SOURCE
from .market_time import normalize_utc, xnys_session_close_at
from .services import is_valid_symbol, normalize_symbol


FEATURE_VERSION = "market-features-v1"
BENCHMARK_SYMBOL = "SPY"
RETURN_LOOKBACK_DAYS = 20
SHORT_MOMENTUM_DAYS = 5
TRADING_DAYS_PER_YEAR = 252
SNAPSHOT_MODES = ("historical_research", "observed")


@dataclass(frozen=True)
class FeatureRow:
    """Features for one symbol at one completed daily bar.

    ``volatility_20d`` is the sample standard deviation (``ddof=1``) of the
    twenty close-to-close simple returns ending on ``trading_date``, annualized
    by ``sqrt(252)``.  ``volume_ratio_20d`` divides current volume by the mean
    of the preceding 20 daily volumes, excluding current-day volume.
    """

    symbol: str
    trading_date: date
    momentum_5d: float
    momentum_20d: float
    volatility_20d: float
    volume_ratio_20d: float
    drawdown_20d: float
    relative_return_20d: float
    feature_version: str = FEATURE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureSkip:
    symbol: str
    trading_date: date
    reason: str


@dataclass(frozen=True)
class FeatureBuildReport:
    rows: tuple[FeatureRow, ...]
    skips: tuple[FeatureSkip, ...]

    @property
    def skipped_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for skip in self.skips:
            counts[skip.reason] = counts.get(skip.reason, 0) + 1
        return counts


def build_features(
    prices_by_symbol: Mapping[str, Sequence[object]],
    *,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    _latest_only: bool = False,
) -> FeatureBuildReport:
    """Build explainable features from already-cut historical daily bars.

    The function accepts ``DailyPrice`` or ORM ``MarketPrice`` objects, as
    long as each has ``symbol``, ``trading_date``, ``close`` and ``volume``.
    It validates whole input series before calculating anything, so duplicate
    dates and non-finite numeric inputs cannot quietly alter a time series.
    """
    normalized_benchmark = _validate_symbol(benchmark_symbol, "benchmark_symbol")
    normalized_prices = _validate_and_normalize_prices(prices_by_symbol)
    benchmark_rows = normalized_prices.get(normalized_benchmark, ())
    benchmark_by_date = {
        row.trading_date: row for row in benchmark_rows
    }
    benchmark_index_by_date = {row.trading_date: index for index, row in enumerate(benchmark_rows)}

    feature_rows: list[FeatureRow] = []
    skips: list[FeatureSkip] = []
    for symbol in sorted(normalized_prices):
        rows = normalized_prices[symbol]
        indexes = range(len(rows) - 1, len(rows)) if _latest_only and rows else range(len(rows))
        for index in indexes:
            current = rows[index]
            if index < RETURN_LOOKBACK_DAYS:
                skips.append(FeatureSkip(symbol, current.trading_date, "insufficient_history"))
                continue

            benchmark_start = benchmark_by_date.get(rows[index - RETURN_LOOKBACK_DAYS].trading_date)
            benchmark_end = benchmark_by_date.get(current.trading_date)
            if benchmark_start is None or benchmark_end is None:
                skips.append(FeatureSkip(symbol, current.trading_date, "missing_benchmark_date"))
                continue
            benchmark_end_index = benchmark_index_by_date[current.trading_date]
            stock_window_dates = [row.trading_date for row in rows[index - RETURN_LOOKBACK_DAYS : index + 1]]
            if benchmark_end_index < RETURN_LOOKBACK_DAYS:
                skips.append(FeatureSkip(symbol, current.trading_date, "insufficient_benchmark_history"))
                continue
            benchmark_window_dates = [
                row.trading_date
                for row in benchmark_rows[
                    benchmark_end_index - RETURN_LOOKBACK_DAYS : benchmark_end_index + 1
                ]
            ]
            if stock_window_dates != benchmark_window_dates:
                skips.append(FeatureSkip(symbol, current.trading_date, "missing_symbol_session"))
                continue

            prior_volumes = [_volume(row) for row in rows[index - RETURN_LOOKBACK_DAYS : index]]
            mean_prior_volume = math.fsum(prior_volumes) / RETURN_LOOKBACK_DAYS
            if mean_prior_volume <= 0.0:
                skips.append(FeatureSkip(symbol, current.trading_date, "non_positive_prior_volume_mean"))
                continue

            current_close = _close(current)
            return_window = [
                (_close(rows[position]) / _close(rows[position - 1])) - 1.0
                for position in range(index - RETURN_LOOKBACK_DAYS + 1, index + 1)
            ]
            mean_return = math.fsum(return_window) / RETURN_LOOKBACK_DAYS
            variance = math.fsum((value - mean_return) ** 2 for value in return_window) / (
                RETURN_LOOKBACK_DAYS - 1
            )
            rolling_high = max(_close(row) for row in rows[index - RETURN_LOOKBACK_DAYS + 1 : index + 1])

            momentum_5d = _finite_feature(
                "momentum_5d", current_close / _close(rows[index - SHORT_MOMENTUM_DAYS]) - 1.0
            )
            momentum_20d = _finite_feature(
                "momentum_20d", current_close / _close(rows[index - RETURN_LOOKBACK_DAYS]) - 1.0
            )
            volatility_20d = _finite_feature(
                "volatility_20d", math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)
            )
            volume_ratio_20d = _finite_feature("volume_ratio_20d", _volume(current) / mean_prior_volume)
            drawdown_20d = _finite_feature("drawdown_20d", current_close / rolling_high - 1.0)
            relative_return_20d = _finite_feature(
                "relative_return_20d",
                (current_close / _close(rows[index - RETURN_LOOKBACK_DAYS]))
                - (_close(benchmark_end) / _close(benchmark_start)),
            )

            feature_rows.append(
                FeatureRow(
                    symbol=symbol,
                    trading_date=current.trading_date,
                    momentum_5d=momentum_5d,
                    momentum_20d=momentum_20d,
                    volatility_20d=volatility_20d,
                    volume_ratio_20d=volume_ratio_20d,
                    drawdown_20d=drawdown_20d,
                    relative_return_20d=relative_return_20d,
                )
            )
    return FeatureBuildReport(rows=tuple(feature_rows), skips=tuple(skips))


def build_features_from_snapshot(
    symbols: Iterable[str],
    *,
    as_of_time: datetime,
    source: str = YAHOO_SOURCE,
    mode: str = "historical_research",
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    data_loader: Callable[..., Sequence[object]] | None = None,
) -> FeatureBuildReport:
    """Build every historic row from the snapshot available at its own close.

    ``as_of_time`` only discovers candidate dates.  A candidate on date ``t``
    is calculated from snapshots cut at ``min(XNYS_close(t), as_of_time)``.
    Hence a provider correction observed after ``t`` cannot alter the feature
    at ``t``.  This intentionally favors a strict point-in-time boundary over
    using end-of-period revised prices.

    The current snapshot API returns one symbol at a time, so this conservative
    fallback caches only the last 21 bars for each (symbol, trading date).  A
    future revision batch query may replace it without changing the formulas.
    """
    if mode not in SNAPSHOT_MODES:
        raise ValueError(f"mode must be one of: {', '.join(SNAPSHOT_MODES)}")
    final_cutoff = normalize_utc(as_of_time)
    if data_loader is None:
        from .market_data_snapshots import get_market_data

        data_loader = get_market_data

    normalized_benchmark = _validate_symbol(benchmark_symbol, "benchmark_symbol")
    requested_symbols = {_validate_symbol(symbol, "symbol") for symbol in symbols}
    requested_symbols.add(normalized_benchmark)
    candidate_dates = {
        symbol: tuple(
            row.trading_date
            for row in data_loader(symbol, as_of_time=final_cutoff, source=source, mode=mode)
        )
        for symbol in sorted(requested_symbols)
    }
    cached_snapshots: dict[tuple[str, date], tuple[object, ...]] = {}

    def snapshot_at_session_close(symbol: str, trading_date: date) -> tuple[object, ...]:
        key = (symbol, trading_date)
        if key not in cached_snapshots:
            cutoff = min(xnys_session_close_at(trading_date), final_cutoff)
            rows = data_loader(symbol, as_of_time=cutoff, source=source, mode=mode)
            cached_snapshots[key] = tuple(rows[-(RETURN_LOOKBACK_DAYS + 1) :])
        return cached_snapshots[key]

    feature_rows: list[FeatureRow] = []
    skips: list[FeatureSkip] = []
    for symbol in sorted(requested_symbols):
        for trading_date in candidate_dates[symbol]:
            symbol_snapshot = snapshot_at_session_close(symbol, trading_date)
            benchmark_snapshot = snapshot_at_session_close(normalized_benchmark, trading_date)
            if not any(row.trading_date == trading_date for row in symbol_snapshot):
                skips.append(FeatureSkip(symbol, trading_date, "not_visible_at_feature_cutoff"))
                continue
            report = build_features(
                {
                    symbol: symbol_snapshot,
                    normalized_benchmark: benchmark_snapshot,
                },
                benchmark_symbol=normalized_benchmark,
                _latest_only=True,
            )
            feature_rows.extend(
                row
                for row in report.rows
                if row.symbol == symbol and row.trading_date == trading_date
            )
            skips.extend(
                skip
                for skip in report.skips
                if skip.symbol == symbol and skip.trading_date == trading_date
            )
    return FeatureBuildReport(rows=tuple(feature_rows), skips=tuple(skips))


def metadata(*, as_of_time: datetime, source: str, mode: str) -> dict[str, Any]:
    """Return machine-readable provenance for a feature export."""
    if mode not in SNAPSHOT_MODES:
        raise ValueError(f"mode must be one of: {', '.join(SNAPSHOT_MODES)}")
    return {
        "feature_version": FEATURE_VERSION,
        "as_of_time": normalize_utc(as_of_time).isoformat(),
        "source": source,
        "snapshot_mode": mode,
        "historical_research_limit": (
            "Daily bars are assumed available at XNYS session close on their trading date; "
            "later provider revisions are excluded until observed."
            if mode == "historical_research"
            else None
        ),
        "observed_limit": (
            "Only versions actually observed by the cutoff are included. Initial historical backfills "
            "recorded later can therefore produce no rows at older cutoffs."
            if mode == "observed"
            else None
        ),
        "formula": {
            "momentum_5d": "close_t / close_t-5 - 1",
            "momentum_20d": "close_t / close_t-20 - 1",
            "volatility_20d": "sample stddev of 20 close-to-close returns (ddof=1) * sqrt(252)",
            "volume_ratio_20d": "volume_t / mean(volume_t-20 ... volume_t-1)",
            "drawdown_20d": "close_t / max(close_t-19 ... close_t) - 1",
            "relative_return_20d": "stock 20-day return - SPY 20-day return, exact-date aligned",
        },
    }


def _validate_and_normalize_prices(
    prices_by_symbol: Mapping[str, Sequence[object]],
) -> dict[str, tuple[object, ...]]:
    normalized_prices: dict[str, tuple[object, ...]] = {}
    for key, input_rows in prices_by_symbol.items():
        symbol = _validate_symbol(key, "symbol")
        if symbol in normalized_prices:
            raise ValueError(f"duplicate normalized symbol mapping key: {symbol}")
        rows = tuple(sorted(input_rows, key=lambda row: _trading_date(row)))
        seen_dates: set[date] = set()
        for row in rows:
            row_symbol = _validate_symbol(getattr(row, "symbol", None), "row.symbol")
            if row_symbol != symbol:
                raise ValueError(f"row symbol {row_symbol} does not match mapping key {symbol}")
            current_date = _trading_date(row)
            if current_date in seen_dates:
                raise ValueError(f"duplicate trading_date for {symbol}: {current_date.isoformat()}")
            seen_dates.add(current_date)
            _close(row)
            _volume(row)
        normalized_prices[symbol] = rows
    return normalized_prices


def _validate_symbol(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a valid symbol")
    normalized = normalize_symbol(value)
    if not is_valid_symbol(normalized):
        raise ValueError(f"{name} must be a valid symbol")
    return normalized


def _trading_date(row: object) -> date:
    value = getattr(row, "trading_date", None)
    if not isinstance(value, date):
        raise ValueError("row.trading_date must be a date")
    return value


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _close(row: object) -> float:
    close = _finite_number(getattr(row, "close", None), "row.close")
    if close <= 0.0:
        raise ValueError("row.close must be greater than zero")
    return close


def _volume(row: object) -> float:
    volume = _finite_number(getattr(row, "volume", None), "row.volume")
    if volume < 0.0:
        raise ValueError("row.volume must not be negative")
    return volume


def _finite_feature(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} is not finite; input values are too extreme")
    return value
