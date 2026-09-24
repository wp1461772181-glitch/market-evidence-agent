from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ForecastRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)


class ForecastRunRequest(BaseModel):
    """One explicit request for a saved numeric market forecast."""

    model_config = ConfigDict(extra="forbid")

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


class ForecastRunResponse(ForecastSnapshotResponse):
    """A newly archived numeric forecast plus the target it will be judged on."""

    cutoff_date: date
    target_window: dict[str, date]
    model_status: Literal["experimental_offline_model"]
    limitations: list[str]


class ForecastSnapshotTimelineEntry(ForecastSnapshotResponse):
    """One saved snapshot plus its position and link in a revision chain."""

    version: int
    root_snapshot_id: UUID
    parent_snapshot_id: UUID | None
    revision_reason: str | None
    target_window: dict[str, date] | None = None


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


class DashboardEvaluation(BaseModel):
    """Small, fixed-scope summary of the saved Week 4 offline evaluation."""

    artifact_version: str
    model_name: str
    scope: str
    data_as_of_time: datetime
    feature_version: str
    snapshot_mode: str
    fold_count: int
    test_rows: int
    models: dict[str, dict[str, float]]
    limitations: list[str]


class DashboardCandle(BaseModel):
    """One saved daily price bar for the dashboard's historical chart."""

    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    benchmark_close: float | None = None


class DashboardPriceHistory(BaseModel):
    """Bounded historical market data displayed alongside saved research."""

    source: str
    latest_trading_date: date | None
    candles: list[DashboardCandle]


class DashboardResponse(BaseModel):
    symbol: str
    snapshots: list[ForecastSnapshotTimelineEntry]
    refresh_reports: list[ForecastRefreshResponse]
    evaluation: DashboardEvaluation | None
    price_history: DashboardPriceHistory


class SecFilingInventoryItem(BaseModel):
    """An official filing awaiting review; never forecast input by itself."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    accession_number: str
    form: str
    filed_at: date
    accepted_at: str | None
    primary_document: str
    source_url: str
    source: str
    review_status: str
    human_review_note: str | None
    reviewed_at: datetime | None
    observed_at: datetime
    content_status: str
    content_observed_at: datetime | None
    content_excerpt_sha256: str | None
    content_truncated: bool
    content_error: str | None
    review_scope_note: str = (
        "Manual source relevance review only; it does not validate claims, directions, or forecasts."
    )


class SecFilingInventoryResponse(BaseModel):
    symbol: str
    filings: list[SecFilingInventoryItem]


class SecFilingScanResponse(SecFilingInventoryResponse):
    cik: str | None
    discovered_count: int
    created_count: int
    skipped_count: int
    observed_at: datetime


class SecFilingContentResponse(SecFilingInventoryItem):
    content_excerpt: str | None
    cache_hit: bool


class SecFilingReviewRequest(BaseModel):
    """A local human decision about whether this official source is relevant."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["accepted", "rejected"]
    note: Annotated[str, Field(min_length=1, max_length=700)]


class UploadedEvidenceItem(BaseModel):
    """One manual source.  Star ratings are human input, never probabilities."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    title: str
    source_url: str
    published_at: datetime
    observed_at: datetime
    credibility_stars: int
    credibility_reason: str
    impact_severity: Literal["low", "medium", "high"]
    filename: str
    content_sha256: str
    content_preview: str = ""
    status: Literal["unconfirmed"]


class UploadedEvidenceResponse(BaseModel):
    symbol: str
    items: list[UploadedEvidenceItem]


class EvidenceRevisionRequest(BaseModel):
    """One selected historical forecast plus one later source document."""

    model_config = ConfigDict(extra="forbid")

    parent_snapshot_id: UUID
    source_type: Literal["official_filing", "uploaded_media"]
    source_id: UUID
    mode: Literal["manual", "automatic"] = "manual"


class EvidenceRevisionResponse(BaseModel):
    """A source-grounded conclusion whose direction still needs human review."""

    id: UUID
    symbol: str
    mode: Literal["manual", "automatic"]
    source_type: Literal["official_filing", "uploaded_media"]
    source_id: UUID
    parent_snapshot_id: UUID
    revised_snapshot_id: UUID
    status: Literal["pending_review"]
    review_status: Literal["pending_review"]
    evidence_conclusion: str
    model_probability_changed: Literal[False]
    source: dict[str, Any]
    evidence: dict[str, Any]
    probabilities: dict[str, Any]
    created_at: datetime
    limitations: list[str]


class EvidenceRevisionInventoryResponse(BaseModel):
    symbol: str
    revisions: list[EvidenceRevisionResponse]
