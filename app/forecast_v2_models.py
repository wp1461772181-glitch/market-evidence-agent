"""Persistence records for the append-only V2 forecast workflow.

These tables deliberately have no foreign keys to the legacy forecast tables.
They store immutable forecast/evidence/evaluation snapshots and the durable job
state needed by the V2 worker.  Job and monitor rows are operational records
and may advance their status; a new forecast, evidence extraction, or result
correction is always represented by a new V2 row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


JOB_KINDS = ("new", "manual_revision", "automatic_revision")
JOB_STATUSES = ("queued", "running", "succeeded", "succeeded_no_change", "blocked_data", "failed")
EVIDENCE_SOURCE_TYPES = ("official_filing", "uploaded_media")
EVIDENCE_REVIEW_STATUSES = ("pending_review", "accepted", "rejected")
MODEL_STATUSES = ("research_only", "experimental_joint", "baseline_only", "blocked_data")
EVALUATION_STATUSES = ("pending", "succeeded", "blocked_price", "failed")


class ForecastJobV2(Base):
    """Durable unit of work for one requested V2 forecast or revision."""

    __tablename__ = "forecast_jobs_v2"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_forecast_jobs_v2_idempotency_key"),
        CheckConstraint(
            "kind IN ('new', 'manual_revision', 'automatic_revision')",
            name="ck_forecast_jobs_v2_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'succeeded_no_change', 'blocked_data', 'failed')",
            name="ck_forecast_jobs_v2_status",
        ),
        CheckConstraint("lease_epoch >= 0", name="ck_forecast_jobs_v2_lease_epoch"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    root_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("forecast_versions_v2.id", ondelete="RESTRICT", use_alter=True, name="fk_jobs_v2_root_version"),
        nullable=True,
        index=True,
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("forecast_versions_v2.id", ondelete="RESTRICT", use_alter=True, name="fk_jobs_v2_parent_version"),
        nullable=True,
        index=True,
    )
    source_refs: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    attempts: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    result_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("forecast_versions_v2.id", ondelete="RESTRICT", use_alter=True, name="fk_jobs_v2_result_version"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceEventVersionV2(Base):
    """Frozen source/extraction/review state used as a V2 evidence input."""

    __tablename__ = "evidence_event_versions_v2"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "content_sha256",
            "review_status",
            "extraction_schema_version",
            "review_fingerprint",
            name="uq_evidence_event_versions_v2_source_state",
        ),
        CheckConstraint(
            "source_type IN ('official_filing', 'uploaded_media')",
            name="ck_evidence_event_versions_v2_source_type",
        ),
        CheckConstraint(
            "review_status IN ('pending_review', 'accepted', 'rejected')",
            name="ck_evidence_event_versions_v2_review_status",
        ),
        CheckConstraint(
            "user_rating_stars IS NULL OR (user_rating_stars >= 1 AND user_rating_stars <= 5)",
            name="ck_evidence_event_versions_v2_user_rating",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("evidence_event_versions_v2.id", ondelete="RESTRICT"),
        nullable=True,
    )
    structured_facts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    citations: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    user_rating_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    review_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    extraction_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ForecastVersionV2(Base):
    """One immutable answer to a fixed V2 target contract."""

    __tablename__ = "forecast_versions_v2"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_forecast_versions_v2_job"),
        UniqueConstraint("root_id", "version_no", name="uq_forecast_versions_v2_root_version_no"),
        CheckConstraint("version_no >= 1", name="ck_forecast_versions_v2_version_no"),
        CheckConstraint(
            "model_status IN ('research_only', 'experimental_joint', 'baseline_only', 'blocked_data')",
            name="ck_forecast_versions_v2_model_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("forecast_jobs_v2.id", ondelete="RESTRICT", use_alter=True, name="fk_versions_v2_job"),
        nullable=False,
    )
    root_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("forecast_versions_v2.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("forecast_versions_v2.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    target_contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    target_contract_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    market_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_input_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_version_manifest: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    feature_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    baseline_probabilities: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    joint_probabilities: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_status: Mapped[str] = mapped_column(String(32), nullable=False)
    model_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    research_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OfficialMonitorRunV2(Base):
    """One persisted hourly scan, including partial failures by symbol."""

    __tablename__ = "official_monitor_runs_v2"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed')",
            name="ck_official_monitor_runs_v2_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    per_symbol_results: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retry_reason: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    last_success_watermark: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class ForecastEvaluationV2(Base):
    """Append-only score for a forecast version and one price-result revision."""

    __tablename__ = "forecast_evaluations_v2"
    __table_args__ = (
        UniqueConstraint(
            "forecast_version_id", "result_version", name="uq_forecast_evaluations_v2_version_result"
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'blocked_price', 'failed')",
            name="ck_forecast_evaluations_v2_status",
        ),
        CheckConstraint("result_version >= 1", name="ck_forecast_evaluations_v2_result_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    forecast_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("forecast_versions_v2.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_contract_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actual_target_close: Mapped[float | None] = mapped_column(nullable=True)
    price_input_version: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actual_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    label_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    brier_score: Mapped[float | None] = mapped_column(nullable=True)
    log_loss: Mapped[float | None] = mapped_column(nullable=True)
    direction_correct: Mapped[bool | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_message: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


V2_TABLES = (
    ForecastJobV2.__table__,
    EvidenceEventVersionV2.__table__,
    ForecastVersionV2.__table__,
    OfficialMonitorRunV2.__table__,
    ForecastEvaluationV2.__table__,
)
