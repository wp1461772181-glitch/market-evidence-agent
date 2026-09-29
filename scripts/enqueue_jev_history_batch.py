"""Queue point-in-time monthly Jev historical replays for the Magnificent Seven.

Preview is the default. Pass ``--apply`` to create durable historical replay
jobs through the local V2 API. Only matured XNYS month-end targets with at
least one public SEC filing and a complete stock/SPY price lookback are queued.
Every result remains tagged ``historical_research`` and never enters the live
observed calibration cohort.
"""

from __future__ import annotations

import argparse
import json
import time
from calendar import monthrange
from datetime import UTC, date, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from exchange_calendars import get_calendar
from sqlalchemy import select

from app.database import SessionLocal
from app.forecast_contract import future_xnys_sessions, latest_completed_xnys_session
from app.forecast_jobs import SUPPORTED_STOCKS
from app.forecast_v2_models import ForecastJobV2, ForecastVersionV2
from app.market_data import YAHOO_SOURCE
from app.market_time import xnys_session_close_at
from app.models import MarketPriceRevision, SecFilingInventory


DEFAULT_API = "http://127.0.0.1:8000"
SYMBOLS = tuple(SUPPORTED_STOCKS)


def _month(value: str) -> tuple[int, int]:
    try:
        parsed = date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must use YYYY-MM") from exc
    if parsed.strftime("%Y-%m") != value:
        raise argparse.ArgumentTypeError("month must use YYYY-MM")
    return parsed.year, parsed.month


def _month_range(start: tuple[int, int], end: tuple[int, int]):
    year, month = start
    while (year, month) <= end:
        yield year, month
        month += 1
        if month == 13:
            year += 1
            month = 1


def _month_end_session(calendar, year: int, month: int) -> date | None:
    end_day = monthrange(year, month)[1]
    sessions = calendar.sessions_in_range(
        pd.Timestamp(date(year, month, 1)), pd.Timestamp(date(year, month, end_day))
    )
    return sessions[-1].date() if len(sessions) else None


def _request_key(symbol: str, anchor: date) -> str:
    return f"jev-history-monthly-v1-{symbol}-{anchor.isoformat()}"


def _public_sec_dates(rows: list[SecFilingInventory]) -> list[datetime]:
    accepted = []
    for row in rows:
        if row.review_status == "rejected" or not row.accepted_at:
            continue
        try:
            value = datetime.fromisoformat(row.accepted_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if value.tzinfo is not None and value.utcoffset() is not None:
            accepted.append(value.astimezone(UTC))
    return accepted


def build_schedule(*, start_month: str, through_month: str):
    start, end = _month(start_month), _month(through_month)
    if start > end:
        raise ValueError("start month must be on or before through month")
    now = datetime.now(UTC)
    latest = latest_completed_xnys_session(now)
    calendar = get_calendar("XNYS")
    entries = []

    with SessionLocal() as db:
        market_rows = db.scalars(
            select(MarketPriceRevision).where(
                MarketPriceRevision.source == YAHOO_SOURCE,
                MarketPriceRevision.is_initial_backfill.is_(True),
                MarketPriceRevision.symbol.in_((*SYMBOLS, "SPY")),
            )
        ).all()
        dates_by_symbol: dict[str, set[date]] = {}
        for row in market_rows:
            dates_by_symbol.setdefault(row.symbol, set()).add(row.trading_date)

        filings_by_symbol = {
            symbol: _public_sec_dates(list(db.scalars(
                select(SecFilingInventory).where(SecFilingInventory.symbol == symbol)
            )))
            for symbol in SYMBOLS
        }
        existing_pairs: set[tuple[str, date]] = set()
        for root in db.scalars(select(ForecastVersionV2).where(ForecastVersionV2.root_id == ForecastVersionV2.id)):
            manifest = root.model_manifest if isinstance(root.model_manifest, dict) else {}
            contract = root.target_contract if isinstance(root.target_contract, dict) else {}
            if manifest.get("time_mode") == "historical_research" and contract.get("anchor_date"):
                try:
                    existing_pairs.add((root.symbol, date.fromisoformat(contract["anchor_date"])))
                except ValueError:
                    pass
        existing_keys = set(db.scalars(
            select(ForecastJobV2.idempotency_key).where(ForecastJobV2.time_mode == "historical_research")
        ))

        for year, month in _month_range(start, end):
            anchor = _month_end_session(calendar, year, month)
            if anchor is None:
                continue
            decision_at = xnys_session_close_at(anchor)
            target_end = future_xnys_sessions(anchor, 20)[-1]
            if target_end > latest:
                continue
            for symbol in SYMBOLS:
                key = _request_key(symbol, anchor)
                if (symbol, anchor) in existing_pairs or key in existing_keys:
                    continue
                if not any(published_at <= decision_at for published_at in filings_by_symbol[symbol]):
                    continue
                stock_dates = sorted(day for day in dates_by_symbol.get(symbol, ()) if day <= anchor)
                bench_dates = sorted(day for day in dates_by_symbol.get("SPY", ()) if day <= anchor)
                if len(stock_dates) < 21 or len(bench_dates) < 21 or stock_dates[-1] != anchor or bench_dates[-1] != anchor:
                    continue
                entries.append({
                    "symbol": symbol,
                    "decision_date": anchor,
                    "target_end_date": target_end,
                    "source_count": sum(published_at <= decision_at for published_at in filings_by_symbol[symbol]),
                    "idempotency_key": key,
                })
    return entries, latest


def _enqueue(entry: dict, *, api_base: str, timeout: float = 30.0) -> dict:
    payload = json.dumps({
        "symbol": entry["symbol"],
        "decision_date": entry["decision_date"].isoformat(),
        "source_refs": [],
    }).encode("utf-8")
    request = Request(
        f"{api_base.rstrip('/')}/v2/historical-replays",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Idempotency-Key": entry["idempotency_key"]},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
            if response.status != 202:
                raise RuntimeError(f"unexpected API status {response.status}")
            return result
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{entry['symbol']} {entry['decision_date']}: HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"local V2 API is unavailable: {exc.reason}") from exc


def main() -> int:
    today = datetime.now(UTC).date()
    start_default = date(today.year - 10, today.month, 1).strftime("%Y-%m")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-month", default=start_default, type=lambda value: _validate_month(value))
    parser.add_argument("--through-month", type=lambda value: _validate_month(value))
    parser.add_argument("--api-base", default=DEFAULT_API)
    parser.add_argument("--limit", type=int, help="queue only the first N entries")
    parser.add_argument("--apply", action="store_true", help="queue jobs; without this flag only preview the schedule")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    latest = latest_completed_xnys_session(datetime.now(UTC))
    through_month = args.through_month or latest.strftime("%Y-%m")
    try:
        entries, latest = build_schedule(start_month=args.from_month, through_month=through_month)
    except ValueError as exc:
        parser.error(str(exc))
    if args.limit is not None:
        entries = entries[:args.limit]

    print(f"Matured market cutoff: {latest}; queued candidates: {len(entries)}")
    print("Historical inputs use month-end SEC filings and initial-backfill OHLCV; results remain research-only.")
    if not args.apply:
        for entry in entries[:20]:
            print(f"{entry['symbol']} {entry['decision_date']} -> {entry['target_end_date']} ({entry['source_count']} public SEC rows)")
        if len(entries) > 20:
            print(f"... {len(entries) - 20} more")
        print("Preview only. Pass --apply to enqueue these requests.")
        return 0

    accepted = 0
    failed = 0
    for index, entry in enumerate(entries, start=1):
        try:
            job = _enqueue(entry, api_base=args.api_base)
            accepted += 1
            print(f"{index}/{len(entries)} queued {entry['symbol']} {entry['decision_date']} job={job.get('id')}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"{index}/{len(entries)} failed {entry['symbol']} {entry['decision_date']}: {exc}", flush=True)
            if isinstance(exc, RuntimeError) and "API is unavailable" in str(exc):
                break
        time.sleep(0.08)
    print(f"Enqueue finished: accepted={accepted}, failed={failed}")
    return 1 if failed else 0


def _validate_month(value: str) -> str:
    _month(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
