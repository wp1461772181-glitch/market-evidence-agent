"""Create source-grounded, human-review-pending evidence revisions.

This module deliberately copies a selected forecast's numerical probabilities.
It never invents a probability adjustment from a filing or a media report.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .event_extraction import DocumentInput, EventExtractionError, EventProvider, extract_document, project_events_for_review
from .event_provider import DEFAULT_DEEPSEEK_MODEL, EventProviderError
from .evidence_revision_models import EvidenceRevision
from .models import ForecastRevision, ForecastSnapshot, SecFilingInventory, UploadedEvidence
from .services import is_valid_symbol, normalize_symbol


EvidenceSourceType = Literal["official_filing", "uploaded_media"]
EvidenceRevisionMode = Literal["manual", "automatic"]
_SOURCE_TYPES = frozenset({"official_filing", "uploaded_media"})
_REVISION_MODES = frozenset({"manual", "automatic"})
_MAX_AUTOMATIC_AGE = timedelta(days=3)
_NO_EXTRACTABLE_PDF_TEXT = "[PDF uploaded; no extractable text preview is available.]"
MAX_ANALYSIS_CHARACTERS = 20_000


class EvidenceRevisionError(ValueError):
    """A safe, user-displayable evidence revision failure."""


@dataclass(frozen=True)
class EvidenceRevisionResult:
    revision: EvidenceRevision
    original_snapshot: ForecastSnapshot
    revised_snapshot: ForecastSnapshot

    def as_dict(self) -> dict:
        return {
            "id": str(self.revision.id),
            "symbol": self.revision.symbol,
            "mode": self.revision.mode,
            "source_type": self.revision.source_type,
            "source_id": str(self.revision.source_id),
            "parent_snapshot_id": str(self.revision.parent_snapshot_id),
            "revised_snapshot_id": str(self.revision.revised_snapshot_id),
            "status": "pending_review",
            "review_status": "pending_review",
            "evidence_conclusion": self.revision.evidence_summary,
            "model_probability_changed": False,
            "source": {
                "title": self.revision.source_title,
                "url": self.revision.source_url,
                "published_at": self.revision.source_published_at.isoformat(),
                "observed_at": self.revision.source_observed_at.isoformat(),
                "official_confirmation": self.revision.official_confirmation,
                "status": self.revision.source_status,
                "credibility_stars": self.revision.credibility_stars,
                "credibility_reason": self.revision.credibility_reason,
                "impact_severity": self.revision.impact_severity,
                "content_sha256": self.revision.source_content_sha256,
            },
            "evidence": {
                "summary": self.revision.evidence_summary,
                "quote": self.revision.evidence_quote,
                "model_impact_direction": self.revision.model_impact_direction,
                "direction_status": self.revision.direction_status,
                "extraction_cache_key": self.revision.extraction_cache_key,
                "analysis_text_characters": self.revision.analysis_text_characters,
                "analysis_text_truncated": self.revision.analysis_text_truncated,
            },
            "probabilities": {
                "original": _probabilities(self.original_snapshot),
                "revised": _probabilities(self.revised_snapshot),
                "numeric_probability_changed": False,
            },
            "created_at": self.revision.created_at.isoformat(),
            "limitations": [
                "The numerical probabilities are copied from the selected market-model forecast.",
                "The qualitative evidence conclusion is source-grounded but pending human verification and is not a causal price estimate.",
                (
                    f"Extraction analyzed only the first {self.revision.analysis_text_characters} characters of this source."
                    if self.revision.analysis_text_truncated
                    else "Extraction analyzed the complete saved source text."
                ),
                "Uploaded media remain unconfirmed even when the uploader assigns a high credibility rating.",
            ],
        }


@dataclass(frozen=True)
class _ResolvedSource:
    source_type: EvidenceSourceType
    source_id: UUID
    symbol: str
    document: DocumentInput
    available_at: datetime
    observed_at: datetime
    official_confirmation: bool
    credibility_stars: int | None
    credibility_reason: str | None
    impact_severity: str | None
    source_status: str
    source_content_sha256: str
    analysis_text_characters: int
    analysis_text_truncated: bool


def create_evidence_revision_tables() -> None:
    """Create only this additive table after its snapshot dependency exists."""
    from .database import engine

    from .models import EventExtraction

    ForecastSnapshot.__table__.create(bind=engine, checkfirst=True)
    ForecastRevision.__table__.create(bind=engine, checkfirst=True)
    SecFilingInventory.__table__.create(bind=engine, checkfirst=True)
    UploadedEvidence.__table__.create(bind=engine, checkfirst=True)
    EventExtraction.__table__.create(bind=engine, checkfirst=True)
    EvidenceRevision.__table__.create(bind=engine, checkfirst=True)


def create_evidence_revision(
    *,
    parent_snapshot_id: UUID | str,
    source_type: EvidenceSourceType,
    source_id: UUID | str,
    mode: EvidenceRevisionMode,
    db: Session,
    provider_factory: Callable[[], EventProvider],
    model: str = DEFAULT_DEEPSEEK_MODEL,
    checked_at: datetime | None = None,
) -> EvidenceRevisionResult:
    """Append a copy of one selected forecast with a validated evidence conclusion.

    The source must be newer than the selected forecast's creation time.  An
    automatic revision can only use an official filing and only during the
    three-day window requested for the scheduled checker.
    """
    parent_id = _uuid(parent_snapshot_id, "parent_snapshot_id")
    normalized_source_type = _choice(source_type, _SOURCE_TYPES, "source_type")
    normalized_mode = _choice(mode, _REVISION_MODES, "mode")
    resolved_source_id = _uuid(source_id, "source_id")
    if not model.strip():
        raise EvidenceRevisionError("model must not be empty")
    check_time = _as_utc(checked_at or datetime.now(UTC), "checked_at")

    parent = db.get(ForecastSnapshot, parent_id)
    if parent is None:
        raise EvidenceRevisionError("selected forecast snapshot was not found")
    source = _resolve_source(
        source_type=normalized_source_type,
        source_id=resolved_source_id,
        db=db,
    )
    if source.symbol != parent.symbol:
        raise EvidenceRevisionError("evidence source must belong to the selected forecast symbol")
    parent_created_at = _as_utc(parent.created_at, "parent forecast created_at")
    if source.available_at <= parent_created_at:
        raise EvidenceRevisionError("evidence source must become available after the selected forecast")
    if source.available_at > check_time:
        raise EvidenceRevisionError("evidence source is not available at the revision time")
    if normalized_mode == "automatic":
        if source.source_type != "official_filing":
            raise EvidenceRevisionError("automatic evidence revisions require an official filing")
        if check_time > parent_created_at + _MAX_AUTOMATIC_AGE:
            raise EvidenceRevisionError("automatic evidence revision is limited to sources within three days of the forecast")

    try:
        extraction = extract_document(
            source.document,
            db=db,
            provider_factory=provider_factory,
            model=model,
        )
        event = _selected_event(project_events_for_review(extraction.batch))
    except (EventExtractionError, EventProviderError) as exc:
        raise EvidenceRevisionError(f"evidence extraction failed; no forecast revision was created: {exc}") from exc

    revised_snapshot = _copy_snapshot(parent)
    revision = EvidenceRevision(
        parent_snapshot_id=parent.id,
        revised_snapshot_id=revised_snapshot.id,
        symbol=parent.symbol,
        source_type=source.source_type,
        source_id=source.source_id,
        mode=normalized_mode,
        source_title=source.document.title,
        source_url=source.document.source_url,
        source_published_at=source.available_at,
        source_observed_at=source.observed_at,
        source_content_sha256=source.document.sha256,
        analysis_text_characters=source.analysis_text_characters,
        analysis_text_truncated=source.analysis_text_truncated,
        official_confirmation=source.official_confirmation,
        credibility_stars=source.credibility_stars,
        credibility_reason=source.credibility_reason,
        impact_severity=source.impact_severity,
        source_status=source.source_status,
        extraction_cache_key=extraction.cache_key,
        evidence_summary=event["summary"],
        evidence_quote=event["evidence_quote"],
        model_impact_direction=event["impact_direction"],
        direction_status="review_required",
    )
    try:
        db.add(revised_snapshot)
        db.flush()
        db.add(revision)
        db.commit()
        db.refresh(revision)
        db.refresh(revised_snapshot)
    except IntegrityError as exc:
        db.rollback()
        raise EvidenceRevisionError("this evidence source already revised the selected forecast") from exc
    except Exception:
        db.rollback()
        raise
    return EvidenceRevisionResult(revision=revision, original_snapshot=parent, revised_snapshot=revised_snapshot)


def evidence_revisions_for_symbol(*, symbol: str, db: Session) -> list[EvidenceRevisionResult]:
    """Read all saved evidence revisions for one stock without invoking a model."""
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise EvidenceRevisionError("symbol must contain 1-5 ASCII letters")
    revisions = (
        db.query(EvidenceRevision)
        .filter(EvidenceRevision.symbol == normalized_symbol)
        .order_by(EvidenceRevision.created_at.asc(), EvidenceRevision.id.asc())
        .all()
    )
    results: list[EvidenceRevisionResult] = []
    for revision in revisions:
        parent = db.get(ForecastSnapshot, revision.parent_snapshot_id)
        revised = db.get(ForecastSnapshot, revision.revised_snapshot_id)
        if parent is None or revised is None or parent.symbol != normalized_symbol or revised.symbol != normalized_symbol:
            raise EvidenceRevisionError("saved evidence revision has an inconsistent forecast link")
        results.append(EvidenceRevisionResult(revision=revision, original_snapshot=parent, revised_snapshot=revised))
    return results


def _resolve_source(*, source_type: str, source_id: UUID, db: Session) -> _ResolvedSource:
    if source_type == "official_filing":
        filing = db.get(SecFilingInventory, source_id)
        if filing is None:
            raise EvidenceRevisionError("official filing was not found")
        if filing.content_status != "fetched" or not filing.content_excerpt or not filing.content_excerpt_sha256:
            raise EvidenceRevisionError("official filing requires fetched text before it can revise a forecast")
        if filing.review_status == "rejected":
            raise EvidenceRevisionError("rejected official filing cannot revise a forecast")
        analysis_text, truncated = _analysis_text(filing.content_excerpt)
        return _ResolvedSource(
            source_type="official_filing",
            source_id=filing.id,
            symbol=filing.symbol,
            document=DocumentInput(
                document_id=f"sec-{filing.id}",
                company=filing.symbol,
                ticker=filing.symbol,
                source_url=filing.source_url,
                source_domain=_https_hostname(filing.source_url),
                published_date=filing.filed_at,
                title=f"{filing.symbol} {filing.form} filing {filing.accession_number}",
                text=analysis_text,
                sha256=_text_sha256(analysis_text),
            ),
            available_at=_filing_available_at(filing),
            observed_at=_as_utc(filing.content_observed_at or filing.observed_at, "filing observed time"),
            official_confirmation=True,
            credibility_stars=None,
            credibility_reason=None,
            impact_severity=None,
            source_status=filing.review_status,
            source_content_sha256=filing.content_excerpt_sha256,
            analysis_text_characters=len(analysis_text),
            analysis_text_truncated=truncated,
        )
    if source_type == "uploaded_media":
        media = db.get(UploadedEvidence, source_id)
        if media is None:
            raise EvidenceRevisionError("uploaded media evidence was not found")
        if media.status != "unconfirmed":
            raise EvidenceRevisionError("uploaded media evidence has an unsupported status")
        if media.content_text == _NO_EXTRACTABLE_PDF_TEXT:
            raise EvidenceRevisionError("uploaded PDF has no extractable text and cannot revise a forecast")
        analysis_text, truncated = _analysis_text(media.content_text)
        return _ResolvedSource(
            source_type="uploaded_media",
            source_id=media.id,
            symbol=media.symbol,
            document=DocumentInput(
                document_id=f"media-{media.id}",
                company=media.symbol,
                ticker=media.symbol,
                source_url=media.source_url,
                source_domain=_https_hostname(media.source_url),
                published_date=_as_utc(media.published_at, "uploaded media published_at").date(),
                title=media.title,
                text=analysis_text,
                sha256=_text_sha256(analysis_text),
            ),
            available_at=_as_utc(media.published_at, "uploaded media published_at"),
            observed_at=_as_utc(media.observed_at, "uploaded media observed_at"),
            official_confirmation=False,
            credibility_stars=media.credibility_stars,
            credibility_reason=media.credibility_reason,
            impact_severity=media.impact_severity,
            source_status=media.status,
            source_content_sha256=media.content_sha256,
            analysis_text_characters=len(analysis_text),
            analysis_text_truncated=truncated,
        )
    raise EvidenceRevisionError("unsupported evidence source type")  # pragma: no cover - input is validated above.


def _copy_snapshot(parent: ForecastSnapshot) -> ForecastSnapshot:
    return ForecastSnapshot(
        id=uuid4(),
        symbol=parent.symbol,
        feature_trading_date=parent.feature_trading_date,
        feature_as_of_time=parent.feature_as_of_time,
        model_version=parent.model_version,
        model_sha256=parent.model_sha256,
        model_manifest_sha256=parent.model_manifest_sha256,
        feature_export_sha256=parent.feature_export_sha256,
        feature_version=parent.feature_version,
        feature_source=parent.feature_source,
        feature_snapshot_mode=parent.feature_snapshot_mode,
        feature_values=dict(parent.feature_values),
        bearish_probability=parent.bearish_probability,
        neutral_probability=parent.neutral_probability,
        bullish_probability=parent.bullish_probability,
    )


def _selected_event(projected: dict[str, list[dict]]) -> dict:
    events = projected.get("events")
    if not isinstance(events, list) or not events:
        raise EvidenceRevisionError("evidence extraction found no material source-grounded event")
    event = events[0]
    if not isinstance(event, dict):  # pragma: no cover - projection owns this contract.
        raise EvidenceRevisionError("evidence extraction result is invalid")
    return event


def _filing_available_at(filing: SecFilingInventory) -> datetime:
    value = filing.accepted_at
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
        if len(value) == 14 and value.isdigit():
            try:
                return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            except ValueError:
                pass
    raise EvidenceRevisionError("official filing requires an exact SEC acceptance timestamp")


def _https_hostname(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise EvidenceRevisionError("evidence source_url must be an absolute https URL")
    return parsed.hostname


def _uuid(value: UUID | str, name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise EvidenceRevisionError(f"{name} must be a valid UUID") from exc


def _choice(value: str, allowed: frozenset[str], name: str) -> str:
    if value not in allowed:
        raise EvidenceRevisionError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return value


def _as_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceRevisionError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def _probabilities(snapshot: ForecastSnapshot) -> dict[str, float]:
    return {
        "bearish": snapshot.bearish_probability,
        "neutral": snapshot.neutral_probability,
        "bullish": snapshot.bullish_probability,
    }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _analysis_text(value: str) -> tuple[str, bool]:
    return value[:MAX_ANALYSIS_CHARACTERS], len(value) > MAX_ANALYSIS_CHARACTERS
