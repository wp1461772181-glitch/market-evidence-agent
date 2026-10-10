"""Bounded runtime ticker discovery shared by refresh and SEC monitors."""
from sqlalchemy import inspect, select

from .forecast_jobs import DEFAULT_STOCKS
from .symbols import normalize_market_symbol

MAX_ACTIVE_SYMBOLS = 40


def active_symbols(db, *, extra_models=()) -> tuple[str, ...]:
    """Defaults first, then stable validated persisted symbols, capped at 40."""
    values = set(DEFAULT_STOCKS)
    candidates = set()
    inspector = inspect(db.get_bind())
    from .models import MarketPrice, MarketPriceRevision
    models = [MarketPrice, MarketPriceRevision, *extra_models]
    for model in models:
        if model.__tablename__ not in inspector.get_table_names():
            continue
        candidates.update(db.scalars(select(model.symbol).distinct().limit(500)))
    extras = set()
    for value in candidates:
        try:
            normalized = normalize_market_symbol(value)
        except ValueError:
            continue
        if normalized not in values:
            extras.add(normalized)
    return tuple(sorted(values) + sorted(extras)[: MAX_ACTIVE_SYMBOLS - len(values)])
