"""Refresh stale daily bars for the supported stocks and their benchmark.

The launchd job checks hourly, but a symbol is fetched only when a completed
XNYS session is newer than the latest locally stored bar. Each symbol is an
independent ingestion run, so one provider failure does not block the others
and the next hourly check retries only stale symbols.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv


def refresh_once(*, now: datetime | None = None) -> dict[str, object]:
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env", override=False)

    # Import after loading the project environment because DATABASE_URL is
    # read when app.database creates its SQLAlchemy engine.
    from sqlalchemy import func, select

    from .database import SessionLocal
    from .features import BENCHMARK_SYMBOL
    from .forecast_contract import latest_completed_xnys_session
    from .forecast_jobs import SUPPORTED_STOCKS
    from .market_data import YAHOO_SOURCE, fetch_daily_prices
    from .market_data_ingestion import ingest_market_data
    from .models import MarketPrice, MarketPriceRevision

    instant = (now or datetime.now(UTC)).astimezone(UTC)
    latest_session = latest_completed_xnys_session(instant)
    symbols = sorted((*SUPPORTED_STOCKS, BENCHMARK_SYMBOL))
    results: dict[str, dict[str, object]] = {}

    for symbol in symbols:
        with SessionLocal() as db:
            latest = db.scalar(
                select(func.max(MarketPriceRevision.trading_date)).where(
                    MarketPriceRevision.symbol == symbol,
                    MarketPriceRevision.source == YAHOO_SOURCE,
                )
            )
            if latest is None:
                latest = db.scalar(
                    select(func.max(MarketPrice.trading_date)).where(
                        MarketPrice.symbol == symbol,
                        MarketPrice.source == YAHOO_SOURCE,
                    )
                )

        if latest is not None and latest >= latest_session:
            results[symbol] = {"status": "current", "latest_trading_date": latest.isoformat()}
            continue

        # Existing data is extended from its last stored day; a new install
        # gets a bounded one-year seed so charts are still useful immediately.
        start_date = latest + timedelta(days=1) if latest is not None else latest_session - timedelta(days=370)
        try:
            summary = ingest_market_data(
                [symbol],
                start_date,
                latest_session,
                instant,
                fetcher=fetch_daily_prices,
            )
            results[symbol] = {
                "status": "updated" if summary.inserted_count else "no_new_rows",
                "from": start_date.isoformat(),
                "through": latest_session.isoformat(),
                "inserted_count": summary.inserted_count,
                "skipped_count": summary.skipped_count,
            }
        except Exception as exc:
            # Error messages can contain provider response details. Keep the
            # persistent launchd log limited to a stable exception class.
            results[symbol] = {"status": "failed", "error_type": type(exc).__name__}

    return {
        "checked_at": instant.isoformat(),
        "latest_completed_session": latest_session.isoformat(),
        "symbols": results,
        "failed_symbols": [symbol for symbol, row in results.items() if row["status"] == "failed"],
    }


def main() -> None:
    print(json.dumps(refresh_once(), sort_keys=True))


if __name__ == "__main__":
    main()
