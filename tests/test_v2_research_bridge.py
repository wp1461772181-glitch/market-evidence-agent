import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.database import Base, SessionLocal, engine
from app.evidence_context import EvidenceContext, FrozenEvidenceEvent
from app.event_provider import ProviderResult
from app.v2_research_bridge import (
    V2ResearchBridgeError,
    frozen_research_sources,
    prepare_context_research,
    run_context_research,
)


_TEXT = "Revenue increased 6% to $100 billion. Operating expenses rose by 4%."


@dataclass
class FixtureResearchProvider:
    responses: list[str]
    calls: int = 0

    def extract(self, **_: object) -> ProviderResult:
        self.calls += 1
        return ProviderResult(content=self.responses.pop(0), response_model="fixture", usage=None)


@dataclass
class FixtureExtractionProvider:
    response: str
    calls: int = 0

    def extract(self, **_: object) -> ProviderResult:
        self.calls += 1
        return ProviderResult(content=self.response, response_model="fixture-event", usage=None)


@pytest.fixture(autouse=True)
def tables(disposable_database):
    Base.metadata.create_all(bind=engine)


def _context(**snapshot_changes: object) -> EvidenceContext:
    event_id = uuid4()
    published_at = datetime(2026, 9, 10, 14, tzinfo=UTC)
    observed_at = datetime(2026, 9, 10, 15, tzinfo=UTC)
    snapshot: dict[str, object] = {
        "symbol": "AAPL",
        "title": "Quarterly results",
        "source_url": "https://www.sec.gov/Archives/example/q.htm",
        "published_at": published_at.isoformat(),
        "observed_at": observed_at.isoformat(),
        "analysis_text": _TEXT,
        "content_excerpt": _TEXT,
        "analysis_text_characters": len(_TEXT),
        "analysis_text_truncated": True,
        "content_truncated": False,
        "content_sha256": hashlib.sha256(_TEXT.encode()).hexdigest(),
        "coverage": "excerpt_only",
    }
    snapshot.update(snapshot_changes)
    event = FrozenEvidenceEvent(
        id=event_id,
        event_key=f"AAPL:official_filing:{event_id}",
        source_type="official_filing",
        source_id=uuid4(),
        content_sha256=hashlib.sha256(_TEXT.encode()).hexdigest(),
        published_at=published_at,
        observed_at=observed_at,
        review_status="accepted",
        user_rating_stars=None,
        is_new=True,
        discovery_kind="initial",
        source_snapshot=snapshot,
    )
    return EvidenceContext(
        symbol="AAPL",
        decision_at=datetime(2026, 9, 11, tzinfo=UTC),
        mode="observed",
        events=(event,),
        coverage_incomplete=False,
        omitted_source_refs=(),
    )


def _claim(source_id: str, claim: str, quote: str) -> str:
    return json.dumps({"claims": [{"claim": claim, "source_id": source_id, "evidence_quote": quote}]})


def _events(quote: str = "Revenue increased 6% to $100 billion.") -> str:
    return json.dumps(
        {
            "events": [
                {
                    "event_type": "earnings_release",
                    "event_date": "2026-09-10",
                    "impact_direction": "positive",
                    "summary": "Revenue was reported.",
                    "evidence_quote": quote,
                }
            ]
        }
    )


def test_bridge_converts_only_frozen_snapshot_and_preserves_coverage_metadata():
    context = _context()

    source = frozen_research_sources(context)[0]

    assert source.document.text == _TEXT
    assert source.document.sha256 == hashlib.sha256(_TEXT.encode()).hexdigest()
    assert source.document.source_url == "https://www.sec.gov/Archives/example/q.htm"
    assert source.coverage_incomplete is True
    assert source.provenance["content_sha256"] == context.events[0].content_sha256
    assert source.provenance["frozen_snapshot"]["coverage"] == "excerpt_only"
    assert source.events == {"events": [], "excluded_events": []}


@pytest.mark.parametrize(
    "snapshot_changes, message",
    [
        ({"content_sha256": "0" * 64}, "content hash"),
        ({"source_url": "http://www.sec.gov/Archives/example/q.htm"}, "HTTPS"),
        ({"observed_at": "2026-09-12T00:00:00+00:00"}, "timestamps"),
        ({"analysis_text": _TEXT.replace("Revenue", "revenuE")}, "prefix"),
    ],
)
def test_bridge_rejects_hash_url_and_timestamp_mismatch(snapshot_changes, message):
    with pytest.raises(V2ResearchBridgeError, match=message):
        frozen_research_sources(_context(**snapshot_changes))


def test_bridge_runs_the_existing_research_nodes_with_a_fixture_provider_only():
    context = _context()
    source_id = str(context.events[0].id)
    provider = FixtureResearchProvider(
        [
            _claim(source_id, "Revenue growth supports the business case.", "Revenue increased 6% to $100 billion."),
            _claim(source_id, "Higher expenses qualify the business case.", "Operating expenses rose by 4%."),
        ]
    )
    event_provider = FixtureExtractionProvider(_events())

    with SessionLocal() as db:
        run = run_context_research(
            context=context,
            db=db,
            provider_factory=lambda: provider,
            event_provider_factory=lambda: event_provider,
            model="fixture-model",
            event_model="fixture-event-model",
        )

    assert run.status == "succeeded"
    assert provider.calls == 2
    assert event_provider.calls == 1
    assert run.report is not None
    assert run.report["time_scope"]["data_mode"] == "observed"
    assert run.source_snapshot[0]["coverage_incomplete"] is True
    assert run.report["event_extraction"]["provider_call_count"] == 1
    assert run.report["event_extraction"]["outcomes"][0]["cache_hit"] is False


def test_bridge_marks_historical_backfill_and_does_not_present_it_as_observed():
    context = _context()
    late_observed_at = datetime(2026, 9, 12, tzinfo=UTC)
    original = context.events[0]
    historical_event = FrozenEvidenceEvent(
        id=original.id,
        event_key=original.event_key,
        source_type=original.source_type,
        source_id=original.source_id,
        content_sha256=original.content_sha256,
        published_at=original.published_at,
        observed_at=late_observed_at,
        review_status=original.review_status,
        user_rating_stars=original.user_rating_stars,
        is_new=original.is_new,
        discovery_kind="backfill_discovered",
        source_snapshot={**original.source_snapshot, "observed_at": late_observed_at.isoformat()},
    )
    historical = EvidenceContext(
        symbol=context.symbol,
        decision_at=context.decision_at,
        mode="historical_research",
        events=(historical_event,),
        coverage_incomplete=context.coverage_incomplete,
        omitted_source_refs=context.omitted_source_refs,
    )
    source = frozen_research_sources(historical)[0]
    assert source.provenance["historical_backfill"] is True
    assert source.provenance["observed_after_decision"] is True

    source_id = str(historical_event.id)
    research_provider = FixtureResearchProvider(
        [
            _claim(source_id, "Revenue supports the business case.", "Revenue increased 6% to $100 billion."),
            _claim(source_id, "Expenses qualify the business case.", "Operating expenses rose by 4%."),
        ]
    )
    event_provider = FixtureExtractionProvider(_events())
    with SessionLocal() as db:
        run = run_context_research(
            context=historical,
            db=db,
            provider_factory=lambda: research_provider,
            event_provider_factory=lambda: event_provider,
            model="fixture-historical-research",
            event_model="fixture-historical-extraction",
        )

    assert run.status == "succeeded"
    assert run.report["time_scope"]["data_mode"] == "historical_research"
    assert "not available to the system" in run.report["time_scope"]["limitation"]


def test_context_event_extraction_reuses_cache_and_keeps_media_coverage_visible():
    context = _context()
    first_provider = FixtureExtractionProvider(_events())
    second_provider = FixtureExtractionProvider(_events())
    with SessionLocal() as db:
        first = prepare_context_research(
            context=context,
            db=db,
            event_provider_factory=lambda: first_provider,
            event_model="fixture-event-cache-model",
        )
        second = prepare_context_research(
            context=context,
            db=db,
            event_provider_factory=lambda: second_provider,
            event_model="fixture-event-cache-model",
        )

    assert first.provider_call_count == 1
    assert first.cache_hit_count == 0
    assert second.provider_call_count == 0
    assert second.cache_hit_count == 1
    assert first.sources[0].events["events"][0]["evidence_quote"] in _TEXT
    anchor = first.sources[0].events["events"][0]
    assert _TEXT[anchor["quote_start"]:anchor["quote_end"]] == anchor["evidence_quote"]
    assert anchor["analysis_text_sha256"] == hashlib.sha256(_TEXT.encode()).hexdigest()
    assert first.sources[0].provenance["event_extraction"]["impact_direction_status"] == "model_generated_review_required"
    assert second.coverage_incomplete is True
    assert second_provider.calls == 0


def test_context_event_extraction_rejects_bad_event_quote_without_passing_source_to_research():
    provider = FixtureExtractionProvider(_events("not present in frozen text"))
    with SessionLocal() as db:
        prepared = prepare_context_research(
            context=_context(),
            db=db,
            event_provider_factory=lambda: provider,
            event_model="fixture-event-invalid-quote",
        )

    assert provider.calls == 1
    assert prepared.sources == ()
    assert prepared.provider_call_count == 1
    assert prepared.coverage_incomplete is True
    assert prepared.outcomes[0].status == "failed"
    assert "exact substring" in prepared.outcomes[0].error


def test_context_event_extraction_accepts_uploaded_media_without_upgrading_its_status():
    context = _context()
    event = context.events[0]
    media_snapshot = {
        **event.source_snapshot,
        "title": "Media report",
        "content_text": _TEXT,
        "content_text_sha256": hashlib.sha256(_TEXT.encode()).hexdigest(),
    }
    media_snapshot.pop("content_excerpt")
    media_event = FrozenEvidenceEvent(
        id=event.id,
        event_key=f"AAPL:uploaded_media:{event.id}",
        source_type="uploaded_media",
        source_id=event.source_id,
        content_sha256=event.content_sha256,
        published_at=event.published_at,
        observed_at=event.observed_at,
        review_status="pending_review",
        user_rating_stars=4,
        is_new=True,
        discovery_kind="initial",
        source_snapshot=media_snapshot,
    )
    media_context = EvidenceContext(
        symbol="AAPL", decision_at=context.decision_at, mode="observed", events=(media_event,),
        coverage_incomplete=False, omitted_source_refs=(),
    )
    provider = FixtureExtractionProvider(_events())
    with SessionLocal() as db:
        prepared = prepare_context_research(
            context=media_context,
            db=db,
            event_provider_factory=lambda: provider,
            event_model="fixture-event-media",
        )

    assert prepared.sources[0].source_type == "uploaded_media"
    assert prepared.sources[0].provenance["review_status"] == "pending_review"
    assert prepared.sources[0].provenance["event_extraction"]["impact_direction_status"] == "model_generated_review_required"


def test_uploaded_pdf_uses_frozen_extracted_text_hash_while_retaining_raw_bytes_fingerprint():
    context = _context()
    original = context.events[0]
    raw_pdf_bytes = b"%PDF-1.7\x00compressed-bytes-not-equal-to-extracted-text"
    raw_hash = hashlib.sha256(raw_pdf_bytes).hexdigest()
    media_snapshot = {
        **original.source_snapshot,
        "title": "Media PDF report",
        "content_text": _TEXT,
        "content_text_sha256": hashlib.sha256(_TEXT.encode()).hexdigest(),
        "content_sha256": raw_hash,
    }
    media_snapshot.pop("content_excerpt")
    pdf_event = FrozenEvidenceEvent(
        id=original.id,
        event_key=f"AAPL:uploaded_media:{original.id}",
        source_type="uploaded_media",
        source_id=original.source_id,
        content_sha256=raw_hash,
        published_at=original.published_at,
        observed_at=original.observed_at,
        review_status="pending_review",
        user_rating_stars=3,
        is_new=True,
        discovery_kind="initial",
        source_snapshot=media_snapshot,
    )
    pdf_context = EvidenceContext(
        symbol="AAPL", decision_at=context.decision_at, mode="observed", events=(pdf_event,),
        coverage_incomplete=False, omitted_source_refs=(),
    )

    source = frozen_research_sources(pdf_context)[0]

    assert source.document.sha256 == hashlib.sha256(_TEXT.encode()).hexdigest()
    assert source.provenance["content_sha256"] == raw_hash
    assert source.provenance["frozen_snapshot"]["content_text_sha256"] == hashlib.sha256(_TEXT.encode()).hexdigest()


def test_uploaded_media_rejects_tampered_extracted_text_hash():
    context = _context()
    original = context.events[0]
    media_snapshot = {
        **original.source_snapshot,
        "content_text": _TEXT,
        "content_text_sha256": "0" * 64,
    }
    media_snapshot.pop("content_excerpt")
    media_event = FrozenEvidenceEvent(
        id=original.id, event_key=f"AAPL:uploaded_media:{original.id}", source_type="uploaded_media",
        source_id=original.source_id, content_sha256=original.content_sha256,
        published_at=original.published_at, observed_at=original.observed_at,
        review_status="pending_review", user_rating_stars=2, is_new=True, discovery_kind="initial",
        source_snapshot=media_snapshot,
    )
    media_context = EvidenceContext(
        symbol="AAPL", decision_at=context.decision_at, mode="observed", events=(media_event,),
        coverage_incomplete=False, omitted_source_refs=(),
    )

    with pytest.raises(V2ResearchBridgeError, match="content_text_sha256"):
        frozen_research_sources(media_context)
