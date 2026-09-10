"""Build a small, traceable supervised dataset from an exported feature snapshot.

The features are deliberately loaded from the Week 3 export rather than
recomputed here.  A target is only added once the following twenty *XNYS
sessions* have completed, and its two endpoint prices are read from the
snapshot visible at that final close.  This keeps label construction separate
from feature engineering and prevents a later price correction from silently
changing an older training example.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .features import BENCHMARK_SYMBOL, FEATURE_VERSION, SNAPSHOT_MODES, TRADING_DAYS_PER_YEAR
from .market_data import YAHOO_SOURCE
from .market_time import _xnys_calendar_for_year, normalize_utc, xnys_session_close_at
from .services import is_valid_symbol, normalize_symbol


FEATURE_COLUMNS = (
    "momentum_5d",
    "momentum_20d",
    "volatility_20d",
    "volume_ratio_20d",
    "drawdown_20d",
    "relative_return_20d",
)
FORWARD_SESSIONS = 20
THRESHOLD_VOLATILITY_MULTIPLIER = 0.5
MINIMUM_LABEL_THRESHOLD = 1e-8
CLASS_MAPPING = {"bearish": 0, "neutral": 1, "bullish": 2}


def load_training_dataset(feature_path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load a Week 3 JSON export and attach only labels mature at its cutoff.

    The feature file is the reproducible input: its SHA-256 and snapshot
    provenance are carried into the returned metadata.  The default price
    loader is the existing revision-aware snapshot API.
    """
    path = Path(feature_path)
    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"feature export is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("feature export must be a JSON object")

    raw_metadata = payload.get("metadata")
    raw_rows = payload.get("rows")
    if not isinstance(raw_metadata, Mapping) or not isinstance(raw_rows, list):
        raise ValueError("feature export must contain object metadata and list rows")
    as_of_time = _parse_timestamp(raw_metadata.get("as_of_time"), "metadata.as_of_time")
    source = _non_empty_string(raw_metadata.get("source"), "metadata.source")
    mode = _non_empty_string(raw_metadata.get("snapshot_mode"), "metadata.snapshot_mode")
    if mode not in SNAPSHOT_MODES:
        raise ValueError(f"metadata.snapshot_mode must be one of: {', '.join(SNAPSHOT_MODES)}")
    feature_version = _non_empty_string(raw_metadata.get("feature_version"), "metadata.feature_version")
    if feature_version != FEATURE_VERSION:
        raise ValueError(
            f"unsupported feature_version {feature_version!r}; expected {FEATURE_VERSION!r}"
        )

    from .market_data_snapshots import get_market_data

    dataset, build_metadata = build_training_dataset(
        raw_rows,
        as_of_time=as_of_time,
        source=source,
        mode=mode,
        price_loader=get_market_data,
        feature_version=feature_version,
    )
    build_metadata.update(
        {
            "feature_file": str(path),
            "feature_file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "feature_export_metadata": dict(raw_metadata),
        }
    )
    return dataset, build_metadata


def build_training_dataset(
    feature_rows: Iterable[Mapping[str, object]],
    *,
    as_of_time: datetime,
    source: str = YAHOO_SOURCE,
    mode: str = "historical_research",
    price_loader: Callable[..., Sequence[object]],
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    feature_version: str = FEATURE_VERSION,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach 20-session excess-return classes to exported feature rows.

    ``price_loader`` follows :func:`app.market_data_snapshots.get_market_data`.
    It is injected so tests can prove the cutoff used for every label.  For a
    row at ``t``, this function asks it for a snapshot at the session close of
    ``t+20`` and retains only the closes at ``t`` and ``t+20``; complete price
    histories are never cached.
    """
    cutoff = normalize_utc(as_of_time)
    if mode not in SNAPSHOT_MODES:
        raise ValueError(f"mode must be one of: {', '.join(SNAPSHOT_MODES)}")
    if feature_version != FEATURE_VERSION:
        raise ValueError(
            f"unsupported feature_version {feature_version!r}; expected {FEATURE_VERSION!r}"
        )
    normalized_benchmark = _validate_symbol(benchmark_symbol, "benchmark_symbol")
    raw_rows = tuple(feature_rows)
    parsed_rows = _parse_feature_rows(raw_rows, benchmark_symbol=normalized_benchmark)
    excluded: Counter[str] = Counter()
    output_rows: list[dict[str, object]] = []
    endpoint_cache: dict[tuple[str, date, date], tuple[float, float] | None] = {}

    def endpoints(symbol: str, decision_date: date, label_end_date: date, label_close: datetime):
        key = (symbol, decision_date, label_end_date)
        if key not in endpoint_cache:
            snapshot = price_loader(symbol, as_of_time=label_close, source=source, mode=mode)
            endpoint_cache[key] = _extract_endpoint_closes(snapshot, decision_date, label_end_date)
        return endpoint_cache[key]

    for feature in parsed_rows:
        decision_date = feature["trading_date"]
        assert isinstance(decision_date, date)
        label_end_date = _label_end_date(decision_date)
        label_available_at = xnys_session_close_at(label_end_date)
        if label_available_at > cutoff:
            excluded["label_not_mature_at_cutoff"] += 1
            continue

        stock_prices = endpoints(feature["symbol"], decision_date, label_end_date, label_available_at)
        benchmark_prices = endpoints(normalized_benchmark, decision_date, label_end_date, label_available_at)
        if stock_prices is None or benchmark_prices is None:
            excluded["missing_endpoint_price"] += 1
            continue
        stock_start, stock_end = stock_prices
        benchmark_start, benchmark_end = benchmark_prices
        forward_excess_return = (stock_end / stock_start - 1.0) - (benchmark_end / benchmark_start - 1.0)
        threshold = max(
            MINIMUM_LABEL_THRESHOLD,
            THRESHOLD_VOLATILITY_MULTIPLIER
            * float(feature["volatility_20d"])
            * math.sqrt(FORWARD_SESSIONS / TRADING_DAYS_PER_YEAR),
        )
        output_rows.append(
            {
                "symbol": feature["symbol"],
                "trading_date": decision_date,
                "decision_time": xnys_session_close_at(decision_date),
                "label_end_date": label_end_date,
                "label_available_at": label_available_at,
                "target": _classify_target(forward_excess_return, threshold),
                "forward_excess_return": forward_excess_return,
                "label_threshold": threshold,
                **{column: feature[column] for column in FEATURE_COLUMNS},
            }
        )

    columns = (
        "symbol",
        "trading_date",
        "decision_time",
        "label_end_date",
        "label_available_at",
        "target",
        "forward_excess_return",
        "label_threshold",
        *FEATURE_COLUMNS,
    )
    dataset = pd.DataFrame(output_rows, columns=columns).sort_values(
        ["trading_date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    metadata: dict[str, Any] = {
        "feature_version": feature_version,
        "source": source,
        "snapshot_mode": mode,
        "as_of_time": cutoff.isoformat(),
        "benchmark_symbol": normalized_benchmark,
        "row_count": len(dataset),
        "input_feature_row_count": len(raw_rows),
        "benchmark_rows_excluded": len(raw_rows) - len(parsed_rows),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "class_mapping": CLASS_MAPPING,
        "feature_columns": list(FEATURE_COLUMNS),
        "label": {
            "forward_sessions": FORWARD_SESSIONS,
            "target_formula": "stock_return_t_to_t_plus_20 - SPY_return_t_to_t_plus_20",
            "threshold_formula": "max(1e-8, 0.5 * volatility_20d * sqrt(20 / 252))",
            "threshold_volatility_multiplier": THRESHOLD_VOLATILITY_MULTIPLIER,
            "minimum_threshold": MINIMUM_LABEL_THRESHOLD,
            "boundary_class": "neutral",
            "price_snapshot": "snapshot at label_end_date XNYS session close",
            "historical_research_limit": (
                "Initial historical backfills are a research assumption, not proof that this system "
                "possessed the bar at that historical close."
                if mode == "historical_research"
                else None
            ),
        },
    }
    return dataset, metadata


def _parse_feature_rows(
    feature_rows: Iterable[Mapping[str, object]], *, benchmark_symbol: str
) -> list[dict[str, object]]:
    parsed: list[dict[str, object]] = []
    seen: set[tuple[str, date]] = set()
    for raw in feature_rows:
        if not isinstance(raw, Mapping):
            raise ValueError("each feature row must be an object")
        symbol = _validate_symbol(raw.get("symbol"), "row.symbol")
        if symbol == benchmark_symbol:
            continue
        trading_date = _parse_date(raw.get("trading_date"), "row.trading_date")
        key = (symbol, trading_date)
        if key in seen:
            raise ValueError(f"duplicate feature row for {symbol} on {trading_date.isoformat()}")
        seen.add(key)
        parsed_row: dict[str, object] = {"symbol": symbol, "trading_date": trading_date}
        for column in FEATURE_COLUMNS:
            parsed_row[column] = _finite_float(raw.get(column), f"row.{column}")
        if float(parsed_row["volatility_20d"]) < 0.0:
            raise ValueError("row.volatility_20d must be non-negative")
        parsed.append(parsed_row)
    return sorted(parsed, key=lambda row: (row["trading_date"], row["symbol"]))


def _label_end_date(decision_date: date) -> date:
    calendar = _xnys_calendar_for_year(decision_date.year)
    # Sixty calendar days covers twenty sessions plus the longest normal US
    # holiday cluster.  The calendar is constructed with a following-year pad.
    sessions = calendar.sessions_in_range(decision_date + timedelta(days=1), decision_date + timedelta(days=60))
    future_dates = [session.date() for session in sessions]
    if len(future_dates) < FORWARD_SESSIONS:
        raise ValueError(f"could not find {FORWARD_SESSIONS} XNYS sessions after {decision_date.isoformat()}")
    return future_dates[FORWARD_SESSIONS - 1]


def _extract_endpoint_closes(
    rows: Sequence[object], decision_date: date, label_end_date: date
) -> tuple[float, float] | None:
    closes: dict[date, float] = {}
    for row in rows:
        row_date = getattr(row, "trading_date", None)
        if row_date not in {decision_date, label_end_date}:
            continue
        close = _finite_float(getattr(row, "close", None), "price.close")
        if close <= 0.0:
            raise ValueError("price.close must be positive")
        if row_date in closes:
            raise ValueError(f"duplicate snapshot price for {row_date.isoformat()}")
        closes[row_date] = close
    if decision_date not in closes or label_end_date not in closes:
        return None
    return closes[decision_date], closes[label_end_date]


def _classify_target(forward_excess_return: float, threshold: float) -> int:
    if forward_excess_return > threshold:
        return CLASS_MAPPING["bullish"]
    if forward_excess_return < -threshold:
        return CLASS_MAPPING["bearish"]
    return CLASS_MAPPING["neutral"]


def _parse_date(value: object, name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 date") from exc
    raise ValueError(f"{name} must be an ISO-8601 date")


def _parse_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        return normalize_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), name=name)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timezone-aware timestamp") from exc


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number")
    return numeric


def _validate_symbol(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a valid symbol")
    symbol = normalize_symbol(value)
    if not is_valid_symbol(symbol):
        raise ValueError(f"{name} must be a valid symbol")
    return symbol


def _non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value
