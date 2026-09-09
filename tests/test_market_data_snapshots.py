from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.database import SessionLocal
from app.market_data_ingestion import migrate_legacy_market_prices
from app.models import MarketPrice, MarketPriceRevision


def _add_price(*, symbol: str, trading_date: date, source: str) -> None:
    with SessionLocal() as db:
        db.add(
            MarketPrice(
                symbol=symbol,
                trading_date=trading_date,
                open=100.0,
                high=105.0,
                low=99.0,
                close=103.0,
                volume=1_000_000,
                source=source,
                fetched_at=datetime(2026, 9, 6, 6, 0, tzinfo=UTC),
            )
        )
        db.commit()
    migrate_legacy_market_prices()


def _delete_source(source: str) -> None:
    with SessionLocal() as db:
        db.query(MarketPriceRevision).filter(MarketPriceRevision.source == source).delete()
        db.query(MarketPrice).filter(MarketPrice.source == source).delete()
        db.commit()


def test_snapshot_excludes_prices_after_as_of_time(client):
    from app.market_data_snapshots import get_market_data

    source = f"snapshot-test-{uuid4().hex}"
    try:
        _add_price(symbol="AAPL", trading_date=date(2025, 2, 28), source=source)
        _add_price(symbol="AAPL", trading_date=date(2025, 3, 3), source=source)

        rows = get_market_data(
            "aapl",
            as_of_time=datetime(2025, 3, 1, 23, 59, tzinfo=UTC),
            source=source,
        )

        assert [row.trading_date for row in rows] == [date(2025, 2, 28)]
        assert all(row.trading_date <= date(2025, 3, 1) for row in rows)
    finally:
        _delete_source(source)


def test_snapshot_is_sorted_oldest_to_newest(client):
    from app.market_data_snapshots import get_market_data

    source = f"snapshot-test-{uuid4().hex}"
    try:
        _add_price(symbol="MSFT", trading_date=date(2025, 1, 3), source=source)
        _add_price(symbol="MSFT", trading_date=date(2025, 1, 2), source=source)

        rows = get_market_data(
            "MSFT",
            as_of_time=datetime(2025, 1, 3, 23, 59, tzinfo=UTC),
            source=source,
        )

        assert [row.trading_date for row in rows] == [date(2025, 1, 2), date(2025, 1, 3)]
    finally:
        _delete_source(source)


def test_snapshot_filters_by_source(client):
    from app.market_data_snapshots import get_market_data

    source_a = f"snapshot-a-{uuid4().hex}"
    source_b = f"snapshot-b-{uuid4().hex}"
    try:
        _add_price(symbol="NVDA", trading_date=date(2025, 4, 1), source=source_a)
        _add_price(symbol="NVDA", trading_date=date(2025, 4, 1), source=source_b)

        rows = get_market_data(
            "NVDA",
            as_of_time=datetime(2025, 4, 1, 23, 59, tzinfo=UTC),
            source=source_a,
        )

        assert len(rows) == 1
        assert rows[0].source == source_a
    finally:
        _delete_source(source_a)
        _delete_source(source_b)


def test_snapshot_rejects_invalid_symbol(client):
    from app.market_data_snapshots import get_market_data

    with pytest.raises(ValueError, match="symbol"):
        get_market_data(
            "AAPL!",
            as_of_time=datetime(2025, 3, 1, tzinfo=UTC),
        )


def test_snapshot_requires_timezone_aware_as_of_time(client):
    from app.market_data_snapshots import get_market_data

    with pytest.raises(ValueError, match="timezone-aware"):
        get_market_data("AAPL", as_of_time=datetime(2025, 3, 1))
