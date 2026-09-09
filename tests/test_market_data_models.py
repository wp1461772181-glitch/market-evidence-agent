from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

import app.models as models
from app.database import SessionLocal


def test_market_price_model_exists():
    assert hasattr(models, "MarketPrice")


def test_market_price_can_be_persisted_with_source_and_fetch_time(client):
    source = f"test-source-{uuid4().hex}"
    fetched_at = datetime(2026, 9, 6, 6, 30, tzinfo=UTC)

    with SessionLocal() as db:
        price = models.MarketPrice(
            symbol="AAPL",
            trading_date=date(2026, 9, 4),
            open=239.50,
            high=241.20,
            low=238.80,
            close=240.90,
            volume=52_000_000,
            source=source,
            fetched_at=fetched_at,
        )
        db.add(price)
        db.commit()
        db.refresh(price)

        assert price.id is not None
        assert price.symbol == "AAPL"
        assert price.trading_date == date(2026, 9, 4)
        assert price.source == source
        assert price.fetched_at == fetched_at

        db.delete(price)
        db.commit()


def test_market_price_rejects_duplicate_symbol_date_source(client):
    source = f"test-source-{uuid4().hex}"
    common_fields = {
        "symbol": "MSFT",
        "trading_date": date(2026, 9, 4),
        "open": 500.0,
        "high": 505.0,
        "low": 498.0,
        "close": 503.0,
        "volume": 20_000_000,
        "source": source,
        "fetched_at": datetime(2026, 9, 6, 6, 30, tzinfo=UTC),
    }

    with SessionLocal() as db:
        first = models.MarketPrice(**common_fields)
        db.add(first)
        db.commit()

        duplicate = models.MarketPrice(**common_fields)
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.delete(first)
        db.commit()


def test_ingestion_run_persists_source_cutoff_and_status(client):
    source = f"test-source-{uuid4().hex}"
    as_of_time = datetime(2026, 9, 6, 6, 0, tzinfo=UTC)
    completed_at = datetime(2026, 9, 6, 6, 5, tzinfo=UTC)

    with SessionLocal() as db:
        run = models.IngestionRun(
            source=source,
            as_of_time=as_of_time,
            status="completed",
            completed_at=completed_at,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        assert run.id is not None
        assert run.source == source
        assert run.as_of_time == as_of_time
        assert run.started_at is not None
        assert run.completed_at == completed_at
        assert run.status == "completed"

        db.delete(run)
        db.commit()
