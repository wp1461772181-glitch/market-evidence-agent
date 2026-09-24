"""Append-only evidence revisions that may branch from one forecast snapshot.

The existing ``ForecastRevision`` link intentionally forms a single linear
Week 8 history.  Evidence-led review can legitimately offer more than one
alternative reading of a saved forecast, so it lives in this additive side
table rather than weakening the older unique-parent constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class EvidenceRevision(Base):
    """One immutable evidence-led copy of a prior numerical forecast.

    ``revised_snapshot_id`` points to a newly created snapshot with the exact
    same market-model values as its parent.  The evidence conclusion is kept
    alongside it and remains explicitly pending human verification.
    """

    __tablename__ = "evidence_revisions"
    __table_args__ = (
        UniqueConstraint("revised_snapshot_id", name="uq_evidence_revisions_revised_snapshot"),
        UniqueConstraint("parent_snapshot_id", "source_type", "source_id", name="uq_evidence_revisions_source_parent"),
        CheckConstraint("source_type IN ('official_filing', 'uploaded_media')", name="ck_evidence_revision_source_type"),
        CheckConstraint("mode IN ('manual', 'automatic')", name="ck_evidence_revision_mode"),
        CheckConstraint("direction_status = 'review_required'", name="ck_evidence_revision_direction_status"),
        CheckConstraint("official_confirmation IN (TRUE, FALSE)", name="ck_evidence_revision_official_confirmation"),
        CheckConstraint(
            "(source_type = 'official_filing' AND official_confirmation = TRUE "
            "AND credibility_stars IS NULL AND impact_severity IS NULL) "
            "OR (source_type = 'uploaded_media' AND official_confirmation = FALSE "
            "AND credibility_stars >= 1 AND credibility_stars <= 5 "
            "AND impact_severity IN ('low', 'medium', 'high'))",
            name="ck_evidence_revision_source_confidence",
        ),
        CheckConstraint("length(trim(evidence_summary)) > 0", name="ck_evidence_revision_summary"),
        CheckConstraint("length(trim(evidence_quote)) > 0", name="ck_evidence_revision_quote"),
        CheckConstraint("analysis_text_characters > 0", name="ck_evidence_revision_analysis_text_characters"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parent_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("forecast_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revised_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("forecast_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_text_characters: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_text_truncated: Mapped[bool] = mapped_column(nullable=False)
    official_confirmation: Mapped[bool] = mapped_column(nullable=False)
    credibility_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credibility_reason: Mapped[str | None] = mapped_column(String(700), nullable=True)
    impact_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_status: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_summary: Mapped[str] = mapped_column(String(700), nullable=False)
    evidence_quote: Mapped[str] = mapped_column(String(240), nullable=False)
    model_impact_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    direction_status: Mapped[str] = mapped_column(String(32), nullable=False, default="review_required")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
