"""Freeze eligible SEC and uploaded-media sources into V2 evidence versions.

This is deliberately a source/context layer.  It never extracts facts, calls
an LLM, or changes a model probability.  Later P2 work can consume the frozen
text and citations without rereading mutable legacy inventory rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .forecast_v2_models import EvidenceEventVersionV2
from .models import SecFilingInventory, UploadedEvidence
from .services import normalize_symbol


CONTEXT_SCHEMA_VERSION = "evidence-context-v1"
MAX_NEW_DOCUMENTS = 10
MAX_ANALYSIS_CHARACTERS = 24_000
ContextMode = Literal["observed", "historical_research"]
SourceType = Literal["official_filing", "uploaded_media"]


class EvidenceContextError(ValueError):
    """A safe, point-in-time or source-integrity error."""

    def __init__(self, message: str, *, code: str = "invalid_evidence_context") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FrozenEvidenceEvent:
    id: UUID
    event_key: str
    source_type: SourceType
    source_id: UUID
    content_sha256: str
    published_at: datetime
    observed_at: datetime
    review_status: str
    user_rating_stars: int | None
    is_new: bool
    discovery_kind: Literal["initial", "new_publication", "backfill_discovered", "inherited", "state_changed"]
    source_snapshot: dict[str, Any]

    # P2 context has frozen documents but no fact extractor result yet.  These
    # stable defaults let feature construction distinguish missing information
    # from a fabricated zero/neutral fact.
    facts: tuple[dict[str, Any], ...] = ()
    guidance: Literal["raised", "lowered", "maintained", "unknown"] = "unknown"
    operational_event_type: str | None = None
    operational_event_status: Literal["none", "unknown"] = "unknown"
    is_active: bool = True
    is_corrected_or_withdrawn: bool = False

    @property
    def content_hash(self) -> str:
        return self.content_sha256

    @property
    def public_at(self) -> datetime:
        return self.published_at

    @property
    def user_stars(self) -> int | None:
        return self.user_rating_stars

    @property
    def independent_source_key(self) -> str:
        return f"{self.source_type}:{self.source_id}"

    @property
    def source_url(self) -> str:
        return str(self.source_snapshot["source_url"])

    @property
    def frozen_text(self) -> str:
        return str(self.source_snapshot["analysis_text"])

    def as_manifest_item(self) -> dict[str, Any]:
        # A forecast references the immutable event-version row.  Its frozen
        # document stays in that row, rather than being copied into every
        # forecast manifest.
        snapshot = self.source_snapshot
        return {
            "event_version_id": str(self.id),
            "id": str(self.id),
            "event_key": self.event_key,
            "source_type": self.source_type,
            "source_id": str(self.source_id),
            "content_sha256": self.content_sha256,
            "content_hash": self.content_sha256,
            "published_at": self.published_at.isoformat(),
            "public_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "review_status": self.review_status,
            "user_rating_stars": self.user_rating_stars,
            "user_stars": self.user_rating_stars,
            "is_new": self.is_new,
            "discovery_kind": self.discovery_kind,
            "is_active": self.is_active,
            "is_corrected_or_withdrawn": self.is_corrected_or_withdrawn,
            "independent_source_key": self.independent_source_key,
            "source_url": snapshot.get("source_url"),
            "coverage": snapshot.get("coverage"),
            "coverage_incomplete": snapshot.get("coverage_incomplete"),
            "analysis_text_characters": snapshot.get("analysis_text_characters"),
            "analysis_text_truncated": snapshot.get("analysis_text_truncated"),
            "content_truncated": snapshot.get("content_truncated"),
            "content_text_sha256": snapshot.get("content_text_sha256"),
        }


@dataclass(frozen=True)
class EvidenceContext:
    symbol: str
    decision_at: datetime
    mode: ContextMode
    events: tuple[FrozenEvidenceEvent, ...]
    coverage_incomplete: bool
    omitted_source_refs: tuple[dict[str, str], ...]

    def evidence_manifest(self) -> list[dict[str, Any]]:
        return [event.as_manifest_item() for event in self.events]


@dataclass(frozen=True)
class _Candidate:
    source_type: SourceType
    source_id: UUID
    symbol: str
    event_key: str
    content_sha256: str
    source_snapshot: dict[str, Any]
    published_at: datetime
    observed_at: datetime
    review_status: str
    review_snapshot: dict[str, Any]
    user_rating_stars: int | None
    explicit: bool = False


def freeze_evidence_context(
    *,
    db: Session,
    symbol: str,
    decision_at: datetime,
    mode: ContextMode = "observed",
    previous_event_ids: Sequence[UUID | str] = (),
    previous_decision_at: datetime | None = None,
    source_refs: Sequence[Mapping[str, object]] = (),
    max_new_documents: int = MAX_NEW_DOCUMENTS,
) -> EvidenceContext:
    """Create/reuse immutable V2 source rows and select one point-in-time set.

    In ``observed`` mode both the public and system-observed timestamps must
    be no later than ``decision_at``.  ``historical_research`` only relaxes
    the observed-time condition and retains that fact in every source snapshot;
    it must never be presented as a contemporaneous observed decision.
    """
    normalized = _supported_symbol(symbol)
    cutoff = _aware_utc(decision_at, "decision_at")
    if mode not in {"observed", "historical_research"}:
        raise EvidenceContextError("mode must be observed or historical_research")
    if max_new_documents < 1 or max_new_documents > MAX_NEW_DOCUMENTS:
        raise EvidenceContextError(f"max_new_documents must be between 1 and {MAX_NEW_DOCUMENTS}")
    prior_cutoff = _aware_utc(previous_decision_at, "previous_decision_at") if previous_decision_at else None

    try:
        prior = _load_prior_events(db, previous_event_ids, normalized)
        raw_candidates, automatic_omissions = _resolve_candidates(
            db,
            symbol=normalized,
            decision_at=cutoff,
            mode=mode,
            source_refs=source_refs,
        )
        candidates = _deduplicate_candidates(raw_candidates)
        candidate_by_key = {candidate.event_key: candidate for candidate in candidates}

        selected: list[FrozenEvidenceEvent] = []
        prior_keys = set(prior)
        # Preserve an older immutable snapshot only while its current source
        # state has not changed.  A content/review change appends a new row and
        # replaces the inherited state for future contexts.
        for event_key, old in sorted(prior.items()):
            candidate = candidate_by_key.pop(event_key, None)
            if candidate is None:
                selected.append(_frozen_from_row(old, is_new=False, discovery_kind="inherited"))
                continue
            current, created = _get_or_create_event(db, candidate)
            if _same_event_state(old, current):
                selected.append(_frozen_from_row(old, is_new=False, discovery_kind="inherited"))
            else:
                selected.append(_frozen_from_row(current, is_new=True, discovery_kind="state_changed"))

        # A later upload/repost with byte-identical content is supporting
        # provenance, not another event feature.  Keep the already frozen
        # event in a revision context so repeated coverage cannot accumulate.
        prior_content_hashes = {row.content_sha256 for row in prior.values()}
        new_candidates = [
            candidate for candidate in candidate_by_key.values() if candidate.content_sha256 not in prior_content_hashes
        ]
        explicit_new = [candidate for candidate in new_candidates if candidate.explicit]
        if len(explicit_new) > max_new_documents:
            raise EvidenceContextError(
                f"at most {max_new_documents} newly selected source_refs are supported",
                code="explicit_source_limit",
            )
        automatic_new = [candidate for candidate in new_candidates if not candidate.explicit]
        if not prior:
            automatic_new = _initial_candidates(automatic_new, decision_at=cutoff)
        explicit_new.sort(key=_candidate_sort_key, reverse=True)
        automatic_new.sort(key=_candidate_sort_key, reverse=True)
        selected_new = [*explicit_new, *automatic_new]
        omitted_automatic = [_source_ref(candidate) for candidate in selected_new[max_new_documents:] if not candidate.explicit]
        omitted = tuple([*automatic_omissions, *omitted_automatic])
        for candidate in selected_new[:max_new_documents]:
            current, _ = _get_or_create_event(db, candidate)
            selected.append(
                _frozen_from_row(
                    current,
                    is_new=True,
                    discovery_kind=_discovery_kind(candidate, prior_cutoff, has_prior=bool(prior_keys)),
                )
            )
        coverage_incomplete = (
            bool(automatic_omissions)
            or bool(omitted_automatic)
            or any(bool(event.source_snapshot.get("coverage_incomplete")) for event in selected)
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise EvidenceContextError("evidence context could not be stored", code="storage_conflict") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise EvidenceContextError("evidence context store is unavailable", code="storage_unavailable") from exc
    except Exception:
        db.rollback()
        raise

    selected.sort(key=lambda event: (event.published_at, event.observed_at, event.event_key, str(event.id)))
    return EvidenceContext(
        symbol=normalized,
        decision_at=cutoff,
        mode=mode,
        events=tuple(selected),
        coverage_incomplete=coverage_incomplete,
        omitted_source_refs=omitted,
    )


def _load_prior_events(
    db: Session, event_ids: Sequence[UUID | str], symbol: str
) -> dict[str, EvidenceEventVersionV2]:
    prior: dict[str, EvidenceEventVersionV2] = {}
    for raw_id in event_ids:
        event_id = _uuid(raw_id, "previous_event_id")
        row = db.get(EvidenceEventVersionV2, event_id)
        if row is None:
            raise EvidenceContextError("previous evidence event was not found", code="not_found")
        if row.symbol != symbol:
            raise EvidenceContextError("previous evidence event belongs to another symbol", code="cross_symbol")
        existing = prior.get(row.event_key)
        if existing is None or _event_sort_key(row) > _event_sort_key(existing):
            prior[row.event_key] = row
    return prior


def _resolve_candidates(
    db: Session,
    *,
    symbol: str,
    decision_at: datetime,
    mode: ContextMode,
    source_refs: Sequence[Mapping[str, object]],
) -> tuple[list[_Candidate], list[dict[str, str]]]:
    refs = _normalise_refs(source_refs)
    filings = db.scalars(
        select(SecFilingInventory).where(SecFilingInventory.symbol == symbol)
    ).all()
    uploads = db.scalars(
        select(UploadedEvidence).where(UploadedEvidence.symbol == symbol)
    ).all()
    candidates: list[_Candidate] = []
    omissions: list[dict[str, str]] = []
    # Discovery inventories can legitimately contain unfetched, malformed,
    # or time-ambiguous rows.  For automatic selection these do not erase the
    # usable evidence set; they remain visible as incomplete coverage.  Manual
    # refs below are deliberately resolved a second time and fail closed.
    for source_type, rows, builder in (
        ("official_filing", filings, _candidate_from_filing),
        ("uploaded_media", uploads, _candidate_from_upload),
    ):
        for row in rows:
            try:
                candidates.append(builder(row, decision_at=decision_at))
            except EvidenceContextError as exc:
                if exc.code in {"missing_official_content", "content_hash_mismatch", "ambiguous_official_time"}:
                    omissions.append({"source_type": source_type, "source_id": str(row.id)})
                    continue
                raise
    for ref in refs:
        candidates.append(
            replace(
                _candidate_for_ref(db, ref, expected_symbol=symbol, decision_at=decision_at),
                explicit=True,
            )
        )

    eligible: list[_Candidate] = []
    for candidate in candidates:
        _validate_candidate_symbol(candidate, symbol)
        if candidate.published_at > decision_at:
            if candidate.explicit:
                raise EvidenceContextError("source is published after decision_at", code="future_source")
            continue
        if mode == "observed" and candidate.observed_at > decision_at:
            if candidate.explicit:
                raise EvidenceContextError("source was observed after decision_at", code="future_source")
            continue
        eligible.append(candidate)
    return eligible, omissions


def _candidate_for_ref(
    db: Session, ref: dict[str, UUID], *, expected_symbol: str, decision_at: datetime
) -> _Candidate:
    if ref["source_type"] == "official_filing":
        row = db.get(SecFilingInventory, ref["source_id"])
        if row is None:
            raise EvidenceContextError("official filing source was not found", code="not_found")
        candidate = _candidate_from_filing(row, decision_at=decision_at)
    else:
        row = db.get(UploadedEvidence, ref["source_id"])
        if row is None:
            raise EvidenceContextError("uploaded media source was not found", code="not_found")
        candidate = _candidate_from_upload(row, decision_at=decision_at)
    _validate_candidate_symbol(candidate, expected_symbol)
    return candidate


def _candidate_from_filing(row: SecFilingInventory, *, decision_at: datetime) -> _Candidate:
    published_at = _exact_sec_acceptance(row)
    if row.content_status != "fetched" or not row.content_excerpt or not row.content_excerpt_sha256:
        raise EvidenceContextError(
            "official filing has no frozen fetched content excerpt",
            code="missing_official_content",
        )
    observed_at = _aware_utc(row.content_observed_at or row.observed_at, "official filing observed_at")
    content = row.content_excerpt
    computed_hash = _hash_text(content)
    if computed_hash != row.content_excerpt_sha256:
        raise EvidenceContextError("official filing excerpt hash does not match its saved content", code="content_hash_mismatch")
    review_status, review_snapshot = _filing_review_at(row, decision_at=decision_at)
    content_source_url = row.content_source_url or row.source_url
    content_document_name = row.content_document_name or row.primary_document
    content_kind = row.content_kind or "primary_document"
    attachment_status = row.related_attachment_status or ("unknown" if row.form == "8-K" else "not_applicable")
    attachment_incomplete = row.form == "8-K" and attachment_status in {"unknown", "not_checked", "unavailable"}
    analysis_truncated = len(content) > MAX_ANALYSIS_CHARACTERS
    content_incomplete = bool(row.content_truncated) or analysis_truncated or attachment_incomplete
    if content_kind == "exhibit_99_1":
        coverage = "8k_related_exhibit"
    elif row.form == "8-K" and attachment_status == "not_found":
        coverage = "8k_primary_document_no_related_exhibit"
    elif row.form == "8-K":
        coverage = "8k_primary_document_attachment_coverage_incomplete"
    else:
        coverage = "excerpt_only" if row.content_truncated else "fetched_excerpt"
    analysis_end = min(len(content), MAX_ANALYSIS_CHARACTERS)
    return _Candidate(
        source_type="official_filing",
        source_id=row.id,
        symbol=row.symbol,
        event_key=f"{row.symbol}:official_filing:{row.accession_number}",
        content_sha256=row.content_excerpt_sha256,
        source_snapshot={
            "source_type": "official_filing",
            "source_id": str(row.id),
            "symbol": row.symbol,
            "accession_number": row.accession_number,
            "form": row.form,
            "source_url": content_source_url,
            "filing_source_url": row.source_url,
            "document_name": content_document_name,
            "document_kind": content_kind,
            "related_attachment_status": attachment_status,
            "related_attachment_error": row.related_attachment_error,
            "publication_time_basis": "sec_accession_acceptance_time",
            "published_at": published_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "content_excerpt": content,
            "analysis_text": content[:MAX_ANALYSIS_CHARACTERS],
            "analysis_text_characters": analysis_end,
            "analysis_text_truncated": analysis_truncated,
            "analysis_locator": {
                "kind": "extracted_text_char_range",
                "start": 0,
                "end": analysis_end,
            },
            "content_truncated": row.content_truncated,
            "content_sha256": row.content_excerpt_sha256,
            "content_text_sha256": row.content_excerpt_sha256,
            "coverage": coverage,
            "coverage_incomplete": content_incomplete,
        },
        published_at=published_at,
        observed_at=observed_at,
        review_status=review_status,
        review_snapshot=review_snapshot,
        user_rating_stars=None,
    )


def _candidate_from_upload(row: UploadedEvidence, *, decision_at: datetime) -> _Candidate:
    published_at = _aware_utc(row.published_at, "uploaded media published_at")
    observed_at = _aware_utc(row.observed_at, "uploaded media observed_at")
    if not row.content_text or _hash_bytes(row.raw_content) != row.content_sha256:
        raise EvidenceContextError("uploaded media content does not match its saved hash", code="content_hash_mismatch")
    # The legacy upload table only permits ``unconfirmed`` and has no review
    # history. Keep its user-supplied star assessment as source metadata, but
    # never invent a later human verification state for a historical context.
    content_text_sha256 = _hash_text(row.content_text)
    review_snapshot = {
        "status": "unconfirmed",
        "credibility_stars": row.credibility_stars,
        "credibility_reason": row.credibility_reason,
        "impact_severity": row.impact_severity,
        # An upload hashes raw bytes.  Include the separately extracted text
        # hash in the version fingerprint, so a later extraction cannot reuse
        # a stale frozen document merely because the PDF bytes match.
        "content_text_sha256": content_text_sha256,
    }
    return _Candidate(
        source_type="uploaded_media",
        source_id=row.id,
        symbol=row.symbol,
        # Matching uploaded bytes are one possible re-post, regardless of the
        # filename or user-selected URL.  Do not count it more than once.
        event_key=f"{row.symbol}:uploaded_media:{row.content_sha256}",
        content_sha256=row.content_sha256,
        source_snapshot={
            "source_type": "uploaded_media",
            "source_id": str(row.id),
            "symbol": row.symbol,
            "title": row.title,
            "source_url": row.source_url,
            "filename": row.filename,
            "published_at": published_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "content_text": row.content_text,
            "analysis_text": row.content_text[:MAX_ANALYSIS_CHARACTERS],
            "analysis_text_characters": min(len(row.content_text), MAX_ANALYSIS_CHARACTERS),
            "analysis_text_truncated": len(row.content_text) > MAX_ANALYSIS_CHARACTERS,
            "analysis_locator": {
                "kind": "extracted_text_char_range",
                "start": 0,
                "end": min(len(row.content_text), MAX_ANALYSIS_CHARACTERS),
            },
            "content_sha256": row.content_sha256,
            "content_hash_basis": "raw_bytes",
            "raw_content_sha256": row.content_sha256,
            "content_text_sha256": content_text_sha256,
            "coverage": "uploaded_text",
            "coverage_incomplete": len(row.content_text) > MAX_ANALYSIS_CHARACTERS,
        },
        published_at=published_at,
        observed_at=observed_at,
        review_status="pending_review",
        review_snapshot=review_snapshot,
        user_rating_stars=row.credibility_stars,
    )


def _filing_review_at(
    row: SecFilingInventory, *, decision_at: datetime
) -> tuple[str, dict[str, Any]]:
    """Return only a review state demonstrably known by the decision cutoff."""
    reviewed_at = _aware_utc(row.reviewed_at, "official filing reviewed_at") if row.reviewed_at else None
    if row.review_status == "pending_review":
        return "pending_review", {
            "review_status": "pending_review",
            "human_review_note": None,
            "reviewed_at": None,
            "temporally_available": True,
        }
    if reviewed_at is None or reviewed_at > decision_at:
        return "pending_review", {
            "review_status": "pending_review",
            "human_review_note": None,
            "reviewed_at": None,
            "temporally_available": False,
        }
    return row.review_status, {
        "review_status": row.review_status,
        "human_review_note": row.human_review_note,
        "reviewed_at": reviewed_at.isoformat(),
        "temporally_available": True,
    }


def _get_or_create_event(db: Session, candidate: _Candidate) -> tuple[EvidenceEventVersionV2, bool]:
    review_fingerprint = _digest(candidate.review_snapshot)
    existing = db.scalar(
        select(EvidenceEventVersionV2).where(
            EvidenceEventVersionV2.source_type == candidate.source_type,
            EvidenceEventVersionV2.source_id == candidate.source_id,
            EvidenceEventVersionV2.content_sha256 == candidate.content_sha256,
            EvidenceEventVersionV2.review_status == candidate.review_status,
            EvidenceEventVersionV2.extraction_schema_version == CONTEXT_SCHEMA_VERSION,
            EvidenceEventVersionV2.review_fingerprint == review_fingerprint,
        )
    )
    if existing is not None:
        return existing, False
    previous = db.scalar(
        select(EvidenceEventVersionV2)
        .where(
            EvidenceEventVersionV2.source_type == candidate.source_type,
            EvidenceEventVersionV2.source_id == candidate.source_id,
        )
        .order_by(EvidenceEventVersionV2.created_at.desc(), EvidenceEventVersionV2.id.desc())
        .limit(1)
    )
    cache_key = _digest(
        {
            "content_sha256": candidate.content_sha256,
            "review_fingerprint": review_fingerprint,
            "schema": CONTEXT_SCHEMA_VERSION,
        }
    )
    row = EvidenceEventVersionV2(
        symbol=candidate.symbol,
        source_type=candidate.source_type,
        source_id=candidate.source_id,
        source_snapshot={**candidate.source_snapshot, "context_mode": "frozen"},
        content_sha256=candidate.content_sha256,
        published_at=candidate.published_at,
        observed_at=candidate.observed_at,
        extracted_at=None,
        event_key=candidate.event_key,
        previous_version_id=previous.id if previous else None,
        structured_facts=None,
        citations=[],
        user_rating_stars=candidate.user_rating_stars,
        review_status=candidate.review_status,
        review_snapshot=candidate.review_snapshot,
        review_fingerprint=review_fingerprint,
        extraction_schema_version=CONTEXT_SCHEMA_VERSION,
        extraction_cache_key=cache_key,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(EvidenceEventVersionV2).where(
                EvidenceEventVersionV2.source_type == candidate.source_type,
                EvidenceEventVersionV2.source_id == candidate.source_id,
                EvidenceEventVersionV2.content_sha256 == candidate.content_sha256,
                EvidenceEventVersionV2.review_status == candidate.review_status,
                EvidenceEventVersionV2.extraction_schema_version == CONTEXT_SCHEMA_VERSION,
                EvidenceEventVersionV2.review_fingerprint == review_fingerprint,
            )
        )
        if existing is None:
            raise
        return existing, False
    return row, True


def _deduplicate_candidates(candidates: Sequence[_Candidate]) -> list[_Candidate]:
    selected: dict[str, _Candidate] = {}
    for candidate in sorted(candidates, key=_candidate_sort_key):
        # Event key controls state replacement; same body across media uploads
        # is also one report for current feature construction.
        dedup_key = f"{candidate.symbol}:{candidate.content_sha256}"
        current = selected.get(dedup_key)
        if (
            current is None
            or (candidate.explicit and not current.explicit)
            or (
                candidate.explicit == current.explicit
                and _candidate_sort_key(candidate) > _candidate_sort_key(current)
            )
        ):
            selected[dedup_key] = candidate
    return list(selected.values())


def _initial_candidates(candidates: Sequence[_Candidate], *, decision_at: datetime) -> list[_Candidate]:
    """Pick the initial background deliberately instead of taking oldest rows.

    The first context carries the newest available annual report, quarterly
    report, and all other eligible candidates from the preceding 90 days.  Old
    miscellaneous filing inventory does not silently consume the ten-document
    budget.
    """
    latest_reports: list[_Candidate] = []
    for form in ("10-K", "10-Q"):
        matching = [
            candidate
            for candidate in candidates
            if candidate.source_type == "official_filing" and candidate.source_snapshot.get("form") == form
        ]
        if matching:
            latest_reports.append(max(matching, key=_candidate_sort_key))
    recent_cutoff = decision_at - timedelta(days=90)
    recent = [candidate for candidate in candidates if candidate.published_at >= recent_cutoff]
    selected: dict[tuple[str, UUID], _Candidate] = {
        (candidate.source_type, candidate.source_id): candidate for candidate in [*latest_reports, *recent]
    }
    return sorted(selected.values(), key=_candidate_sort_key, reverse=True)


def _same_event_state(old: EvidenceEventVersionV2, current: EvidenceEventVersionV2) -> bool:
    return (
        old.source_type == current.source_type
        and old.source_id == current.source_id
        and old.content_sha256 == current.content_sha256
        and old.review_fingerprint == current.review_fingerprint
        and old.extraction_schema_version == current.extraction_schema_version
    )


def _discovery_kind(
    candidate: _Candidate, previous_decision_at: datetime | None, *, has_prior: bool
) -> Literal["initial", "new_publication", "backfill_discovered"]:
    if not has_prior:
        return "initial"
    if previous_decision_at is not None and candidate.published_at > previous_decision_at:
        return "new_publication"
    return "backfill_discovered"


def _frozen_from_row(
    row: EvidenceEventVersionV2,
    *,
    is_new: bool,
    discovery_kind: Literal["initial", "new_publication", "backfill_discovered", "inherited", "state_changed"],
) -> FrozenEvidenceEvent:
    return FrozenEvidenceEvent(
        id=row.id,
        event_key=row.event_key,
        source_type=row.source_type,  # type: ignore[arg-type]
        source_id=row.source_id,
        content_sha256=row.content_sha256,
        published_at=_aware_utc(row.published_at, "event published_at"),
        observed_at=_aware_utc(row.observed_at, "event observed_at"),
        review_status=row.review_status,
        user_rating_stars=row.user_rating_stars,
        is_new=is_new,
        discovery_kind=discovery_kind,
        source_snapshot=dict(row.source_snapshot),
        facts=tuple(row.structured_facts.get("facts", ()) if isinstance(row.structured_facts, dict) else ()),
        guidance=(
            row.structured_facts.get("guidance", "unknown")
            if isinstance(row.structured_facts, dict)
            and row.structured_facts.get("guidance", "unknown") in {"raised", "lowered", "maintained", "unknown"}
            else "unknown"
        ),
        operational_event_type=(
            row.structured_facts.get("operational_event_type")
            if isinstance(row.structured_facts, dict) and isinstance(row.structured_facts.get("operational_event_type"), str)
            else None
        ),
        operational_event_status=(
            row.structured_facts.get("operational_event_status", "unknown")
            if isinstance(row.structured_facts, dict)
            and row.structured_facts.get("operational_event_status", "unknown") in {"none", "unknown"}
            else "unknown"
        ),
    )


def _normalise_refs(source_refs: Sequence[Mapping[str, object]]) -> list[dict[str, UUID]]:
    normalized: list[dict[str, UUID]] = []
    seen: set[tuple[str, UUID]] = set()
    for ref in source_refs:
        source_type = ref.get("source_type")
        if source_type not in {"official_filing", "uploaded_media"}:
            raise EvidenceContextError("source_type must be official_filing or uploaded_media")
        source_id = _uuid(ref.get("source_id"), "source_id")
        key = (source_type, source_id)
        if key in seen:
            raise EvidenceContextError("source_refs must not repeat a source")
        seen.add(key)
        normalized.append({"source_type": source_type, "source_id": source_id})
    return normalized


def _exact_sec_acceptance(row: SecFilingInventory) -> datetime:
    raw = row.accepted_at
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
    raise EvidenceContextError(
        "official filing requires an exact timezone-aware SEC acceptance timestamp",
        code="ambiguous_official_time",
    )


def _supported_symbol(value: str) -> str:
    normalized = normalize_symbol(value)
    if normalized not in {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"}:
        raise EvidenceContextError("symbol is not supported for V2 forecasting")
    return normalized


def _validate_candidate_symbol(candidate: _Candidate, symbol: str) -> None:
    if candidate.symbol != symbol:
        raise EvidenceContextError("source belongs to another symbol", code="cross_symbol")


def _candidate_sort_key(candidate: _Candidate) -> tuple[datetime, datetime, str, str]:
    return (candidate.published_at, candidate.observed_at, candidate.source_type, str(candidate.source_id))


def _event_sort_key(row: EvidenceEventVersionV2) -> tuple[datetime, str]:
    return (_aware_utc(row.created_at, "event created_at"), str(row.id))


def _source_ref(candidate: _Candidate) -> dict[str, str]:
    return {"source_type": candidate.source_type, "source_id": str(candidate.source_id)}


def _aware_utc(value: datetime | None, name: str) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceContextError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def _uuid(value: object, name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise EvidenceContextError(f"{name} must be a valid UUID") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _iso_or_none(value: datetime | None) -> str | None:
    return _aware_utc(value, "reviewed_at").isoformat() if value is not None else None
