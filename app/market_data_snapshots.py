from datetime import datetime
from typing import Literal

from sqlalchemy import select

from .database import SessionLocal
from .market_data import YAHOO_SOURCE
from .market_time import normalize_utc
from .models import MarketPriceRevision
from .services import is_valid_symbol, normalize_symbol


SnapshotMode = Literal["historical_research", "observed"]


def _visible_at(revision: MarketPriceRevision, mode: SnapshotMode) -> datetime:
    if mode == "historical_research" and revision.is_initial_backfill:
        # Explicit research assumption only; not a claim that this system had
        # the legacy bar at the historical market close.
        return revision.available_at
    return max(revision.available_at, revision.observed_at)


def get_market_data(
    symbol: str,
    *,
    as_of_time: datetime,
    source: str = YAHOO_SOURCE,
    mode: SnapshotMode = "historical_research",
) -> list[MarketPriceRevision]:
    """Return the newest daily-bar version visible at one normalized UTC instant.

    ``historical_research`` treats only first-imported legacy/backfilled bars as
    available at their XNYS close. Later revisions remain hidden until observed,
    preventing future corrections from changing old feature snapshots.
    ``observed`` also requires that each version had already reached this system.
    """
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise ValueError("symbol must contain 1-5 ASCII letters")
    if not source:
        raise ValueError("source must not be empty")
    if mode not in {"historical_research", "observed"}:
        raise ValueError("mode must be 'historical_research' or 'observed'")
    cutoff = normalize_utc(as_of_time)

    with SessionLocal() as db:
        revisions = db.scalars(
            select(MarketPriceRevision)
            .where(
                MarketPriceRevision.symbol == normalized_symbol,
                MarketPriceRevision.source == source,
            )
            .order_by(MarketPriceRevision.trading_date.asc(), MarketPriceRevision.revision_number.asc())
        ).all()

    visible_by_date: dict = {}
    for revision in revisions:
        if _visible_at(revision, mode) > cutoff:
            continue
        previous = visible_by_date.get(revision.trading_date)
        if previous is None or revision.revision_number > previous.revision_number:
            visible_by_date[revision.trading_date] = revision
    return [visible_by_date[trading_date] for trading_date in sorted(visible_by_date)]
