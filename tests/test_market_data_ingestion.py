from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.market_data import DailyPrice, MarketDataError
from app.models import IngestionRun, MarketPrice, MarketPriceRevision


def _rows(source: str) -> list[DailyPrice]:
    return [
        DailyPrice(
            symbol="AAPL",
            trading_date=date(2026, 9, 3),
            open=230.0,
            high=233.0,
            low=229.0,
            close=232.0,
            volume=40_000_000,
            source=source,
        ),
        DailyPrice(
            symbol="AAPL",
            trading_date=date(2026, 9, 4),
            open=232.0,
            high=235.0,
            low=231.0,
            close=234.0,
            volume=42_000_000,
            source=source,
        ),
    ]


def test_ingestion_persists_prices_and_completed_run(client):
    from app.market_data_ingestion import ingest_market_data

    source = f"test-ingestion-{uuid4().hex}"
    as_of_time = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)
    fetched_at = datetime(2026, 9, 6, 7, 5, tzinfo=UTC)

    def fetcher(symbol: str, start_date: date, end_date: date):
        assert symbol == "AAPL"
        return _rows(source)

    summary = ingest_market_data(
        symbols=["AAPL"],
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 4),
        as_of_time=as_of_time,
        source=source,
        fetcher=fetcher,
        now_factory=lambda: fetched_at,
    )

    assert summary.inserted_count == 2
    assert summary.skipped_count == 0
    assert summary.status == "completed"

    with SessionLocal() as db:
        prices = list(db.scalars(select(MarketPrice).where(MarketPrice.source == source)))
        run = db.get(IngestionRun, summary.run_id)

        assert len(prices) == 2
        assert {price.trading_date for price in prices} == {date(2026, 9, 3), date(2026, 9, 4)}
        assert all(price.fetched_at == fetched_at for price in prices)
        assert run is not None
        assert run.source == source
        assert run.as_of_time == as_of_time
        assert run.status == "completed"
        assert run.completed_at == fetched_at

        for revision in db.scalars(select(MarketPriceRevision).where(MarketPriceRevision.source == source)):
            db.delete(revision)
        db.flush()
        for price in prices:
            db.delete(price)
        db.delete(run)
        db.commit()


def test_ingestion_is_idempotent_for_same_symbol_date_and_source(client):
    from app.market_data_ingestion import ingest_market_data

    source = f"test-idempotent-{uuid4().hex}"
    as_of_time = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)

    def fetcher(symbol: str, start_date: date, end_date: date):
        return _rows(source)

    first = ingest_market_data(
        symbols=["AAPL"],
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 4),
        as_of_time=as_of_time,
        source=source,
        fetcher=fetcher,
    )
    second = ingest_market_data(
        symbols=["AAPL"],
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 4),
        as_of_time=as_of_time,
        source=source,
        fetcher=fetcher,
    )

    assert first.inserted_count == 2
    assert first.skipped_count == 0
    assert second.inserted_count == 0
    assert second.skipped_count == 2

    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(MarketPrice).where(MarketPrice.source == source))
        runs = list(db.scalars(select(IngestionRun).where(IngestionRun.source == source)))

        assert count == 2
        assert len(runs) == 2
        assert all(run.status == "completed" for run in runs)

        for revision in db.scalars(select(MarketPriceRevision).where(MarketPriceRevision.source == source)):
            db.delete(revision)
        db.flush()
        for price in db.scalars(select(MarketPrice).where(MarketPrice.source == source)):
            db.delete(price)
        for run in runs:
            db.delete(run)
        db.commit()


def test_failed_provider_call_records_failed_ingestion_run(client):
    from app.market_data_ingestion import ingest_market_data

    source = f"test-failed-{uuid4().hex}"
    as_of_time = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)

    def failing_fetcher(symbol: str, start_date: date, end_date: date):
        raise MarketDataError("provider unavailable")

    with pytest.raises(MarketDataError, match="provider unavailable"):
        ingest_market_data(
            symbols=["AAPL"],
            start_date=date(2026, 9, 3),
            end_date=date(2026, 9, 4),
            as_of_time=as_of_time,
            source=source,
            fetcher=failing_fetcher,
        )

    with SessionLocal() as db:
        runs = list(db.scalars(select(IngestionRun).where(IngestionRun.source == source)))
        prices = list(db.scalars(select(MarketPrice).where(MarketPrice.source == source)))

        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].completed_at is not None
        assert prices == []

        for revision in db.scalars(select(MarketPriceRevision).where(MarketPriceRevision.source == source)):
            db.delete(revision)
        db.flush()
        db.delete(runs[0])
        db.commit()
