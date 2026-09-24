"""Storage and validation for manual, unconfirmed market evidence.

This module accepts only bytes supplied in an upload request.  It deliberately
does not retrieve the declared source URL, call a model, alter a forecast, or
promote a user-supplied source to an official source.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import engine
from .models import UploadedEvidence
from .services import is_valid_symbol, normalize_symbol


MAX_UPLOAD_BYTES = 5_000_000
MAX_EXTRACTED_TEXT_CHARACTERS = 120_000
MAX_CONTENT_PREVIEW_CHARACTERS = 2_000
MAX_PDF_PAGES = 100
SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".pdf"})


class ManualEvidenceError(ValueError):
    """A safe validation error for a user-uploaded source."""


def create_uploaded_evidence(
    *,
    symbol: str,
    title: str,
    source_url: str,
    published_at: datetime,
    credibility_stars: int,
    credibility_reason: str,
    impact_severity: str,
    filename: str | None,
    content: bytes,
    db: Session,
    observed_at: datetime | None = None,
) -> tuple[UploadedEvidence, bool]:
    """Persist one upload, returning ``created=False`` for an exact duplicate.

    The duplicate key includes the stock symbol and original file hash.  It is
    intentionally scoped to a symbol so an identical press clipping can still
    be attached independently to two companies by an explicit user action.
    """
    normalized_symbol = _validated_symbol(symbol)
    clean_title = _required_text(title, "title", maximum=300)
    clean_reason = _required_text(credibility_reason, "credibility_reason", maximum=700)
    clean_impact_severity = _impact_severity(impact_severity)
    clean_url = _validate_source_url(source_url)
    clean_filename, suffix = _validate_filename(filename)
    observed_at = _require_aware_utc(observed_at or datetime.now(UTC), "observed_at")
    published_at = _require_aware_utc(published_at, "published_at")
    if published_at > observed_at:
        raise ManualEvidenceError("published_at must not be later than upload time")
    if not 1 <= credibility_stars <= 5:
        raise ManualEvidenceError("credibility_stars must be between 1 and 5")
    if not content:
        raise ManualEvidenceError("uploaded file must not be empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ManualEvidenceError(f"uploaded file exceeds the {MAX_UPLOAD_BYTES} byte limit")

    content_text = _extract_content_text(content, suffix)
    content_sha256 = hashlib.sha256(content).hexdigest()
    existing = (
        db.query(UploadedEvidence)
        .filter(
            UploadedEvidence.symbol == normalized_symbol,
            UploadedEvidence.content_sha256 == content_sha256,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    evidence = UploadedEvidence(
        symbol=normalized_symbol,
        title=clean_title,
        source_url=clean_url,
        published_at=published_at,
        observed_at=observed_at,
        credibility_stars=credibility_stars,
        credibility_reason=clean_reason,
        impact_severity=clean_impact_severity,
        filename=clean_filename,
        content_sha256=content_sha256,
        raw_content=content,
        content_text=content_text,
        status="unconfirmed",
    )
    db.add(evidence)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(UploadedEvidence)
            .filter(
                UploadedEvidence.symbol == normalized_symbol,
                UploadedEvidence.content_sha256 == content_sha256,
            )
            .one_or_none()
        )
        if existing is None:
            raise
        return existing, False
    db.refresh(evidence)
    return evidence, True


def uploaded_evidence_for_symbol(*, symbol: str, db: Session) -> list[UploadedEvidence]:
    normalized_symbol = _validated_symbol(symbol)
    return (
        db.query(UploadedEvidence)
        .filter(UploadedEvidence.symbol == normalized_symbol)
        .order_by(UploadedEvidence.published_at.desc(), UploadedEvidence.observed_at.desc(), UploadedEvidence.id.desc())
        .all()
    )


def content_preview(content_text: str) -> str:
    """Return a small display-only preview without exposing the original bytes."""
    normalized = " ".join(content_text.split())
    if len(normalized) <= MAX_CONTENT_PREVIEW_CHARACTERS:
        return normalized
    return f"{normalized[:MAX_CONTENT_PREVIEW_CHARACTERS].rstrip()}…"


def create_uploaded_evidence_table() -> None:
    UploadedEvidence.__table__.create(bind=engine, checkfirst=True)
    # ``create_all`` does not widen a pre-existing local table.  Keep the
    # previously saved uploads readable and default their newly separated
    # impact label to medium without changing their credibility rating.
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE uploaded_evidence "
                    "ADD COLUMN IF NOT EXISTS impact_severity VARCHAR(16) NOT NULL DEFAULT 'medium'"
                )
            )
            connection.execute(
                text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'ck_uploaded_evidence_impact_severity') THEN "
                    "ALTER TABLE uploaded_evidence "
                    "ADD CONSTRAINT ck_uploaded_evidence_impact_severity "
                    "CHECK (impact_severity IN ('low', 'medium', 'high')); "
                    "END IF; END $$;"
                )
            )


def _validated_symbol(symbol: str) -> str:
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise ManualEvidenceError("symbol must contain 1-5 ASCII letters")
    return normalized_symbol


def _required_text(value: str, field: str, *, maximum: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ManualEvidenceError(f"{field} must not be empty")
    if len(cleaned) > maximum:
        raise ManualEvidenceError(f"{field} must be at most {maximum} characters")
    return cleaned


def _validate_source_url(source_url: str) -> str:
    if source_url != source_url.strip() or any(character.isspace() for character in source_url) or len(source_url) > 2048:
        raise ManualEvidenceError("source_url must be a trimmed HTTPS URL up to 2048 characters")
    parsed = urlsplit(source_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ManualEvidenceError("source_url must be an HTTPS URL without embedded credentials")
    return source_url


def _validate_filename(filename: str | None) -> tuple[str, str]:
    if not filename:
        raise ManualEvidenceError("uploaded file must have a filename")
    clean_filename = Path(filename).name
    if clean_filename != filename or len(clean_filename) > 255:
        raise ManualEvidenceError("uploaded filename is invalid")
    suffix = Path(clean_filename).suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ManualEvidenceError("uploaded file must be TXT, Markdown, or PDF")
    return clean_filename, suffix


def _impact_severity(value: str) -> str:
    if value not in {"low", "medium", "high"}:
        raise ManualEvidenceError("impact_severity must be one of: low, medium, high")
    return value


def _require_aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManualEvidenceError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _extract_content_text(content: bytes, suffix: str) -> str:
    if suffix == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(content), strict=False)
            parts: list[str] = []
            length = 0
            for page in reader.pages[:MAX_PDF_PAGES]:
                extracted = page.extract_text() or ""
                if extracted:
                    remaining = MAX_EXTRACTED_TEXT_CHARACTERS - length
                    parts.append(extracted[:remaining])
                    length += min(len(extracted), remaining)
                if length >= MAX_EXTRACTED_TEXT_CHARACTERS:
                    break
        except (PdfReadError, OSError, ValueError, TypeError) as exc:
            raise ManualEvidenceError("uploaded PDF could not be read") from exc
        text = "\n".join(parts).strip()
        return text or "[PDF uploaded; no extractable text preview is available.]"
    try:
        return content.decode("utf-8").strip()[:MAX_EXTRACTED_TEXT_CHARACTERS]
    except UnicodeDecodeError as exc:
        raise ManualEvidenceError("text and Markdown uploads must be UTF-8") from exc
