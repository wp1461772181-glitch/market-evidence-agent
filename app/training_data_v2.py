"""Build the frozen V2 market-only training rows from saved price snapshots.

This module is deliberately a reader, not a downloader or trainer.  It only
accepts the bounded ``historical_research`` quote-close files recorded in the
V2 manifest, verifies their bytes, and derives rows whose 20-session target
is already fully known.  Evidence features and model fitting remain separate
steps.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import exchange_calendars as xcals

from .features import BENCHMARK_SYMBOL, FeatureRow, build_features
from .forecast_contract import (
    ForecastContractError,
    HORIZON_SESSIONS,
    PRICE_BASIS,
    PriceBasisError,
    TARGET_SPEC_VERSION,
    absolute_return,
    classify_absolute_return,
    future_xnys_sessions,
    validate_price_basis,
)
from .market_data import DailyPrice


MANIFEST_SCHEMA_VERSION = "v2-corpus-manifest-v1"
SNAPSHOT_SCHEMA_VERSION = "v2-price-snapshot-v1"
SNAPSHOT_SOURCE = "yahoo-finance-chart"
TRAINING_START = date(2021, 1, 1)
TRAINING_END = date(2026, 8, 31)
STOCK_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")
HISTORY_SESSIONS = 61


@dataclass(frozen=True)
class V2MarketTrainingRow:
    """One leakage-safe market decision for a fixed root question.

    Root rows retain the original P3 contract.  Candidate revision rows keep
    that root's ``anchor_date`` and ``target_end_date`` while exposing the
    later decision date and price separately.  This makes it impossible for a
    revision to quietly turn into a new rolling 20-session target.
    """

    symbol: str
    partition: str
    anchor_date: date
    target_end_date: date
    anchor_close: float
    target_close: float
    absolute_return: float
    label: str
    remaining_sessions: int
    realized_return_from_anchor: float
    momentum_5d: float
    momentum_20d: float
    volatility_20d: float
    volume_ratio_20d: float
    drawdown_20d: float
    relative_return_20d: float
    feature_version: str
    stock_price_sha256: str
    benchmark_price_sha256: str
    row_kind: str = "root"
    root_id: str | None = None
    decision_date: date | None = None
    decision_close: float | None = None
    label_available_at: date | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["anchor_date"] = self.anchor_date.isoformat()
        value["target_end_date"] = self.target_end_date.isoformat()
        for field in ("decision_date", "label_available_at"):
            if value[field] is not None:
                value[field] = value[field].isoformat()
        return value


@dataclass(frozen=True)
class V2MarketTrainingDataset:
    rows: tuple[V2MarketTrainingRow, ...]
    audit: dict[str, Any]

    def records(self) -> list[dict[str, Any]]:
        return [row.as_dict() for row in self.rows]


def load_v2_market_training_data(
    manifest_path: str | Path,
    *,
    symbols: Sequence[str] = STOCK_SYMBOLS,
    include_revision_rows: bool = False,
) -> V2MarketTrainingDataset:
    """Verify saved V2 prices and construct fixed-target V2 market rows.

    ``symbols`` is configurable only to make small fixtures practical.  The
    default requires all five stocks plus SPY, exactly as the production
    manifest contract specifies.  The default preserves the root-only P3
    baseline input.  ``include_revision_rows`` is for the later grouped
    revision experiment; it never changes a root's target or label.
    """
    manifest_location = Path(manifest_path)
    requested_symbols = _normalize_symbols(symbols)
    try:
        manifest_bytes = manifest_location.read_bytes()
    except OSError as exc:
        raise ValueError("dataset manifest cannot be read") from exc
    manifest = _read_json_bytes(manifest_bytes, "dataset manifest")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    _validate_manifest(manifest, requested_symbols)

    snapshots, snapshot_hashes = _load_snapshots(manifest_location.parent, manifest, requested_symbols)
    prices_by_symbol = {symbol: _daily_prices(snapshot) for symbol, snapshot in snapshots.items()}
    features = build_features(prices_by_symbol, benchmark_symbol=BENCHMARK_SYMBOL)
    feature_by_key = {(row.symbol, row.trading_date): row for row in features.rows}

    sessions = _xnys_sessions(TRAINING_START, TRAINING_END)
    partitions = _parse_partitions(manifest["partitions"])
    price_dates = {symbol: {row.trading_date for row in rows} for symbol, rows in prices_by_symbol.items()}
    price_by_date = {
        symbol: {row.trading_date: row for row in rows}
        for symbol, rows in prices_by_symbol.items()
    }
    excluded: Counter[str] = Counter()
    roots_considered: Counter[str] = Counter()
    rows_by_partition: Counter[str] = Counter()
    rows_by_kind: Counter[str] = Counter()
    rows: list[V2MarketTrainingRow] = []

    for symbol in requested_symbols:
        stock_snapshot = snapshots[symbol]
        for index, anchor_date in enumerate(sessions):
            partition = _partition_for(anchor_date, partitions)
            if partition is None:
                excluded["root_outside_partition"] += 1
                continue
            roots_considered[partition] += 1
            if index < HISTORY_SESSIONS - 1:
                excluded["insufficient_history"] += 1
                continue

            target_sessions = future_xnys_sessions(anchor_date, HORIZON_SESSIONS)
            target_end_date = target_sessions[-1]
            if _partition_for(target_end_date, partitions) != partition:
                # This removes labels that would cross train/calibration/test
                # walls as well as roots whose target is beyond the frozen
                # training range.
                excluded["cross_partition_purge"] += 1
                continue

            required_sessions = (*sessions[index - (HISTORY_SESSIONS - 1) : index + 1], *target_sessions)
            if any(
                candidate not in price_dates[symbol] or candidate not in price_dates[BENCHMARK_SYMBOL]
                for candidate in required_sessions
            ):
                excluded["missing_price_session"] += 1
                continue

            try:
                validate_price_basis(
                    metadata=stock_snapshot["price_basis"],
                    anchor_date=anchor_date,
                    target_end_date=target_end_date,
                )
            except (PriceBasisError, ForecastContractError) as exc:
                # These are the contract's deliberate, stable fail-closed
                # outcomes.  Unexpected parser or programming failures must
                # surface instead of being relabelled as a safe exclusion.
                excluded[exc.code] += 1
                continue

            feature = feature_by_key.get((symbol, anchor_date))
            if feature is None:
                excluded["missing_market_feature"] += 1
                continue
            anchor = price_by_date[symbol][anchor_date]
            target = price_by_date[symbol][target_end_date]
            value = absolute_return(anchor_close=anchor.close, target_close=target.close)
            root = _make_row(
                symbol=symbol,
                partition=partition,
                anchor=anchor,
                target=target,
                feature=feature,
                value=value,
                stock_hash=snapshot_hashes[symbol],
                benchmark_hash=snapshot_hashes[BENCHMARK_SYMBOL],
            )
            rows.append(root)
            rows_by_partition[partition] += 1
            rows_by_kind["root"] += 1
            if include_revision_rows:
                revisions = _candidate_revision_rows(
                    root=root,
                    target_sessions=target_sessions,
                    price_by_date=price_by_date[symbol],
                    feature_by_key=feature_by_key,
                )
                rows.extend(revisions)
                rows_by_partition[partition] += len(revisions)
                rows_by_kind["revision"] += len(revisions)

    rows.sort(key=lambda row: (row.partition, row.anchor_date, row.symbol, row.decision_date or row.anchor_date))
    _validate_fixed_target_groups(rows)
    return V2MarketTrainingDataset(
        rows=tuple(rows),
        audit={
            "manifest_sha256": manifest_sha256,
            "input_snapshot_count": len(snapshots),
            "input_rows_by_symbol": {symbol: len(prices_by_symbol[symbol]) for symbol in sorted(prices_by_symbol)},
            "snapshot_sha256": dict(sorted(snapshot_hashes.items())),
            "history_sessions_required": HISTORY_SESSIONS,
            "target_sessions_required": HORIZON_SESSIONS,
            "roots_considered_by_partition": _partition_counts(roots_considered),
            "rows_by_partition": _partition_counts(rows_by_partition),
            "rows_by_kind": {name: rows_by_kind.get(name, 0) for name in ("root", "revision")},
            "revision_candidates_included": include_revision_rows,
            "excluded_by_reason": dict(sorted(excluded.items())),
            "total_rows": len(rows),
        },
    )


def _make_row(
    *,
    symbol: str,
    partition: str,
    anchor: DailyPrice,
    target: DailyPrice,
    feature: FeatureRow,
    value: float,
    stock_hash: str,
    benchmark_hash: str,
) -> V2MarketTrainingRow:
    return V2MarketTrainingRow(
        symbol=symbol,
        partition=partition,
        anchor_date=anchor.trading_date,
        target_end_date=target.trading_date,
        anchor_close=anchor.close,
        target_close=target.close,
        absolute_return=value,
        label=classify_absolute_return(value),
        remaining_sessions=HORIZON_SESSIONS,
        # A root prediction is evaluated from its own anchor; it has no
        # realised movement at decision time.
        realized_return_from_anchor=0.0,
        momentum_5d=feature.momentum_5d,
        momentum_20d=feature.momentum_20d,
        volatility_20d=feature.volatility_20d,
        volume_ratio_20d=feature.volume_ratio_20d,
        drawdown_20d=feature.drawdown_20d,
        relative_return_20d=feature.relative_return_20d,
        feature_version=feature.feature_version,
        stock_price_sha256=stock_hash,
        benchmark_price_sha256=benchmark_hash,
        row_kind="root",
        root_id=_root_id(symbol, anchor.trading_date),
        decision_date=anchor.trading_date,
        decision_close=anchor.close,
        label_available_at=target.trading_date,
    )


def _candidate_revision_rows(
    *,
    root: V2MarketTrainingRow,
    target_sessions: Sequence[date],
    price_by_date: Mapping[date, DailyPrice],
    feature_by_key: Mapping[tuple[str, date], FeatureRow],
) -> tuple[V2MarketTrainingRow, ...]:
    """Build one candidate for each still-revisable session of one root.

    A decision on the first session after the root has nineteen sessions left;
    the target session itself is deliberately absent because the V2 contract
    requires ``remaining_sessions > 0`` for a revision.  Features are looked
    up by the candidate's decision date, so no target price is used as a
    feature.  The target price appears only in the mature shared label.
    """
    if root.row_kind != "root" or root.decision_date != root.anchor_date:
        raise ValueError("revision candidates require a normalized root row")
    target_count = len(target_sessions)
    if target_count != HORIZON_SESSIONS or target_sessions[-1] != root.target_end_date:
        raise ValueError("revision candidate target sessions do not match the root contract")

    candidates: list[V2MarketTrainingRow] = []
    for index, decision_date in enumerate(target_sessions[:-1], start=1):
        remaining = HORIZON_SESSIONS - index
        feature = feature_by_key.get((root.symbol, decision_date))
        decision = price_by_date.get(decision_date)
        if feature is None or decision is None:
            # The root itself was already checked for every required session;
            # keeping this fail-closed protects future callers that provide a
            # narrower fixture or a changed price loader.
            continue
        realized = absolute_return(anchor_close=root.anchor_close, target_close=decision.close)
        candidates.append(
            V2MarketTrainingRow(
                symbol=root.symbol,
                partition=root.partition,
                anchor_date=root.anchor_date,
                target_end_date=root.target_end_date,
                anchor_close=root.anchor_close,
                target_close=root.target_close,
                absolute_return=root.absolute_return,
                label=root.label,
                remaining_sessions=remaining,
                realized_return_from_anchor=realized,
                momentum_5d=feature.momentum_5d,
                momentum_20d=feature.momentum_20d,
                volatility_20d=feature.volatility_20d,
                volume_ratio_20d=feature.volume_ratio_20d,
                drawdown_20d=feature.drawdown_20d,
                relative_return_20d=feature.relative_return_20d,
                feature_version=feature.feature_version,
                stock_price_sha256=root.stock_price_sha256,
                benchmark_price_sha256=root.benchmark_price_sha256,
                row_kind="revision",
                root_id=root.root_id,
                decision_date=decision_date,
                decision_close=decision.close,
                label_available_at=root.target_end_date,
            )
        )
    return tuple(candidates)


def _root_id(symbol: str, anchor_date: date) -> str:
    """Stable group key for splitting/weighting root and revision rows together."""
    return f"{symbol}:{anchor_date.isoformat()}"


def _validate_fixed_target_groups(rows: Sequence[V2MarketTrainingRow]) -> None:
    """Assert the internal P3 invariant shared by roots and revisions.

    This is intentionally a construction-time validation, not a trainer that
    treats the 19 candidates as independent observations.  Later grouped
    modelling must choose an explicit per-root weighting or sampling policy.
    """
    groups: dict[str, list[V2MarketTrainingRow]] = {}
    for row in rows:
        if row.row_kind not in {"root", "revision"} or not row.root_id:
            raise ValueError("V2 row has no valid root grouping identity")
        groups.setdefault(row.root_id, []).append(row)
    for root_id, group in groups.items():
        roots = [row for row in group if row.row_kind == "root"]
        if len(roots) != 1:
            raise ValueError(f"V2 root group must contain exactly one root: {root_id}")
        root = roots[0]
        if root.decision_date != root.anchor_date or root.decision_close != root.anchor_close:
            raise ValueError(f"V2 root decision state is invalid: {root_id}")
        if root.remaining_sessions != HORIZON_SESSIONS or root.realized_return_from_anchor != 0.0:
            raise ValueError(f"V2 root state is invalid: {root_id}")
        expected_remaining = {
            decision_date: HORIZON_SESSIONS - index
            for index, decision_date in enumerate(
                future_xnys_sessions(root.anchor_date, HORIZON_SESSIONS)[:-1], start=1
            )
        }
        for row in group:
            if (
                row.symbol != root.symbol
                or row.partition != root.partition
                or row.anchor_date != root.anchor_date
                or row.anchor_close != root.anchor_close
                or row.target_end_date != root.target_end_date
                or row.target_close != root.target_close
                or row.absolute_return != root.absolute_return
                or row.label != root.label
                or row.label_available_at != root.target_end_date
            ):
                raise ValueError(f"V2 row changes its fixed root target: {root_id}")
            if row.row_kind == "revision":
                if row.decision_date is None or row.decision_close is None:
                    raise ValueError(f"V2 revision has no decision price: {root_id}")
                if not root.anchor_date < row.decision_date < root.target_end_date:
                    raise ValueError(f"V2 revision decision is outside the fixed target: {root_id}")
                if row.remaining_sessions != expected_remaining.get(row.decision_date):
                    raise ValueError(f"V2 revision remaining sessions do not reach the fixed target: {root_id}")
                expected_realized = absolute_return(anchor_close=root.anchor_close, target_close=row.decision_close)
                if not math.isclose(row.realized_return_from_anchor, expected_realized, rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError(f"V2 revision realised return is inconsistent: {root_id}")


def _load_snapshots(
    directory: Path, manifest: Mapping[str, Any], symbols: tuple[str, ...]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    expected = {*symbols, BENCHMARK_SYMBOL}
    entries = manifest.get("price_snapshot_files")
    if not isinstance(entries, list):
        raise ValueError("manifest has no price_snapshot_files list")
    by_symbol: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("manifest price snapshot entry is invalid")
        symbol = entry.get("symbol")
        filename = entry.get("filename")
        if symbol in by_symbol or not isinstance(symbol, str) or Path(str(filename)).name != filename:
            raise ValueError("manifest has duplicate or unsafe price snapshot entries")
        by_symbol[symbol] = entry
    if set(by_symbol) != expected:
        raise ValueError("manifest must contain exactly the requested stock snapshots and SPY")

    snapshots: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for symbol in sorted(expected):
        entry = by_symbol[symbol]
        expected_hash = entry.get("sha256")
        if not _valid_sha256(expected_hash):
            raise ValueError(f"manifest price snapshot hash is invalid: {symbol}")
        try:
            payload = (directory / str(entry["filename"])).read_bytes()
        except OSError as exc:
            raise ValueError(f"saved price snapshot is missing: {entry['filename']}") from exc
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"saved price snapshot hash mismatch: {entry['filename']}")
        snapshot = _read_json_bytes(payload, f"price snapshot {symbol}")
        _validate_snapshot(snapshot, entry, symbol)
        snapshots[symbol] = snapshot
        hashes[symbol] = actual_hash
    return snapshots, hashes


def _validate_manifest(manifest: Mapping[str, Any], symbols: tuple[str, ...]) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("manifest has an unsupported schema")
    if manifest.get("mode") != "historical_research":
        raise ValueError("manifest must be historical_research")
    if manifest.get("target_spec_version") != TARGET_SPEC_VERSION or manifest.get("price_basis") != PRICE_BASIS:
        raise ValueError("manifest has an incompatible V2 target or price basis")
    if manifest.get("symbols") != list(symbols):
        raise ValueError("manifest stocks do not match requested symbols")
    if manifest.get("training_range") != {"start": TRAINING_START.isoformat(), "end": TRAINING_END.isoformat()}:
        raise ValueError("manifest must use the fixed V2 training range")
    expected_contract = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "mode": "historical_research",
        "symbols": [*symbols, BENCHMARK_SYMBOL],
        "source": SNAPSHOT_SOURCE,
        "price_basis": PRICE_BASIS,
        "close_field": "indicators.quote.close",
        "cash_dividend_reinvestment_included": False,
        "start_date": TRAINING_START.isoformat(),
        "end_date": TRAINING_END.isoformat(),
    }
    if manifest.get("price_snapshot_contract") != expected_contract:
        raise ValueError("manifest price snapshot contract is incompatible")
    _parse_partitions(manifest.get("partitions"))


def _validate_snapshot(snapshot: Mapping[str, Any], entry: Mapping[str, Any], symbol: str) -> None:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION or snapshot.get("mode") != "historical_research":
        raise ValueError(f"price snapshot has an incompatible schema or mode: {symbol}")
    if snapshot.get("symbol") != symbol or snapshot.get("source") != SNAPSHOT_SOURCE:
        raise ValueError(f"price snapshot symbol or source is invalid: {symbol}")
    if snapshot.get("requested_range") != {
        "start_date": TRAINING_START.isoformat(),
        "end_date": TRAINING_END.isoformat(),
    }:
        raise ValueError(f"price snapshot range is invalid: {symbol}")
    _parse_observed_at(snapshot.get("observed_at"))
    basis = snapshot.get("price_basis")
    if not isinstance(basis, Mapping) or basis.get("basis") != PRICE_BASIS or not basis.get("provider_behavior_verified"):
        raise ValueError(f"price snapshot price basis is unverified: {symbol}")
    if basis.get("cash_dividend_reinvestment_included") is not False or basis.get("price_return_only") is not True:
        raise ValueError(f"price snapshot has an incompatible return convention: {symbol}")
    if snapshot.get("coverage_complete") is not True or snapshot.get("missing_sessions") not in ([], None):
        raise ValueError(f"price snapshot coverage is incomplete: {symbol}")
    if entry.get("symbol") != symbol or entry.get("source") != SNAPSHOT_SOURCE:
        raise ValueError(f"manifest entry does not match snapshot: {symbol}")
    if entry.get("mode") != "historical_research" or entry.get("price_basis") != PRICE_BASIS:
        raise ValueError(f"manifest entry has an incompatible contract: {symbol}")
    if entry.get("start_date") != TRAINING_START.isoformat() or entry.get("end_date") != TRAINING_END.isoformat():
        raise ValueError(f"manifest entry range is invalid: {symbol}")
    if entry.get("coverage_complete") is not True or entry.get("provider_behavior_verified") is not True:
        raise ValueError(f"manifest entry is not verified: {symbol}")
    _parse_observed_at(entry.get("observed_at"))
    if entry.get("row_count") != snapshot.get("row_count") or entry.get("observed_at") != snapshot.get("observed_at"):
        raise ValueError(f"manifest entry metadata does not match snapshot: {symbol}")


def _daily_prices(snapshot: Mapping[str, Any]) -> list[DailyPrice]:
    symbol = snapshot["symbol"]
    values = snapshot.get("prices")
    if not isinstance(values, list) or snapshot.get("row_count") != len(values):
        raise ValueError(f"price snapshot rows are invalid: {symbol}")
    rows: list[DailyPrice] = []
    previous: date | None = None
    for item in values:
        if not isinstance(item, Mapping):
            raise ValueError(f"price snapshot has a malformed row: {symbol}")
        try:
            trading_date = date.fromisoformat(str(item["trading_date"]))
            numeric = [float(item[field]) for field in ("open", "high", "low", "close")]
            volume = int(item["volume"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"price snapshot has an invalid OHLCV row: {symbol}") from exc
        if (
            trading_date < TRAINING_START
            or trading_date > TRAINING_END
            or previous is not None and trading_date <= previous
            or not all(math.isfinite(value) and value > 0.0 for value in numeric)
            or volume < 0
        ):
            raise ValueError(f"price snapshot has an invalid OHLCV row: {symbol}")
        previous = trading_date
        rows.append(DailyPrice(symbol, trading_date, *numeric, volume, source=SNAPSHOT_SOURCE))
    if {row.trading_date for row in rows} != set(_xnys_sessions(TRAINING_START, TRAINING_END)):
        raise ValueError(f"price snapshot does not contain every fixed XNYS session: {symbol}")
    return rows


def _parse_partitions(value: object) -> dict[str, tuple[date, date]]:
    if not isinstance(value, Mapping):
        raise ValueError("manifest partitions are invalid")
    expected_names = ("train", "calibration", "test")
    # Manifests are written with sorted JSON keys, so their object iteration
    # order is not a semantic part of the train/calibration/test contract.
    if set(value) != set(expected_names):
        raise ValueError("manifest partitions must contain train, calibration, test")
    parsed: dict[str, tuple[date, date]] = {}
    for name in expected_names:
        item = value[name]
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("manifest partition range is invalid")
        try:
            start, end = date.fromisoformat(item[0]), date.fromisoformat(item[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("manifest partition range is invalid") from exc
        parsed[name] = (start, end)
    if parsed != {
        "train": (date(2021, 1, 1), date(2023, 12, 31)),
        "calibration": (date(2024, 1, 1), date(2024, 12, 31)),
        "test": (date(2025, 1, 1), date(2026, 8, 31)),
    }:
        raise ValueError("manifest partitions do not match the fixed V2 split")
    return parsed


def _partition_for(value: date, partitions: Mapping[str, tuple[date, date]]) -> str | None:
    for name, (start, end) in partitions.items():
        if start <= value <= end:
            return name
    return None


def _xnys_sessions(start_date: date, end_date: date) -> tuple[date, ...]:
    calendar = xcals.get_calendar(
        "XNYS",
        start=(start_date - timedelta(days=7)).isoformat(),
        end=(end_date + timedelta(days=7)).isoformat(),
    )
    first = calendar.date_to_session(start_date, direction="next")
    last = calendar.date_to_session(end_date, direction="previous")
    return tuple(item.date() for item in calendar.sessions_in_range(first, last))


def _normalize_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(symbol).strip().upper() for symbol in symbols)
    if not normalized or len(set(normalized)) != len(normalized) or BENCHMARK_SYMBOL in normalized:
        raise ValueError("symbols must be a non-empty unique stock sequence without SPY")
    if any(symbol not in STOCK_SYMBOLS for symbol in normalized):
        raise ValueError("symbols must be selected from the five V2 stocks")
    return normalized


def _read_json_bytes(value: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be an object")
    return parsed


def _parse_observed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("price snapshot observed_at is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("price snapshot observed_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("price snapshot observed_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _partition_counts(values: Counter[str]) -> dict[str, int]:
    return {name: values.get(name, 0) for name in ("train", "calibration", "test")}
