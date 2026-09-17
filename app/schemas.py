from datetime import date, datetime
from typing import Any, Literal
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


class ForecastSnapshotTimelineEntry(ForecastSnapshotResponse):
    """One saved snapshot plus its position and link in a revision chain."""

    version: int
    parent_snapshot_id: UUID | None
    revision_reason: str | None


class ForecastSnapshotTimelineResponse(BaseModel):
    root_snapshot_id: UUID
    snapshots: list[ForecastSnapshotTimelineEntry]


class ResearchRunRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    as_of_time: datetime
    document_ids: list[str] = Field(min_length=1, max_length=10)


class ResearchRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    as_of_time: datetime
    source_ids: list[str]
    source_snapshot: list[dict[str, Any]]
    provider: str
    request_model: str
    status: str
    current_stage: str
    node_trace: list[dict[str, Any]]
    report: dict[str, Any] | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class ForecastRefreshRequest(BaseModel):
    """One explicit source-triggered rolling refresh request."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=10)
    before_as_of_time: datetime
    after_as_of_time: datetime
    document_id: str = Field(min_length=1, max_length=128)
    revision_mode: Literal["rolling_refresh"] = "rolling_refresh"


class ForecastRefreshSnapshotResponse(BaseModel):
    id: UUID
    symbol: str
    feature_trading_date: date
    feature_as_of_time: datetime
    model_version: str
    bearish_probability: float
    neutral_probability: float
    bullish_probability: float


class TargetWindowResponse(BaseModel):
    start: date
    end: date


class ForecastRefreshResponse(BaseModel):
    revision_mode: Literal["rolling_refresh"]
    original_snapshot: ForecastRefreshSnapshotResponse
    revised_snapshot: ForecastRefreshSnapshotResponse
    probability_delta: dict[str, float]
    target_windows: dict[str, TargetWindowResponse]
    trigger: dict[str, Any]
    research_run: dict[str, Any]
    limitations: list[str]
