"""Operational jobs and immutable versions for material analysis."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class MaterialAnalysisJob(Base):
    __tablename__ = "material_analysis_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_material_analysis_jobs_idempotency_key"),
        CheckConstraint("source_type IN ('official_filing', 'uploaded_media')", name="ck_material_analysis_jobs_source_type"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'blocked_data')", name="ck_material_analysis_jobs_status"),
        CheckConstraint("lease_epoch >= 0", name="ck_material_analysis_jobs_lease_epoch"),
        Index("ix_material_analysis_jobs_claim", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evidence_event_versions_v2.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    force: Mapped[bool] = mapped_column(nullable=False, default=False)
    cache_hit: Mapped[bool] = mapped_column(nullable=False, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safe_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("material_analysis_versions.id", ondelete="RESTRICT", use_alter=True,
                         name="fk_material_analysis_jobs_result"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MaterialAnalysisVersion(Base):
    __tablename__ = "material_analysis_versions"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "version_no", name="uq_material_analysis_versions_source_version"),
        UniqueConstraint("job_id", name="uq_material_analysis_versions_job"),
        CheckConstraint("source_type IN ('official_filing', 'uploaded_media')", name="ck_material_analysis_versions_source_type"),
        CheckConstraint("version_no >= 1", name="ck_material_analysis_versions_version_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence_event_versions_v2.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("material_analysis_versions.id", ondelete="RESTRICT"), nullable=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("material_analysis_jobs.id", ondelete="RESTRICT"), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    actual_model: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


MATERIAL_ANALYSIS_TABLES = (MaterialAnalysisJob.__table__, MaterialAnalysisVersion.__table__)
