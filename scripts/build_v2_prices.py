"""Build bounded, resumable V2 quote-close price snapshots.

The default command is a read-only plan. ``--apply`` is required before a
network request or file write. Historical downloads are always labelled
``historical_research`` and retain their true fetch completion time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence

import exchange_calendars as xcals

from app.market_data import MarketDataFetchResult, YahooFinanceProvider


STOCK_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")
BENCHMARK_SYMBOL = "SPY"
SNAPSHOT_SYMBOLS = (*STOCK_SYMBOLS, BENCHMARK_SYMBOL)
DEFAULT_START_DATE = date(2021, 1, 1)
DEFAULT_END_DATE = date(2026, 8, 31)
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "v2"
MANIFEST_NAME = "dataset-manifest.json"
SNAPSHOT_SCHEMA_VERSION = "v2-price-snapshot-v1"
MANIFEST_SCHEMA_VERSION = "v2-corpus-manifest-v1"
PRICE_BASIS = "provider_quote_close_v1"

PriceFetcher = Callable[[str, date, date], MarketDataFetchResult]


def plan_price_snapshots(
    *,
    output_dir: Path,
    symbols: Sequence[str] = SNAPSHOT_SYMBOLS,
    start_date: date = DEFAULT_START_DATE,
    end_date: date = DEFAULT_END_DATE,
    max_symbols: int = 1,
) -> dict:
    """Report pending files without calling a provider or creating files."""
    _validate_request(symbols, start_date, end_date, max_symbols)
    manifest_path = output_dir / MANIFEST_NAME
    manifest = _read_manifest(manifest_path) if manifest_path.exists() else None
    existing = _validated_existing_entries(output_dir, manifest) if manifest else {}
    pending = [symbol for symbol in symbols if symbol not in existing]
    return {
        "operation": "plan",
        "writes": False,
        "mode": "historical_research",
        "symbols": list(symbols),
        "training_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "max_symbols": max_symbols,
        "already_saved_symbols": sorted(existing),
        "next_symbols": pending[:max_symbols],
        "pending_symbol_count": len(pending),
        "price_basis": PRICE_BASIS,
    }


def build_price_snapshots(
    *,
    output_dir: Path,
    resume: bool,
    max_symbols: int,
    fetcher: PriceFetcher | None = None,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
    symbols: Sequence[str] = SNAPSHOT_SYMBOLS,
    start_date: date = DEFAULT_START_DATE,
    end_date: date = DEFAULT_END_DATE,
) -> dict:
    """Fetch and save at most ``max_symbols`` immutable price snapshot files."""
    normalised_symbols = _validate_request(symbols, start_date, end_date, max_symbols)
    manifest_path = output_dir / MANIFEST_NAME
    if output_dir.exists() and not resume:
        raise ValueError("output directory exists; use --resume or a new directory")
    if resume and output_dir.exists() and not manifest_path.exists():
        raise ValueError("resume requires an existing dataset-manifest.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(manifest_path) if manifest_path.exists() else _new_manifest(normalised_symbols, start_date, end_date)
    _validate_manifest_contract(manifest, normalised_symbols, start_date, end_date)
    existing = _validated_existing_entries(output_dir, manifest)
    pending = [symbol for symbol in normalised_symbols if symbol not in existing]
    selected = pending[:max_symbols]
    provider_fetch = fetcher or YahooFinanceProvider().fetch_daily_prices_with_metadata
    created: list[str] = []
    errors: list[dict[str, str]] = []

    for symbol in selected:
        try:
            result = provider_fetch(symbol, start_date, end_date)
            observed_at = _utc_now(now_factory())
            snapshot = _snapshot_payload(
                symbol=symbol,
                result=result,
                start_date=start_date,
                end_date=end_date,
                observed_at=observed_at,
            )
            payload = _canonical_json_bytes(snapshot)
            filename = _snapshot_filename(symbol, start_date, end_date)
            destination = output_dir / filename
            if destination.exists():
                raise ValueError(f"refusing to overwrite existing price snapshot: {filename}")
            _write_bytes_atomically(destination, payload)
            manifest["price_snapshot_files"].append(
                {
                    "symbol": symbol,
                    "filename": filename,
                    "sha256": _sha256(payload),
                    "source": snapshot["source"],
                    "mode": snapshot["mode"],
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "observed_at": observed_at.isoformat(),
                    "row_count": snapshot["row_count"],
                    "coverage_complete": snapshot["coverage_complete"],
                    "missing_session_count": len(snapshot["missing_sessions"]),
                    "price_basis": snapshot["price_basis"]["basis"],
                    "provider_behavior_verified": snapshot["price_basis"]["provider_behavior_verified"],
                    "corporate_action_count": len(snapshot["price_basis"]["corporate_actions"]),
                }
            )
            _write_manifest(manifest_path, manifest)
            created.append(symbol)
        except Exception as exc:
            errors.append({"symbol": symbol, "reason": str(exc)})

    return {
        "operation": "apply",
        "mode": "historical_research",
        "created_symbols": created,
        "saved_total": len(manifest["price_snapshot_files"]),
        "remaining_symbols": [symbol for symbol in normalised_symbols if symbol not in {*existing, *created}],
        "errors": errors,
        "price_basis": PRICE_BASIS,
    }


def _snapshot_payload(
    *,
    symbol: str,
    result: MarketDataFetchResult,
    start_date: date,
    end_date: date,
    observed_at: datetime,
) -> dict:
    prices = list(result.prices)
    _validate_prices(symbol, prices, start_date, end_date)
    metadata = result.price_basis
    if metadata.basis != PRICE_BASIS:
        raise ValueError("provider returned an unsupported price basis")
    if not metadata.provider_behavior_verified:
        raise ValueError("provider quote-close behavior is not verified")
    actual_dates = {row.trading_date for row in prices}
    expected_dates = _xnys_sessions(start_date, end_date)
    missing_sessions = sorted(expected_dates - actual_dates)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "mode": "historical_research",
        "symbol": symbol,
        "source": prices[0].source,
        "requested_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "observed_at": observed_at.isoformat(),
        "price_basis": {
            "basis": metadata.basis,
            "provider_behavior_verified": metadata.provider_behavior_verified,
            "adjusted_close_present": metadata.adjusted_close_present,
            "adjusted_close_matches_quote": metadata.adjusted_close_matches_quote,
            "corporate_actions_available": metadata.corporate_actions_available,
            "corporate_actions_response_shape": metadata.corporate_actions_response_shape,
            "corporate_actions": [
                {
                    "kind": action.kind,
                    "effective_date": action.effective_date.isoformat() if action.effective_date else None,
                    "known": action.known,
                    "amount": action.amount,
                    "numerator": action.numerator,
                    "denominator": action.denominator,
                }
                for action in metadata.corporate_actions
            ],
            "verification_notes": list(metadata.verification_notes),
            "price_return_only": True,
            "cash_dividend_reinvestment_included": False,
        },
        "coverage_complete": not missing_sessions,
        "missing_sessions": [item.isoformat() for item in missing_sessions],
        "row_count": len(prices),
        "prices": [
            {
                "trading_date": row.trading_date.isoformat(),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            for row in prices
        ],
    }


def _validate_request(symbols: Sequence[str], start_date: date, end_date: date, max_symbols: int) -> tuple[str, ...]:
    normalised = tuple(symbol.strip().upper() for symbol in symbols)
    if normalised != tuple(dict.fromkeys(normalised)) or not normalised:
        raise ValueError("symbols must be a non-empty unique sequence")
    if any(symbol not in SNAPSHOT_SYMBOLS for symbol in normalised):
        raise ValueError("symbols must be selected from the V2 stocks and SPY")
    if start_date != DEFAULT_START_DATE or end_date != DEFAULT_END_DATE:
        raise ValueError("V2 price snapshots use the fixed 2021-01-01 to 2026-08-31 range")
    if not 1 <= max_symbols <= len(normalised):
        raise ValueError(f"max_symbols must be between 1 and {len(normalised)}")
    return normalised


def _new_manifest(symbols: Sequence[str], start_date: date, end_date: date) -> dict:
    stocks = [symbol for symbol in symbols if symbol != BENCHMARK_SYMBOL]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "mode": "historical_research",
        "symbols": stocks,
        "training_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "source_snapshot_files": [],
        "price_snapshot_files": [],
        "price_snapshot_contract": _price_snapshot_contract(symbols, start_date, end_date),
        "target_spec_version": "absolute-close-v1",
        "price_basis": PRICE_BASIS,
        "extraction_schema_version": "pending_P2",
        "partitions": {
            "train": ["2021-01-01", "2023-12-31"],
            "calibration": ["2024-01-01", "2024-12-31"],
            "test": ["2025-01-01", "2026-08-31"],
        },
        "discovery_incomplete": {},
    }


def _validate_manifest_contract(manifest: dict, symbols: Sequence[str], start_date: date, end_date: date) -> None:
    if manifest.get("mode") != "historical_research" or manifest.get("price_basis") != PRICE_BASIS:
        raise ValueError("existing manifest has an incompatible V2 price contract")
    if manifest.get("symbols") != [symbol for symbol in symbols if symbol != BENCHMARK_SYMBOL]:
        raise ValueError("resume symbols do not match the saved manifest")
    if manifest.get("training_range") != {"start": start_date.isoformat(), "end": end_date.isoformat()}:
        raise ValueError("resume range does not match the saved manifest")
    if not isinstance(manifest.get("price_snapshot_files"), list):
        raise ValueError("existing manifest has no price_snapshot_files list")
    manifest["price_snapshot_contract"] = _price_snapshot_contract(symbols, start_date, end_date)


def _price_snapshot_contract(symbols: Sequence[str], start_date: date, end_date: date) -> dict:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "mode": "historical_research",
        "symbols": list(symbols),
        "source": "yahoo-finance-chart",
        "price_basis": PRICE_BASIS,
        "close_field": "indicators.quote.close",
        "cash_dividend_reinvestment_included": False,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def _read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("existing dataset manifest cannot be read") from exc
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("existing manifest has an unsupported schema")
    return value


def _validated_existing_entries(output_dir: Path, manifest: dict | None) -> dict[str, dict]:
    if manifest is None:
        return {}
    entries: dict[str, dict] = {}
    for entry in manifest.get("price_snapshot_files", []):
        if not isinstance(entry, dict):
            raise ValueError("manifest price snapshot entry is invalid")
        symbol = entry.get("symbol")
        filename = entry.get("filename")
        expected_hash = entry.get("sha256")
        if not isinstance(symbol, str) or symbol in entries or Path(str(filename)).name != filename:
            raise ValueError("manifest has duplicate or unsafe price snapshot entries")
        if not isinstance(expected_hash, str):
            raise ValueError("manifest price snapshot hash is invalid")
        try:
            payload = (output_dir / filename).read_bytes()
        except OSError as exc:
            raise ValueError(f"saved price snapshot is missing: {filename}") from exc
        if _sha256(payload) != expected_hash:
            raise ValueError(f"saved price snapshot hash mismatch: {filename}")
        entries[symbol] = entry
    return entries


def _validate_prices(symbol: str, prices: list, start_date: date, end_date: date) -> None:
    if not prices:
        raise ValueError("provider returned no price rows")
    dates = [row.trading_date for row in prices]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("provider price rows must be sorted with unique dates")
    if any(row.symbol != symbol for row in prices):
        raise ValueError("provider price symbol does not match requested symbol")
    if any(row.trading_date < start_date or row.trading_date > end_date for row in prices):
        raise ValueError("provider returned a date outside the requested range")


def _xnys_sessions(start_date: date, end_date: date) -> set[date]:
    # Include calendar padding so date_to_session can move a holiday boundary
    # to its next/previous trading day without hitting the calendar's bounds.
    calendar = xcals.get_calendar(
        "XNYS",
        start=(start_date - timedelta(days=7)).isoformat(),
        end=(end_date + timedelta(days=7)).isoformat(),
    )
    # The configured V2 range begins on New Year's Day, which is not an XNYS
    # session.  The request bounds are calendar dates, so normalise each bound
    # to its first/last session before asking exchange_calendars for a range.
    first = calendar.date_to_session(start_date, direction="next")
    last = calendar.date_to_session(end_date, direction="previous")
    return {item.date() for item in calendar.sessions_in_range(first, last)}


def _snapshot_filename(symbol: str, start_date: date, end_date: date) -> str:
    return f"prices-{symbol}-{start_date.isoformat()}-{end_date.isoformat()}.json"


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _write_manifest(path: Path, value: dict) -> None:
    _write_bytes_atomically(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create bounded V2 historical quote-close snapshots")
    parser.add_argument("--plan", action="store_true", help="report work only; this is the default")
    parser.add_argument("--apply", action="store_true", help="allow provider requests and writes")
    parser.add_argument("--max-symbols", type=int, default=1, help="at most this many symbols per apply run")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.plan and args.apply:
        parser.error("choose --plan or --apply")
    if args.apply:
        result = build_price_snapshots(output_dir=args.output_dir, resume=args.resume, max_symbols=args.max_symbols)
    else:
        result = plan_price_snapshots(output_dir=args.output_dir, max_symbols=args.max_symbols)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
