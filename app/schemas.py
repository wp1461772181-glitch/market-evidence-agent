from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ForecastRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)


class ForecastResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    bullish_probability: float
    neutral_probability: float
    bearish_probability: float
    model_version: str
    created_at: datetime


class ForecastSnapshotResponse(BaseModel):
    """Persisted provenance for one offline Week 4-model prediction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    feature_trading_date: date
    feature_as_of_time: datetime
    model_version: str
    model_sha256: str
    model_manifest_sha256: str
    feature_export_sha256: str
    feature_version: str
    feature_source: str
    feature_snapshot_mode: str
    feature_values: dict[str, float]
    bearish_probability: float
    neutral_probability: float
    bullish_probability: float
    created_at: datetime
