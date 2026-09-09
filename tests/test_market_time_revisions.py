from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import select

from app.database import SessionLocal
from app.market_data import DailyPrice
from app.market_data_ingestion import ingest_market_data
from app.market_data_snapshots import get_market_data
from app.market_time import xnys_session_close_at
from app.models import IngestionRun, MarketPrice, MarketPriceRevision


def _row(source: str, close: float) -> DailyPrice:
    return DailyPrice(
        symbol="AAPL",
        trading_date=date(2025, 3, 3),
        open=100.0,
        high=105.0,
        low=99.0,
        close=close,
        volume=1_000_000,
        source=source,
    )


def _delete_source(source: str) -> None:
    with SessionLocal() as db:
        for revision in db.scalars(select(MarketPriceRevision).where(MarketPriceRevision.source == source)):
            db.delete(revision)
        db.flush()
        for price in db.scalars(select(MarketPrice).where(MarketPrice.source == source)):
            db.delete(price)
        for run in db.scalars(select(IngestionRun).where(IngestionRun.source == source)):
            db.delete(run)
        db.commit()


def test_xnys_close_uses_dst_and_early_close_calendar():
    assert xnys_session_close_at(date(2025, 3, 3)) == datetime(2025, 3, 3, 21, 0, tzinfo=UTC)
    assert xnys_session_close_at(date(2025, 7, 3)) == datetime(2025, 7, 3, 17, 0, tzinfo=UTC)


def test_snapshot_normalizes_same_instant_and_excludes_pre_close_bar(client):
    source = f"time-revision-{uuid4().hex}"
    try:
        ingest_market_data(
            ["AAPL"],
            date(2025, 3, 3),
            date(2025, 3, 3),
            datetime(2025, 3, 4, tzinfo=UTC),
            source=source,
            fetcher=lambda *_: [_row(source, 101.0)],
            now_factory=lambda: datetime(2025, 3, 4, tzinfo=UTC),
        )
        utc_pre_close = datetime(2025, 3, 3, 20, 59, tzinfo=UTC)
        same_instant_local = datetime.fromisoformat("2025-03-04T09:59:00+13:00")
        assert get_market_data("AAPL", as_of_time=utc_pre_close, source=source) == []
        assert get_market_data("AAPL", as_of_time=same_instant_local, source=source) == []
        assert get_market_data("AAPL", as_of_time=datetime(2025, 3, 3, 21, tzinfo=UTC), source=source)[0].close == 101.0
        assert get_market_data("AAPL", as_of_time=datetime(2025, 3, 3, 21, tzinfo=UTC), source=source, mode="observed") == []
    finally:
        _delete_source(source)


def test_revisions_append_freeze_old_snapshots_and_allow_a_b_a(client):
    source = f"revision-order-{uuid4().hex}"
    observed_times = iter(
        [
            datetime(2025, 3, 4, 9, tzinfo=UTC),
            datetime(2025, 3, 4, 10, tzinfo=UTC),
            datetime(2025, 3, 5, 9, tzinfo=UTC),
            datetime(2025, 3, 5, 10, tzinfo=UTC),
            datetime(2025, 3, 6, 9, tzinfo=UTC),
            datetime(2025, 3, 6, 10, tzinfo=UTC),
            datetime(2025, 3, 7, 9, tzinfo=UTC),
            datetime(2025, 3, 7, 10, tzinfo=UTC),
        ]
    )
    try:
        def ingest(close: float):
            return ingest_market_data(
                ["AAPL"],
                date(2025, 3, 3),
                date(2025, 3, 3),
                datetime(2025, 3, 10, tzinfo=UTC),
                source=source,
                fetcher=lambda *_: [_row(source, close)],
                now_factory=lambda: next(observed_times),
            )

        assert ingest(101.0).inserted_count == 1
        assert ingest(102.0).inserted_count == 1
        assert ingest(101.0).inserted_count == 1
        assert ingest(101.0).skipped_count == 1

        assert get_market_data("AAPL", as_of_time=datetime(2025, 3, 4, 12, tzinfo=UTC), source=source)[0].close == 101.0
        assert get_market_data("AAPL", as_of_time=datetime(2025, 3, 5, 12, tzinfo=UTC), source=source)[0].close == 102.0
        assert get_market_data("AAPL", as_of_time=datetime(2025, 3, 6, 12, tzinfo=UTC), source=source)[0].close == 101.0
    finally:
        _delete_source(source)


def test_ingestion_migrates_existing_legacy_row_before_idempotency_check(client):
    source = f"legacy-revision-{uuid4().hex}"
    with SessionLocal() as db:
        db.add(
            MarketPrice(
                symbol="AAPL",
                trading_date=date(2025, 3, 3),
                open=100.0,
                high=105.0,
                low=99.0,
                close=101.0,
                volume=1_000_000,
                source=source,
                fetched_at=datetime(2025, 3, 4, tzinfo=UTC),
            )
        )
        db.commit()
    try:
        common = dict(
            symbols=["AAPL"],
            start_date=date(2025, 3, 3),
            end_date=date(2025, 3, 3),
            as_of_time=datetime(2025, 3, 10, tzinfo=UTC),
            source=source,
        )
        unchanged = ingest_market_data(
            **common,
            fetcher=lambda *_: [_row(source, 101.0)],
            now_factory=lambda: datetime(2025, 3, 5, tzinfo=UTC),
        )
        changed = ingest_market_data(
            **common,
            fetcher=lambda *_: [_row(source, 102.0)],
            now_factory=lambda: datetime(2025, 3, 6, tzinfo=UTC),
        )
        assert unchanged.inserted_count == 0
        assert unchanged.skipped_count == 1
        assert changed.inserted_count == 1
        with SessionLocal() as db:
            revisions = list(db.scalars(select(MarketPriceRevision).where(MarketPriceRevision.source == source)))
        assert sorted(revision.revision_number for revision in revisions) == [1, 2]
        assert get_market_data("AAPL", as_of_time=datetime(2025, 3, 4, 12, tzinfo=UTC), source=source)[0].close == 101.0
    finally:
        _delete_source(source)
