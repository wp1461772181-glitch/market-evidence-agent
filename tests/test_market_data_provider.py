import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.market_data import MarketDataError, YahooFinanceProvider


FIXTURES_DIR = Path(__file__).parent / "fixtures"


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


def fixture_payload(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


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
    assert query["events"] == ["div,splits,capitalGains"]
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


def test_provider_metadata_records_quote_basis_and_corporate_actions_without_using_adjclose():
    payload = fixture_payload("yahoo_chart_split_and_dividend.json")
    provider = YahooFinanceProvider(opener=lambda request, timeout: FakeResponse(payload))

    result = provider.fetch_daily_prices_with_metadata("AAPL", date(2020, 8, 28), date(2020, 9, 1))

    assert [row.close for row in result.prices] == [124.8075, 129.04, 134.18]
    assert result.price_basis.basis == "provider_quote_close_v1"
    assert result.price_basis.adjusted_close_present is True
    assert result.price_basis.adjusted_close_matches_quote is False
    assert result.price_basis.provider_behavior_verified is True
    assert result.price_basis.verification_notes == (
        "daily_price_close=indicators.quote.close",
        "indicators.adjclose was inspected but not used as DailyPrice.close",
        "adjusted-close values differed from quote close for this response",
        "corporate-action response included an events object",
    )
    assert [
        (action.kind, action.effective_date, action.known, action.amount, action.numerator, action.denominator)
        for action in result.price_basis.corporate_actions
    ] == [
        ("split", date(2020, 8, 31), True, None, 4, 1),
        ("cash_dividend", date(2020, 9, 10), True, 0.205, None, None),
    ]


def test_legacy_fetch_contract_still_returns_a_list_of_daily_prices():
    payload = fixture_payload("yahoo_chart_split_and_dividend.json")
    provider = YahooFinanceProvider(opener=lambda request, timeout: FakeResponse(payload))

    prices = provider.fetch_daily_prices("AAPL", date(2020, 8, 28), date(2020, 9, 1))

    assert isinstance(prices, list)
    assert [row.close for row in prices] == [124.8075, 129.04, 134.18]


def test_provider_metadata_fails_closed_when_returned_quote_rows_have_incomplete_adjclose():
    payload = fixture_payload("yahoo_chart_split_and_dividend.json")
    payload["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"][1] = None
    provider = YahooFinanceProvider(opener=lambda request, timeout: FakeResponse(payload))

    result = provider.fetch_daily_prices_with_metadata("AAPL", date(2020, 8, 28), date(2020, 9, 1))

    assert result.price_basis.adjusted_close_present is True
    assert result.price_basis.adjusted_close_matches_quote is None
    assert result.price_basis.provider_behavior_verified is False
    assert "adjusted-close series was incomplete for returned quote rows" in result.price_basis.verification_notes


def test_provider_metadata_records_zero_provider_reported_actions_when_events_are_absent():
    payload = fixture_payload("yahoo_chart_split_and_dividend.json")
    del payload["chart"]["result"][0]["events"]
    provider = YahooFinanceProvider(opener=lambda request, timeout: FakeResponse(payload))

    result = provider.fetch_daily_prices_with_metadata("AAPL", date(2020, 8, 28), date(2020, 9, 1))

    assert result.price_basis.corporate_actions == ()
    assert result.price_basis.corporate_actions_available is True
    assert result.price_basis.corporate_actions_response_shape == "events_omitted"
    assert result.price_basis.provider_behavior_verified is True
    assert result.price_basis.verification_notes[-1] == (
        "provider returned no split/dividend entries after explicitly requested events"
    )
