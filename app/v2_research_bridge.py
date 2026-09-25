"""Bridge a frozen V2 evidence context into the source-grounded research flow.

This module converts frozen evidence into the existing cached extraction and
research flow.  It does not fetch a URL, read a mutable legacy row, or
manufacture facts.  The only text passed onward is the bounded
``analysis_text`` already frozen in an ``EvidenceContext`` snapshot.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .event_extraction import (
    DEFAULT_DEEPSEEK_MODEL as DEFAULT_EVENT_MODEL,
    PROMPT_VERSION as EVENT_PROMPT_VERSION,
    DocumentInput,
    EventExtractionError,
    EventProvider,
    extract_document,
    project_events_for_review,
)
from .event_provider import EventProviderError
from .evidence_context import EvidenceContext, FrozenEvidenceEvent
from .research_workflow import FrozenResearchSource, ResearchRun, run_frozen_research


class V2ResearchBridgeError(ValueError):
    """A frozen context cannot safely be converted into research input."""


@dataclass(frozen=True)
class EventExtractionOutcome:
    """One bounded extraction attempt, including cache and coverage evidence."""

    source_id: str
    status: str
    cache_key: str | None
    cache_hit: bool | None
    provider_calls: int
    coverage_incomplete: bool
    error: str | None = None


@dataclass(frozen=True)
class PreparedContextResearch:
    """Validated research sources plus explicit event-extraction outcomes."""

    sources: tuple[FrozenResearchSource, ...]
    outcomes: tuple[EventExtractionOutcome, ...]
    coverage_incomplete: bool

    @property
    def cache_hit_count(self) -> int:
        return sum(outcome.cache_hit is True for outcome in self.outcomes)

    @property
    def provider_call_count(self) -> int:
        return sum(outcome.provider_calls for outcome in self.outcomes)


def frozen_research_sources(context: EvidenceContext) -> list[FrozenResearchSource]:
    """Convert one frozen context into validated, no-retrieval research input."""

    if context.mode not in {"observed", "historical_research"}:
        raise V2ResearchBridgeError("V2 research context mode is invalid")
    if not context.events:
        raise V2ResearchBridgeError("V2 research requires at least one frozen evidence event")

    sources = [_convert_event(event, context) for event in context.events]
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise V2ResearchBridgeError("frozen evidence event IDs must not repeat")
    return sources


def run_context_research(
    *,
    context: EvidenceContext,
    db: Session,
    provider_factory: Callable[[], EventProvider],
    event_provider_factory: Callable[[], EventProvider],
    model: str,
    event_model: str = DEFAULT_EVENT_MODEL,
) -> ResearchRun:
    """Run the existing fixed positive/counter workflow over a frozen context.

    ``provider_factory`` is deliberately required.  Tests pass a fixture
    provider, while a later worker may opt in to a configured provider after
    this boundary has completed all local validation.
    """

    prepared = prepare_context_research(
        context=context,
        db=db,
        event_provider_factory=event_provider_factory,
        event_model=event_model,
    )
    if not prepared.sources:
        raise V2ResearchBridgeError("no frozen source passed event extraction validation")
    run = run_frozen_research(
        symbol=context.symbol,
        decision_at=context.decision_at,
        sources=prepared.sources,
        db=db,
        provider_factory=provider_factory,
        model=model,
        time_mode=context.mode,
    )
    if run.report is not None:
        run.report = {
            **run.report,
            "event_extraction": _preparation_snapshot(prepared),
        }
        db.commit()
        db.refresh(run)
    return run


def prepare_context_research(
    *,
    context: EvidenceContext,
    db: Session,
    event_provider_factory: Callable[[], EventProvider],
    event_model: str = DEFAULT_EVENT_MODEL,
) -> PreparedContextResearch:
    """Attach cached, quote-validated event anchors to frozen research sources.

    ``extract_document`` performs its cache lookup before invoking the supplied
    factory.  The per-source wrapped factory makes actual provider construction
    visible without performing any network access in this bridge itself.
    A failed extraction is represented in the returned outcomes and omitted
    from research input; it never becomes invented financial data.
    """

    base_sources = frozen_research_sources(context)
    prepared_sources: list[FrozenResearchSource] = []
    outcomes: list[EventExtractionOutcome] = []
    for source in base_sources:
        provider_calls = 0

        def counted_provider_factory() -> EventProvider:
            nonlocal provider_calls
            provider_calls += 1
            return event_provider_factory()

        try:
            extraction = extract_document(
                source.document,
                db=db,
                provider_factory=counted_provider_factory,
                model=event_model,
            )
            projected = _with_quote_anchors(project_events_for_review(extraction.batch), source.document)
            coverage_incomplete = source.coverage_incomplete
            provenance = {
                **source.provenance,
                "event_extraction": {
                    "cache_key": extraction.cache_key,
                    "cache_hit": extraction.cache_hit,
                    "provider_calls": provider_calls,
                    "prompt_version": EVENT_PROMPT_VERSION,
                    "event_count": len(projected["events"]),
                    "excluded_event_count": len(projected["excluded_events"]),
                    "analysis_text_sha256": source.document.sha256,
                    "impact_direction_status": "model_generated_review_required",
                },
            }
            prepared_sources.append(source.model_copy(update={"events": projected, "provenance": provenance}))
            outcomes.append(
                EventExtractionOutcome(
                    source_id=source.source_id,
                    status="succeeded",
                    cache_key=extraction.cache_key,
                    cache_hit=extraction.cache_hit,
                    provider_calls=provider_calls,
                    coverage_incomplete=coverage_incomplete,
                )
            )
        except (EventExtractionError, EventProviderError) as exc:
            outcomes.append(
                EventExtractionOutcome(
                    source_id=source.source_id,
                    status="failed",
                    cache_key=None,
                    cache_hit=None,
                    provider_calls=provider_calls,
                    coverage_incomplete=True,
                    error=str(exc),
                )
            )
    return PreparedContextResearch(
        sources=tuple(prepared_sources),
        outcomes=tuple(outcomes),
        coverage_incomplete=context.coverage_incomplete or any(outcome.coverage_incomplete for outcome in outcomes),
    )


def _convert_event(event: FrozenEvidenceEvent, context: EvidenceContext) -> FrozenResearchSource:
    if event.source_snapshot.get("symbol") != context.symbol or event.source_snapshot.get("symbol") != event.event_key.split(":", 1)[0]:
        raise V2ResearchBridgeError(f"{event.id}: frozen source symbol does not match its context")
    if event.published_at > context.decision_at:
        raise V2ResearchBridgeError(f"{event.id}: frozen source is after the context decision_at")
    if context.mode == "observed" and event.observed_at > context.decision_at:
        raise V2ResearchBridgeError(f"{event.id}: frozen source is after the context decision_at")

    snapshot = event.source_snapshot
    content_hash = _required_string(snapshot.get("content_sha256"), "content_sha256", event)
    if content_hash != event.content_sha256:
        raise V2ResearchBridgeError(f"{event.id}: snapshot content hash does not match the event")
    text = _required_string(snapshot.get("analysis_text"), "analysis_text", event)
    declared_length = snapshot.get("analysis_text_characters")
    if declared_length is not None and declared_length != len(text):
        raise V2ResearchBridgeError(f"{event.id}: frozen analysis text length does not match its snapshot")
    _validate_frozen_text_prefix(snapshot, event, text)

    source_url = _required_string(snapshot.get("source_url"), "source_url", event)
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise V2ResearchBridgeError(f"{event.id}: frozen source URL must be an absolute HTTPS URL")
    published_at = _snapshot_time(snapshot, "published_at", event)
    observed_at = _snapshot_time(snapshot, "observed_at", event)
    if published_at != event.published_at or observed_at != event.observed_at:
        raise V2ResearchBridgeError(f"{event.id}: snapshot timestamps do not match the frozen event")

    title = _title(snapshot, event)
    document = DocumentInput(
        document_id=f"v2-event-{event.id}",
        company=str(snapshot.get("company") or context.symbol),
        ticker=context.symbol,
        source_url=source_url,
        source_domain=parsed.hostname,
        published_date=published_at.date(),
        title=title,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    coverage_incomplete = bool(
        context.coverage_incomplete
        or snapshot.get("analysis_text_truncated")
        or snapshot.get("content_truncated")
    )
    provenance = _provenance(snapshot, event, context.coverage_incomplete)
    provenance["research_time_mode"] = context.mode
    provenance["historical_backfill"] = context.mode == "historical_research"
    provenance["observed_after_decision"] = event.observed_at > context.decision_at
    return FrozenResearchSource(
        source_id=str(event.id),
        source_type=event.source_type,
        source_record_id=str(event.source_id),
        document=document,
        published_at=published_at,
        observed_at=observed_at,
        provenance=provenance,
        coverage_incomplete=coverage_incomplete,
        # The evidence context has not yet run a V2 event extractor.  Passing
        # an empty list prevents unsupported financial facts from becoming
        # prompt context; later work can attach cached, quote-validated events.
        events={"events": [], "excluded_events": []},
    )


def _required_string(value: object, field: str, event: FrozenEvidenceEvent) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V2ResearchBridgeError(f"{event.id}: frozen snapshot is missing {field}")
    return value


def _validate_frozen_text_prefix(snapshot: dict[str, Any], event: FrozenEvidenceEvent, analysis_text: str) -> None:
    """Prove the bounded analysis text is an unchanged prefix of frozen content."""

    if event.source_type == "official_filing":
        full_text = snapshot.get("content_excerpt")
        expected_hash = event.content_sha256
        hash_field = "event content_sha256"
    else:
        full_text = snapshot.get("content_text")
        expected_hash = snapshot.get("content_text_sha256")
        hash_field = "content_text_sha256"
    if not isinstance(full_text, str) or not full_text:
        raise V2ResearchBridgeError(f"{event.id}: frozen snapshot is missing full source text for hash validation")
    if not isinstance(expected_hash, str) or hashlib.sha256(full_text.encode("utf-8")).hexdigest() != expected_hash:
        raise V2ResearchBridgeError(f"{event.id}: frozen full text hash does not match {hash_field}")
    if not full_text.startswith(analysis_text):
        raise V2ResearchBridgeError(f"{event.id}: frozen analysis text is not a prefix of the full source text")
    truncated = bool(snapshot.get("analysis_text_truncated"))
    if not truncated and analysis_text != full_text:
        raise V2ResearchBridgeError(f"{event.id}: complete analysis text does not match the frozen full source text")


def _snapshot_time(snapshot: dict[str, Any], field: str, event: FrozenEvidenceEvent) -> datetime:
    value = snapshot.get(field)
    if not isinstance(value, str):
        raise V2ResearchBridgeError(f"{event.id}: frozen snapshot is missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V2ResearchBridgeError(f"{event.id}: frozen snapshot has an invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V2ResearchBridgeError(f"{event.id}: frozen snapshot {field} must include a timezone")
    return parsed.astimezone(event.published_at.tzinfo)


def _title(snapshot: dict[str, Any], event: FrozenEvidenceEvent) -> str:
    title = snapshot.get("title")
    if isinstance(title, str) and title.strip():
        return title
    form = snapshot.get("form")
    accession = snapshot.get("accession_number")
    if isinstance(form, str) and form.strip() and isinstance(accession, str) and accession.strip():
        return f"{form} filing {accession}"
    return f"Frozen {event.source_type} evidence"


def _provenance(
    snapshot: dict[str, Any], event: FrozenEvidenceEvent, context_coverage_incomplete: bool
) -> dict[str, Any]:
    # Retain identifiers and coverage metadata, but avoid storing a second
    # copy of the source text in the research-run provenance object.
    metadata = {
        key: value
        for key, value in snapshot.items()
        if key not in {"analysis_text", "content_text", "content_excerpt"}
    }
    return {
        "event_id": str(event.id),
        "event_key": event.event_key,
        "content_sha256": event.content_sha256,
        "review_status": event.review_status,
        "discovery_kind": event.discovery_kind,
        "context_coverage_incomplete": context_coverage_incomplete,
        "frozen_snapshot": metadata,
    }


def _preparation_snapshot(prepared: PreparedContextResearch) -> dict[str, Any]:
    """Keep cache/call/failure information visible in a successful report."""

    return {
        "cache_hit_count": prepared.cache_hit_count,
        "provider_call_count": prepared.provider_call_count,
        "coverage_incomplete": prepared.coverage_incomplete,
        "outcomes": [
            {
                "source_id": outcome.source_id,
                "status": outcome.status,
                "cache_key": outcome.cache_key,
                "cache_hit": outcome.cache_hit,
                "provider_calls": outcome.provider_calls,
                "coverage_incomplete": outcome.coverage_incomplete,
                "error": outcome.error,
            }
            for outcome in prepared.outcomes
        ],
        "limitation": (
            "Event directions are model-generated review anchors. They are not verified causal claims, "
            "financial facts, or forecast probabilities."
        ),
    }


def _with_quote_anchors(
    projected: dict[str, list[dict[str, Any]]], document: DocumentInput
) -> dict[str, list[dict[str, Any]]]:
    """Retain each extracted quote's exact position in the frozen analysis text."""

    anchored_events: list[dict[str, Any]] = []
    for event in projected["events"]:
        quote = event["evidence_quote"]
        start = document.text.index(quote)
        anchored_events.append(
            {
                **event,
                "quote_start": start,
                "quote_end": start + len(quote),
                "analysis_text_sha256": document.sha256,
            }
        )
    return {"events": anchored_events, "excluded_events": projected["excluded_events"]}
