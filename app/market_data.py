import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Callable, Literal
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


@dataclass(frozen=True)
class CorporateAction:
    """One corporate-action marker returned beside Yahoo chart prices.

    The action is descriptive evidence for the V2 price-basis check.  It
    intentionally does not modify an OHLC value: the current price contract is
    provider quote close, not total return or an inferred adjusted series.
    """

    kind: Literal["split", "cash_dividend", "unknown"]
    effective_date: date | None
    known: bool
    amount: float | None = None
    numerator: int | None = None
    denominator: int | None = None


@dataclass(frozen=True)
class PriceBasisMetadata:
    """Evidence describing how one Yahoo chart response was interpreted.

    ``DailyPrice.close`` always comes from ``indicators.quote.close``.  The
    optional adjusted-close series is inspected only to document provider
    behavior; it is never mixed into the returned rows.
    """

    basis: str = "provider_quote_close_v1"
    adjusted_close_present: bool = False
    provider_behavior_verified: bool = False
    corporate_actions_available: bool = False
    corporate_actions_response_shape: Literal["events_object", "events_omitted"] = "events_omitted"
    corporate_actions: tuple[CorporateAction, ...] = ()
    verification_notes: tuple[str, ...] = ()
    adjusted_close_matches_quote: bool | None = None


@dataclass(frozen=True)
class MarketDataFetchResult:
    """The backward-compatible prices plus their provider price-basis proof."""

    prices: tuple[DailyPrice, ...]
    price_basis: PriceBasisMetadata


class YahooFinanceProvider:
    def __init__(self, opener: Callable = urlopen):
        self._opener = opener

    def fetch_daily_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[DailyPrice]:
        """Return legacy OHLCV rows without changing its public contract."""
        return list(self.fetch_daily_prices_with_metadata(symbol, start_date, end_date).prices)

    def fetch_daily_prices_with_metadata(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> MarketDataFetchResult:
        """Return Yahoo quote-close rows together with price-basis metadata.

        Callers that create a V2 forecast should keep ``price_basis`` with the
        forecast input manifest.  Existing ingestion remains free to use
        :meth:`fetch_daily_prices`, which keeps returning ``list[DailyPrice]``.
        """
        normalized_symbol = normalize_symbol(symbol)
        if not is_valid_symbol(normalized_symbol):
            raise ValueError("symbol must contain 1-5 ASCII letters")
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")

        params = {
            "period1": self._to_epoch_seconds(start_date),
            "period2": self._to_epoch_seconds(end_date + timedelta(days=1)),
            "interval": "1d",
            # Yahoo only returns split/dividend markers when they are requested
            # explicitly.  A no-action response may omit ``events`` entirely;
            # that response shape is preserved in PriceBasisMetadata.
            "events": "div,splits,capitalGains",
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
        returned_indexes: list[int] = []
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
            returned_indexes.append(index)

        if not rows:
            raise MarketDataError(f"Yahoo Finance returned no complete OHLCV rows for {normalized_symbol}")
        return MarketDataFetchResult(
            prices=tuple(rows),
            price_basis=self._price_basis_metadata(
                result=result,
                quote=quote,
                returned_indexes=returned_indexes,
            ),
        )

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

    @classmethod
    def _price_basis_metadata(
        cls,
        *,
        result: dict,
        quote: dict,
        returned_indexes: list[int],
    ) -> PriceBasisMetadata:
        adjusted_series = cls._adjusted_close_series(result)
        events_object_present = isinstance(result.get("events"), dict)
        adjusted_values = [
            adjusted_series[index] if index < len(adjusted_series) else None
            for index in returned_indexes
        ]
        adjusted_close_present = any(cls._is_finite_number(value) for value in adjusted_values)
        quote_close_series = quote.get("close") or []
        quote_is_complete = all(
            index < len(quote_close_series) and cls._is_finite_number(quote_close_series[index])
            for index in returned_indexes
        )

        adjusted_close_matches_quote: bool | None = None
        if adjusted_close_present:
            if all(cls._is_finite_number(value) for value in adjusted_values):
                adjusted_close_matches_quote = all(
                    math.isclose(
                        float(quote_close_series[index]),
                        float(adjusted_series[index]),
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                    for index in returned_indexes
                )

        notes = ["daily_price_close=indicators.quote.close"]
        if adjusted_close_present:
            notes.append("indicators.adjclose was inspected but not used as DailyPrice.close")
            if adjusted_close_matches_quote is None:
                notes.append("adjusted-close series was incomplete for returned quote rows")
            elif adjusted_close_matches_quote:
                notes.append("adjusted-close values matched quote close for this response")
            else:
                notes.append("adjusted-close values differed from quote close for this response")
        else:
            notes.append("no usable adjusted-close values were returned for the quote rows")
        if events_object_present:
            notes.append("corporate-action response included an events object")
        else:
            # This provider omits the events object after the explicit event
            # request when it has no split/dividend entries for the interval.
            # We record it as zero provider-reported actions, not as an
            # independent assertion that no real-world corporate action exists.
            notes.append("provider returned no split/dividend entries after explicitly requested events")

        return PriceBasisMetadata(
            adjusted_close_present=adjusted_close_present,
            provider_behavior_verified=quote_is_complete and (
                not adjusted_close_present or adjusted_close_matches_quote is not None
            ),
            corporate_actions_available=True,
            corporate_actions_response_shape="events_object" if events_object_present else "events_omitted",
            corporate_actions=cls._corporate_actions(result),
            verification_notes=tuple(notes),
            adjusted_close_matches_quote=adjusted_close_matches_quote,
        )

    @staticmethod
    def _adjusted_close_series(result: dict) -> list[object]:
        adjusted_blocks = result.get("indicators", {}).get("adjclose") or []
        if not adjusted_blocks or not isinstance(adjusted_blocks[0], dict):
            return []
        values = adjusted_blocks[0].get("adjclose") or []
        return values if isinstance(values, list) else []

    @classmethod
    def _corporate_actions(cls, result: dict) -> tuple[CorporateAction, ...]:
        events = result.get("events")
        if not isinstance(events, dict):
            return ()

        actions: list[CorporateAction] = []
        for event in cls._event_records(events.get("splits")):
            effective_date = cls._event_date(event)
            numerator, denominator = cls._split_ratio(event)
            actions.append(
                CorporateAction(
                    kind="split",
                    effective_date=effective_date,
                    known=effective_date is not None and numerator is not None and denominator is not None,
                    numerator=numerator,
                    denominator=denominator,
                )
            )
        for event in cls._event_records(events.get("dividends")):
            effective_date = cls._event_date(event)
            amount = event.get("amount") if isinstance(event, dict) else None
            valid_amount = float(amount) if cls._is_finite_number(amount) else None
            actions.append(
                CorporateAction(
                    kind="cash_dividend",
                    effective_date=effective_date,
                    known=effective_date is not None and valid_amount is not None,
                    amount=valid_amount,
                )
            )
        return tuple(
            sorted(
                actions,
                key=lambda action: (
                    action.effective_date is None,
                    action.effective_date or date.max,
                    action.kind,
                ),
            )
        )

    @staticmethod
    def _event_records(raw_events: object) -> list[dict]:
        if isinstance(raw_events, dict):
            return [event for event in raw_events.values() if isinstance(event, dict)]
        if isinstance(raw_events, list):
            return [event for event in raw_events if isinstance(event, dict)]
        return []

    @staticmethod
    def _event_date(event: dict) -> date | None:
        raw_date = event.get("date")
        if isinstance(raw_date, (int, float)) and not isinstance(raw_date, bool):
            try:
                return datetime.fromtimestamp(raw_date, tz=UTC).date()
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(raw_date, str):
            try:
                return date.fromisoformat(raw_date)
            except ValueError:
                return None
        return None

    @classmethod
    def _split_ratio(cls, event: dict) -> tuple[int | None, int | None]:
        numerator = event.get("numerator")
        denominator = event.get("denominator")
        if cls._is_positive_int(numerator) and cls._is_positive_int(denominator):
            return int(numerator), int(denominator)

        ratio = event.get("splitRatio")
        if isinstance(ratio, str) and ratio.count(":") == 1:
            left, right = ratio.split(":")
            if left.isdigit() and right.isdigit() and int(left) > 0 and int(right) > 0:
                return int(left), int(right)
        return None, None

    @staticmethod
    def _is_finite_number(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @staticmethod
    def _is_positive_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0


def fetch_daily_prices(symbol: str, start_date: date, end_date: date) -> list[DailyPrice]:
    return YahooFinanceProvider().fetch_daily_prices(symbol, start_date, end_date)
