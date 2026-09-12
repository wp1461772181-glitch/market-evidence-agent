import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Forecast(Base):
    __tablename__ = "forecasts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    bullish_probability: Mapped[float] = mapped_column(Float, nullable=False)
    neutral_probability: Mapped[float] = mapped_column(Float, nullable=False)
    bearish_probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ForecastSnapshot(Base):
    """An append-only-in-application offline Week 5 prediction with its inputs.

    This intentionally has no foreign key to ``Forecast``.  The old endpoint
    still produces its Week 1 ``mock-v1`` records, while this table preserves
    an independently reproducible record made from the Week 4 model artifact.
    """

    __tablename__ = "forecast_snapshots"
    __table_args__ = (
        CheckConstraint(
            "bearish_probability >= 0 AND bearish_probability <= 1 "
            "AND neutral_probability >= 0 AND neutral_probability <= 1 "
            "AND bullish_probability >= 0 AND bullish_probability <= 1",
            name="ck_forecast_snapshot_probability_bounds",
        ),
        CheckConstraint(
            "abs((bearish_probability + neutral_probability + bullish_probability) - 1.0) < 0.00000001",
            name="ck_forecast_snapshot_probability_sum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    feature_trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    feature_as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_export_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_source: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_snapshot_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_values: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    bearish_probability: Mapped[float] = mapped_column(Float, nullable=False)
    neutral_probability: Mapped[float] = mapped_column(Float, nullable=False)
    bullish_probability: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ForecastRevision(Base):
    """One append-only link from a new forecast snapshot to its predecessor.

    Existing, unlinked ``ForecastSnapshot`` rows are version-one roots.  The
    unique parent reference permits one successor only, which keeps every
    revision history a simple linear chain without needing a separate event
    system or a migration of existing records.
    """

    __tablename__ = "forecast_revisions"
    __table_args__ = (
        UniqueConstraint("parent_snapshot_id", name="forecast_revisions_parent_snapshot_id_key"),
        CheckConstraint("length(trim(reason)) > 0", name="ck_forecast_revision_reason_nonblank"),
        CheckConstraint("snapshot_id <> parent_snapshot_id", name="ck_forecast_revision_not_self"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("forecast_snapshots.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    parent_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("forecast_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    root_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("forecast_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(String(280), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EventExtraction(Base):
    """A validated, immutable cache entry for one document extraction request.

    The cache key includes the complete document input and prompt settings.  A
    provider response is only stored after local validation establishes that
    every quoted passage really occurs in the supplied document.
    """

    __tablename__ = "event_extractions"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_metadata: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    request_model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    response_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = (
        UniqueConstraint("symbol", "trading_date", "source", name="uq_market_prices_symbol_date_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class MarketPriceRevision(Base):
    """An append-only observation of one source's daily OHLCV bar.

    ``available_at`` is the earliest possible end-of-session availability.
    ``observed_at`` is when this system actually received the version. Existing
    Week 2 rows become explicitly marked initial backfills; they are useful for
    historical research, but not proof that the system knew them at that time.
    """

    __tablename__ = "market_price_revisions"
    __table_args__ = (
        UniqueConstraint("market_price_id", name="uq_market_price_revision_market_price"),
        UniqueConstraint("symbol", "trading_date", "source", "revision_number", name="uq_market_price_revision_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    market_price_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("market_prices.id", ondelete="RESTRICT"),
        nullable=True,
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    is_initial_backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
