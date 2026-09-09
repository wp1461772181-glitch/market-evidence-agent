from datetime import UTC, date, datetime
from functools import lru_cache

import exchange_calendars as xcals
from exchange_calendars.errors import NotSessionError


@lru_cache(maxsize=32)
def _xnys_calendar_for_year(year: int):
    """Return a calendar whose explicit bounds include the requested year.

    The one-year padding lets the library apply holiday and early-close rules at
    the edge of the requested range instead of relying on its moving defaults.
    """
    return xcals.get_calendar(
        "XNYS",
        start=f"{year - 1}-01-01",
        end=f"{year + 1}-12-31",
    )


def normalize_utc(value: datetime, *, name: str = "as_of_time") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def xnys_session_close_at(trading_date: date) -> datetime:
    """Return the actual XNYS close for a trading session as a UTC instant.

    exchange_calendars supplies the historical DST, holiday, and early-close
    schedule. A daily OHLCV bar is not available before this instant.
    """
    calendar = _xnys_calendar_for_year(trading_date.year)
    try:
        close = calendar.session_close(trading_date)
    except NotSessionError as exc:
        raise ValueError(f"{trading_date.isoformat()} is not an XNYS trading session") from exc
    return close.to_pydatetime().astimezone(UTC)
