import json
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from app.market_data import MarketDataError, YahooFinanceProvider


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload


def chart_payload(symbol: str = "AAPL") -> dict:
    timestamps = [
        int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()),
        int(datetime(2026, 9, 2, tzinfo=UTC).timestamp()),
        int(datetime(2026, 9, 3, tzinfo=UTC).timestamp()),
    ]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": symbol},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, 101.0, None],
                                "high": [102.0, 103.0, None],
                                "low": [99.0, 100.0, None],
                                "close": [101.0, 102.0, None],
                                "volume": [1_000_000, 1_100_000, None],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def test_fetch_daily_prices_normalizes_symbol_and_parses_ohlcv():
    captured_request = {}

    def fake_opener(request, timeout):
        captured_request["request"] = request
        captured_request["timeout"] = timeout
        return FakeResponse(chart_payload())

    provider = YahooFinanceProvider(opener=fake_opener)
    prices = provider.fetch_daily_prices(" aapl ", date(2026, 9, 1), date(2026, 9, 3))

    assert [row.trading_date for row in prices] == [date(2026, 9, 1), date(2026, 9, 2)]
    assert all(row.symbol == "AAPL" for row in prices)
    assert all(row.source == "yahoo-finance-chart" for row in prices)
    assert prices[0].open == 100.0
    assert prices[0].high == 102.0
    assert prices[0].low == 99.0
    assert prices[0].close == 101.0
    assert prices[0].volume == 1_000_000

    request = captured_request["request"]
    query = parse_qs(urlparse(request.full_url).query)
    assert request.full_url.startswith("https://query2.finance.yahoo.com/v8/finance/chart/AAPL?")
    assert query["interval"] == ["1d"]
    assert int(query["period2"][0]) == int(datetime(2026, 9, 4, tzinfo=UTC).timestamp())
    assert request.headers["User-agent"]
    assert captured_request["timeout"] == 20


def test_fetch_daily_prices_rejects_invalid_symbol():
    provider = YahooFinanceProvider(opener=lambda request, timeout: None)

    with pytest.raises(ValueError, match="1-5 ASCII letters"):
        provider.fetch_daily_prices("AAPL!", date(2026, 9, 1), date(2026, 9, 3))


def test_fetch_daily_prices_rejects_reversed_date_range():
    provider = YahooFinanceProvider(opener=lambda request, timeout: None)

    with pytest.raises(ValueError, match="start_date must be on or before end_date"):
        provider.fetch_daily_prices("AAPL", date(2026, 9, 3), date(2026, 9, 1))


def test_fetch_daily_prices_surfaces_provider_error():
    payload = {
        "chart": {
            "result": None,
            "error": {"code": "Not Found", "description": "No data found"},
        }
    }

    provider = YahooFinanceProvider(opener=lambda request, timeout: FakeResponse(payload))

    with pytest.raises(MarketDataError, match="No data found"):
        provider.fetch_daily_prices("AAPL", date(2026, 9, 1), date(2026, 9, 3))
