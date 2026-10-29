"""Integration tests for `query_stock_prices` `saved_price_set` receipts and `calculate_formula` tool."""
from __future__ import annotations

import json
import math
import statistics
import uuid
from datetime import UTC, date, datetime, timedelta

import exchange_calendars as xcals
from sqlalchemy import delete

from app.agent_tools import build_price_set_row, query_stock_prices
from app.agent_workflow.evidence import number_rows
from app.agent_workflow.formula_replay import execute_calculate_formula
from app.database import Base, SessionLocal, engine
from app.models import MarketPrice, MarketPriceRevision

NINE_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "TSM"]
XNYS = xcals.get_calendar("XNYS")


def _sessions_back(end: date, count: int) -> list[date]:
    """Newest-first XNYS sessions on or before `end`; seeded history must be real trading days."""
    sessions: list[date] = []
    day = end
    while len(sessions) < count:
        if XNYS.is_session(day):
            sessions.append(day)
        day -= timedelta(days=1)
    return sessions


def _seed_symbol_prices(symbol: str, count: int, *, base_price: float = 150.0, swing_scale: float = 1.2) -> list[dict]:
    Base.metadata.create_all(bind=engine)
    base_date = date(2026, 10, 28)
    fetched_ts = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    rows: list[dict] = []
    with SessionLocal() as session:
        session.execute(delete(MarketPriceRevision).where(MarketPriceRevision.symbol == symbol))
        session.execute(delete(MarketPrice).where(MarketPrice.symbol == symbol))
        session.commit()
        for idx, d in enumerate(_sessions_back(base_date, count)):
            close_val = round(base_price + idx * 0.25 + swing_scale * math.sin(idx * 0.35), 2)
            mp = MarketPrice(
                id=uuid.uuid4(),
                symbol=symbol,
                trading_date=d,
                open=round(close_val - 0.5, 2),
                high=round(close_val + 1.0, 2),
                low=round(close_val - 1.0, 2),
                close=close_val,
                volume=5_000_000 + idx * 500,
                source="stooq",
                fetched_at=fetched_ts,
            )
            session.add(mp)
            rows.append({
                "id": str(mp.id),
                "symbol": symbol,
                "trading_date": d.isoformat(),
                "open": float(mp.open),
                "high": float(mp.high),
                "low": float(mp.low),
                "close": float(mp.close),
                "volume": int(mp.volume),
                "source": mp.source,
                "fetched_at": fetched_ts.isoformat(),
            })
        session.commit()
    return rows


def test_case1_single_stock_price_set_receipt_and_volatility():
    rows = _seed_symbol_prices("AAPL", 127, base_price=200.0, swing_scale=2.5)
    receipt, _ = build_price_set_row(rows, symbol="AAPL")
    citations: list[dict] = []
    shown = number_rows("query_stock_prices", [receipt], citations)

    assert len(shown) == 1
    receipt_bytes = len(json.dumps(shown[0], ensure_ascii=False).encode("utf-8"))
    assert receipt_bytes < 600
    assert shown[0]["citation"] == "[1]"
    assert shown[0]["n"] == 127
    assert citations[0]["source_type"] == "saved_price_set"
    assert len(citations[0]["row_ids"]) == 127

    formula_code = (
        "def f(close):\n"
        "    r = np.diff(close) / close[:-1]\n"
        "    return float(np.std(r, ddof=1) * math.sqrt(252) * 100)\n"
    )
    outcome = execute_calculate_formula(
        {
            "formula_code": formula_code,
            "bindings": [
                {"label": "AAPL", "inputs": {"close": {"source_ordinal": 1, "field": "close"}}},
            ],
            "decimals": 4,
            "unit": "percent",
        },
        citations,
    )
    assert outcome["ok"] is True
    assert len(outcome["data"]) == 1
    assert outcome["data"][0]["citation"] == "[2]"
    assert citations[1]["source_type"] == "formula_calculation"

    asc_closes = [r["close"] for r in sorted(rows, key=lambda x: x["trading_date"])]
    rets = [(asc_closes[i + 1] - asc_closes[i]) / asc_closes[i] for i in range(len(asc_closes) - 1)]
    expected_raw = statistics.stdev(rets) * math.sqrt(252) * 100.0
    assert abs(citations[1]["calculation"]["raw"] - expected_raw) < 1e-9
    assert outcome["data"][0]["value"] == str(round(expected_raw, 4))


def test_short_price_series_up_to_10_rows_preserves_saved_price_behavior():
    rows = _seed_symbol_prices("AAPL", 5, base_price=210.0)
    citations: list[dict] = []
    shown = number_rows("query_stock_prices", rows, citations)
    assert len(shown) == 5
    assert len(citations) == 5
    assert all(c["source_type"] == "saved_price" for c in citations)


def test_case4_nine_stocks_batch_calculation_fits_request_budget():
    citations: list[dict] = []
    receipts: list[dict] = []
    for idx, sym in enumerate(NINE_TICKERS):
        rows = _seed_symbol_prices(sym, 127, base_price=100.0 + idx * 15.0, swing_scale=0.6 * (idx + 1))
        receipt, _ = build_price_set_row(rows, symbol=sym)
        shown = number_rows("query_stock_prices", [receipt], citations)
        receipts.extend(shown)

    assert len(citations) == 9
    assert all(c["source_type"] == "saved_price_set" for c in citations)

    formula_code = (
        "def f(close):\n"
        "    r = np.diff(close) / close[:-1]\n"
        "    return float(np.std(r, ddof=1) * math.sqrt(252) * 100)\n"
    )
    outcome = execute_calculate_formula(
        {
            "formula_code": formula_code,
            "bindings": [
                {
                    "label": sym,
                    "inputs": {"close": {"source_ordinal": i + 1, "field": "close"}},
                }
                for i, sym in enumerate(NINE_TICKERS)
            ],
            "decimals": 2,
            "unit": "percent",
        },
        citations,
    )
    assert outcome["ok"] is True
    assert len(outcome["data"]) == 9
    assert len(citations) == 18
    formula_cits = [c for c in citations if c["source_type"] == "formula_calculation"]
    assert len(formula_cits) == 9

    # Total serialized evidence sent to the model for 9 stocks + 9 formula results is tiny (< 15 KB << 180 KB)
    serialized_evidence = json.dumps({"price_sets": receipts, "formula": outcome}, ensure_ascii=False).encode("utf-8")
    assert len(serialized_evidence) < 15_000
