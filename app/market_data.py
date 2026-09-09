import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .services import is_valid_symbol, normalize_symbol


YAHOO_CHART_BASE_URL = "https://query2.finance.yahoo.com/v8/finance/chart"
YAHOO_SOURCE = "yahoo-finance-chart"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_USER_AGENT = "market-evidence-agent/0.1 (+historical daily market data)"


class MarketDataError(RuntimeError):
    """Raised when a market-data provider cannot return a usable dataset."""


@dataclass(frozen=True)
class DailyPrice:
    symbol: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str = YAHOO_SOURCE


class YahooFinanceProvider:
    def __init__(self, opener: Callable = urlopen):
        self._opener = opener

    def fetch_daily_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[DailyPrice]:
        normalized_symbol = normalize_symbol(symbol)
        if not is_valid_symbol(normalized_symbol):
            raise ValueError("symbol must contain 1-5 ASCII letters")
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")

        params = {
            "period1": self._to_epoch_seconds(start_date),
            "period2": self._to_epoch_seconds(end_date + timedelta(days=1)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        url = f"{YAHOO_CHART_BASE_URL}/{normalized_symbol}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})

        try:
            with self._opener(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MarketDataError(f"Yahoo Finance request failed for {normalized_symbol}: {exc}") from exc

        chart = payload.get("chart", {})
        provider_error = chart.get("error")
        if provider_error:
            description = provider_error.get("description") or provider_error.get("code") or "unknown provider error"
            raise MarketDataError(f"Yahoo Finance error for {normalized_symbol}: {description}")

        results = chart.get("result") or []
        if not results:
            raise MarketDataError(f"Yahoo Finance returned no data for {normalized_symbol}")

        result = results[0]
        timestamps = result.get("timestamp") or []
        quote_blocks = result.get("indicators", {}).get("quote") or []
        if not quote_blocks:
            raise MarketDataError(f"Yahoo Finance returned no OHLCV data for {normalized_symbol}")

        quote = quote_blocks[0]
        rows: list[DailyPrice] = []
        for index, timestamp in enumerate(timestamps):
            values = self._quote_values_at(quote, index)
            if values is None:
                continue
            open_price, high_price, low_price, close_price, volume = values
            rows.append(
                DailyPrice(
                    symbol=normalized_symbol,
                    trading_date=datetime.fromtimestamp(timestamp, tz=UTC).date(),
                    open=float(open_price),
                    high=float(high_price),
                    low=float(low_price),
                    close=float(close_price),
                    volume=int(volume),
                )
            )

        if not rows:
            raise MarketDataError(f"Yahoo Finance returned no complete OHLCV rows for {normalized_symbol}")
        return rows

    @staticmethod
    def _to_epoch_seconds(value: date) -> int:
        return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp())

    @staticmethod
    def _quote_values_at(quote: dict, index: int):
        keys = ("open", "high", "low", "close", "volume")
        values = []
        for key in keys:
            series = quote.get(key) or []
            if index >= len(series) or series[index] is None:
                return None
            values.append(series[index])
        return tuple(values)


def fetch_daily_prices(symbol: str, start_date: date, end_date: date) -> list[DailyPrice]:
    return YahooFinanceProvider().fetch_daily_prices(symbol, start_date, end_date)
