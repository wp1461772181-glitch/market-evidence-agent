"""Backfill point-in-time OHLCV bars for a ten-year Jev replay window.

The default run downloads and validates the provider response but does not
write to PostgreSQL. Pass ``--apply`` to append the bars through the existing
revision-aware market ingestion path. A short warm-up window is included so
the first monthly replay has enough bars for its market features.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.forecast_contract import latest_completed_xnys_session
from app.features import BENCHMARK_SYMBOL
from app.forecast_jobs import SUPPORTED_STOCKS
from app.market_data import MarketDataFetchResult, YahooFinanceProvider, YAHOO_SOURCE
from app.market_data_ingestion import ingest_market_data


REPLAY_SYMBOLS = tuple(sorted((*SUPPORTED_STOCKS, BENCHMARK_SYMBOL)))
WARMUP_DAYS = 120
MIN_EXPECTED_BARS_PER_YEAR = 220


def _ten_year_start(end: date) -> date:
    try:
        return end.replace(year=end.year - 10)
    except ValueError:
        return end.replace(year=end.year - 10, day=28)


def fetch_and_validate(
    *,
    start_date: date,
    end_date: date,
    symbols: tuple[str, ...] = REPLAY_SYMBOLS,
    provider: Any | None = None,
) -> dict[str, MarketDataFetchResult]:
    market_provider = provider or YahooFinanceProvider()
    results: dict[str, MarketDataFetchResult] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        requests = {
            pool.submit(market_provider.fetch_daily_prices_with_metadata, symbol, start_date, end_date): symbol
            for symbol in symbols
        }
        for future in as_completed(requests):
            symbol = requests[future]
            result = future.result()
            rows = result.prices
            expected_floor = MIN_EXPECTED_BARS_PER_YEAR * max(1, (end_date - start_date).days // 365)
            if not rows or len(rows) < expected_floor:
                raise RuntimeError(f"{symbol}: provider returned too few daily bars for the requested history")
            if rows[-1].trading_date != end_date:
                raise RuntimeError(f"{symbol}: provider history does not reach the latest completed session")
            if rows[0].trading_date > start_date + timedelta(days=7):
                raise RuntimeError(f"{symbol}: provider history starts too late for the requested replay window")
            if not result.price_basis.provider_behavior_verified:
                raise RuntimeError(f"{symbol}: Yahoo price-basis behavior could not be verified")
            results[symbol] = result
    return {symbol: results[symbol] for symbol in symbols}


def ingest_backfill(
    *,
    results: dict[str, MarketDataFetchResult],
    start_date: date,
    end_date: date,
    observed_at: datetime,
) -> dict[str, dict[str, int | str]]:
    summaries: dict[str, dict[str, int | str]] = {}
    for symbol, result in results.items():
        outcome = ingest_market_data(
            [symbol],
            start_date,
            end_date,
            observed_at,
            source=YAHOO_SOURCE,
            fetcher=lambda requested_symbol, _start, _end, rows=list(result.prices): rows,
            now_factory=lambda: observed_at,
        )
        summaries[symbol] = {
            "status": outcome.status,
            "inserted_count": outcome.inserted_count,
            "skipped_count": outcome.skipped_count,
        }
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", type=date.fromisoformat, help="latest completed XNYS session; defaults to today")
    parser.add_argument("--apply", action="store_true", help="append validated backfill rows to the local database")
    args = parser.parse_args()
    end_date = args.end_date or latest_completed_xnys_session(datetime.now(UTC))
    replay_start = _ten_year_start(end_date)
    market_start = replay_start - timedelta(days=WARMUP_DAYS)
    results = fetch_and_validate(start_date=market_start, end_date=end_date)

    print(f"Replay window: {replay_start} through {end_date}; OHLCV warm-up begins {market_start}")
    total = 0
    for symbol, result in results.items():
        splits = [action.effective_date.isoformat() for action in result.price_basis.corporate_actions
                  if action.kind == "split" and action.effective_date is not None]
        total += len(result.prices)
        print(
            f"{symbol}: {len(result.prices)} bars, {result.prices[0].trading_date}.."
            f"{result.prices[-1].trading_date}, verified={result.price_basis.provider_behavior_verified}, "
            f"split_dates={','.join(splits) if splits else 'none'}"
        )
    print(f"Validated total: {total} daily bars across {len(results)} symbols")
    if not args.apply:
        print("Preview only. Pass --apply to persist the append-only market revisions.")
        return 0

    observed_at = datetime.now(UTC)
    summaries = ingest_backfill(
        results=results,
        start_date=market_start,
        end_date=end_date,
        observed_at=observed_at,
    )
    inserted = sum(int(item["inserted_count"]) for item in summaries.values())
    skipped = sum(int(item["skipped_count"]) for item in summaries.values())
    for symbol, item in summaries.items():
        print(f"{symbol}: {item['status']}, inserted={item['inserted_count']}, skipped={item['skipped_count']}")
    print(f"Ingestion completed at {observed_at.isoformat()}: inserted={inserted}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
