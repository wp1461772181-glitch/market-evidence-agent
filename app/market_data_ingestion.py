import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Callable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .market_data import DailyPrice, YAHOO_SOURCE, fetch_daily_prices
from .market_time import normalize_utc, xnys_session_close_at
from .models import IngestionRun, MarketPrice, MarketPriceRevision
from .services import normalize_symbol


@dataclass(frozen=True)
class IngestionSummary:
    run_id: UUID
    source: str
    as_of_time: datetime
    inserted_count: int
    skipped_count: int
    status: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _content_hash(row: DailyPrice) -> str:
    canonical = json.dumps(
        {
            "close": format(float(row.close), ".17g"),
            "high": format(float(row.high), ".17g"),
            "low": format(float(row.low), ".17g"),
            "open": format(float(row.open), ".17g"),
            "source": row.source,
            "symbol": row.symbol,
            "trading_date": row.trading_date.isoformat(),
            "volume": int(row.volume),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _add_legacy_revisions(db: Session, *, observed_at: datetime | None = None) -> int:
    """Append explicit baseline revisions for the immutable Week 2 price rows.

    A baseline is marked as a research backfill. Its original fetch timestamp
    remains the only evidence of when this system actually observed it.
    """
    migration_observed_at = normalize_utc(observed_at or _utc_now(), name="migration_observed_at")
    legacy_rows = db.scalars(
        select(MarketPrice)
        .outerjoin(MarketPriceRevision, MarketPriceRevision.market_price_id == MarketPrice.id)
        .where(MarketPriceRevision.id.is_(None))
    ).all()
    for price in legacy_rows:
        row = DailyPrice(
            symbol=price.symbol,
            trading_date=price.trading_date,
            open=price.open,
            high=price.high,
            low=price.low,
            close=price.close,
            volume=price.volume,
            source=price.source,
        )
        db.add(
            MarketPriceRevision(
                market_price_id=price.id,
                symbol=price.symbol,
                trading_date=price.trading_date,
                open=price.open,
                high=price.high,
                low=price.low,
                close=price.close,
                volume=price.volume,
                source=price.source,
                revision_number=1,
                content_hash=_content_hash(row),
                available_at=xnys_session_close_at(price.trading_date),
                # Week 2 recorded fetch-start timestamps, not proven response
                # receipt times. The migration instant is the first strict
                # observed-time claim this schema can make.
                observed_at=migration_observed_at,
                is_initial_backfill=True,
            )
        )
    return len(legacy_rows)


def _validate_provider_row(
    row: DailyPrice,
    *,
    expected_symbol: str,
    start_date: date,
    end_date: date,
    requested_cutoff: datetime,
    observed_at: datetime,
) -> datetime:
    if row.symbol != expected_symbol:
        raise ValueError("provider row symbol does not match the requested symbol")
    if not start_date <= row.trading_date <= end_date:
        raise ValueError("provider row trading_date is outside the requested range")
    values = (row.open, row.high, row.low, row.close)
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 for value in values):
        raise ValueError("provider row OHLC values must be finite and positive")
    if not isinstance(row.volume, int) or isinstance(row.volume, bool) or row.volume <= 0:
        raise ValueError("provider row volume must be a positive integer")
    available_at = xnys_session_close_at(row.trading_date)
    if available_at > observed_at:
        raise ValueError("provider returned a daily bar before its XNYS session close")
    if available_at > requested_cutoff:
        raise ValueError("provider returned a daily bar after the ingestion cutoff")
    return available_at


def migrate_legacy_market_prices() -> int:
    """Append baseline revisions for pre-versioning rows without altering them."""
    upgrade_market_price_revisions_schema()
    with SessionLocal() as db:
        added = _add_legacy_revisions(db)
        db.commit()
        return added


def upgrade_market_price_revisions_schema() -> bool:
    """Upgrade the empty pre-release revision table without dropping it.

    Returns whether an old, empty table required the in-place upgrade. Existing
    deployments with data deliberately fail closed instead of guessing how to
    rewrite their history.
    """
    Base.metadata.create_all(bind=engine)
    with SessionLocal.begin() as db:
        db.execute(text("LOCK TABLE market_price_revisions IN ACCESS EXCLUSIVE MODE"))
        columns = set(
            db.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'market_price_revisions'"
                )
            )
        )
        current_columns = {
            "id", "market_price_id", "ingestion_run_id", "symbol", "trading_date",
            "open", "high", "low", "close", "volume", "source", "content_hash",
            "revision_number", "available_at", "observed_at", "is_initial_backfill",
        }
        if columns == current_columns:
            return False
        old_columns = (current_columns - {"market_price_id", "revision_number"}) | {"legacy_market_price_id"}
        if columns != old_columns:
            raise RuntimeError("market_price_revisions has an unsupported schema; no migration was applied")
        row_count = db.scalar(text("SELECT COUNT(*) FROM market_price_revisions"))
        if row_count != 0:
            raise RuntimeError("market_price_revisions pre-release schema contains rows; refusing in-place upgrade")

        db.execute(text("ALTER TABLE market_price_revisions RENAME COLUMN legacy_market_price_id TO market_price_id"))
        db.execute(text("ALTER TABLE market_price_revisions ADD COLUMN revision_number integer NOT NULL"))
        db.execute(text("ALTER TABLE market_price_revisions RENAME CONSTRAINT uq_market_price_revision_legacy_price TO uq_market_price_revision_market_price"))
        db.execute(text("ALTER TABLE market_price_revisions DROP CONSTRAINT uq_market_price_revision_content"))
        db.execute(
            text(
                "ALTER TABLE market_price_revisions ADD CONSTRAINT "
                "uq_market_price_revision_number UNIQUE (symbol, trading_date, source, revision_number)"
            )
        )
        return True


def ingest_market_data(
    symbols: list[str],
    start_date: date,
    end_date: date,
    as_of_time: datetime,
    *,
    source: str = YAHOO_SOURCE,
    fetcher: Callable[[str, date, date], list[DailyPrice]] = fetch_daily_prices,
    now_factory: Callable[[], datetime] = _utc_now,
) -> IngestionSummary:
    if not symbols:
        raise ValueError("symbols must not be empty")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if not source:
        raise ValueError("source must not be empty")
    requested_cutoff = normalize_utc(as_of_time)

    with SessionLocal() as db:
        run = IngestionRun(source=source, as_of_time=requested_cutoff, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        inserted_count = 0
        skipped_count = 0
        try:
            if _add_legacy_revisions(db):
                db.flush()
            for symbol in symbols:
                rows = fetcher(symbol, start_date, end_date)
                # The system cannot have observed a provider response before
                # the fetch returned. This timestamp intentionally comes
                # after the external call, not at ingestion start.
                observed_at = normalize_utc(now_factory(), name="observed_at")
                if any(row.source != source for row in rows):
                    raise ValueError("fetched row source does not match ingestion source")

                if not rows:
                    continue

                row_symbols = {row.symbol for row in rows}
                normalized_symbol = normalize_symbol(symbol)
                if row_symbols != {normalized_symbol}:
                    raise ValueError("provider row symbol does not match the requested symbol")
                dates = {row.trading_date for row in rows}

                existing_legacy_keys = set(
                    db.execute(
                        select(MarketPrice.trading_date, MarketPrice.source).where(
                            MarketPrice.symbol == normalized_symbol,
                            MarketPrice.trading_date.in_(dates),
                            MarketPrice.source == source,
                        )
                    ).all()
                )

                latest_revision_by_date: dict[date, MarketPriceRevision] = {}
                for revision in db.scalars(
                    select(MarketPriceRevision).where(
                            MarketPriceRevision.symbol == normalized_symbol,
                            MarketPriceRevision.trading_date.in_(dates),
                            MarketPriceRevision.source == source,
                        )
                ):
                    previous = latest_revision_by_date.get(revision.trading_date)
                    if previous is None or revision.revision_number > previous.revision_number:
                        latest_revision_by_date[revision.trading_date] = revision

                for row in rows:
                    key = (row.trading_date, row.source)
                    content_hash = _content_hash(row)
                    latest_revision = latest_revision_by_date.get(row.trading_date)
                    if latest_revision is not None and latest_revision.content_hash == content_hash:
                        skipped_count += 1
                        continue

                    available_at = _validate_provider_row(
                        row,
                        expected_symbol=normalized_symbol,
                        start_date=start_date,
                        end_date=end_date,
                        requested_cutoff=requested_cutoff,
                        observed_at=observed_at,
                    )
                    is_initial_backfill = (
                        latest_revision is None
                        and key not in existing_legacy_keys
                        and row.trading_date < observed_at.date()
                    )
                    market_price_id = None
                    if key not in existing_legacy_keys:
                        price = MarketPrice(
                                symbol=row.symbol,
                                trading_date=row.trading_date,
                                open=row.open,
                                high=row.high,
                                low=row.low,
                                close=row.close,
                                volume=row.volume,
                                source=row.source,
                                fetched_at=observed_at,
                            )
                        db.add(price)
                        db.flush()
                        market_price_id = price.id
                        existing_legacy_keys.add(key)
                    revision = MarketPriceRevision(
                            ingestion_run_id=run_id,
                            market_price_id=market_price_id,
                            symbol=row.symbol,
                            trading_date=row.trading_date,
                            open=row.open,
                            high=row.high,
                            low=row.low,
                            close=row.close,
                            volume=row.volume,
                            source=row.source,
                            revision_number=(latest_revision.revision_number + 1 if latest_revision is not None else 1),
                            content_hash=content_hash,
                            available_at=available_at,
                            observed_at=observed_at,
                            is_initial_backfill=is_initial_backfill,
                        )
                    db.add(revision)
                    latest_revision_by_date[row.trading_date] = revision
                    inserted_count += 1

            run.status = "completed"
            run.completed_at = normalize_utc(now_factory(), name="completed_at")
            db.commit()

            return IngestionSummary(
                run_id=run_id,
                source=source,
                as_of_time=requested_cutoff,
                inserted_count=inserted_count,
                skipped_count=skipped_count,
                status="completed",
            )
        except Exception:
            db.rollback()
            failed_run = db.get(IngestionRun, run_id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.completed_at = normalize_utc(now_factory(), name="completed_at")
                db.commit()
            raise
