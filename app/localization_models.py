"""Separate, reusable display translations for immutable AI records."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AIContentTranslation(Base):
    __tablename__ = "ai_content_translations"
    __table_args__ = (
        UniqueConstraint(
            "content_kind", "content_id", "source_sha256", "locale", "prompt_version",
            name="uq_ai_content_translations_source_locale_prompt",
        ),
        CheckConstraint("content_kind IN ('material_analysis', 'forecast_brief')",
                        name="ck_ai_content_translations_content_kind"),
        CheckConstraint("locale = 'en-US'", name="ck_ai_content_translations_locale"),
        Index("ix_ai_content_translations_content", "content_kind", "content_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    content_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fields: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    actual_model: Mapped[str] = mapped_column(String(160), nullable=False)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


LOCALIZATION_TABLES = (AIContentTranslation.__table__,)
