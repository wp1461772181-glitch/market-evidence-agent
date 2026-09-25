import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.evidence_context import EvidenceContextError, freeze_evidence_context
from app.forecast_v2_models import EvidenceEventVersionV2
from app.models import SecFilingInventory, UploadedEvidence


@pytest.fixture(autouse=True)
def v2_tables(disposable_database):
    Base.metadata.create_all(bind=engine)


def _filing(*, symbol="AAPL", accepted_at=None, observed_at=None, content="official evidence", status="accepted"):
    accepted_at = accepted_at or datetime(2026, 9, 10, 14, tzinfo=UTC)
    observed_at = observed_at or accepted_at + timedelta(minutes=5)
    with SessionLocal() as db:
        row = SecFilingInventory(
            symbol=symbol, cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
            form="10-Q", filed_at=accepted_at.date(), accepted_at=accepted_at.isoformat(),
            primary_document="q.htm", source_url="https://www.sec.gov/Archives/example/q.htm", source="sec-edgar",
            review_status=status, human_review_note="fixture", reviewed_at=observed_at, observed_at=observed_at,
            content_status="fetched", content_observed_at=observed_at, content_excerpt=content,
            content_excerpt_sha256=hashlib.sha256(content.encode()).hexdigest(), content_truncated=False, content_error=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _media(*, symbol="AAPL", published_at=None, observed_at=None, content=b"media evidence", content_text=None):
    published_at = published_at or datetime(2026, 9, 10, 15, tzinfo=UTC)
    observed_at = observed_at or published_at + timedelta(minutes=5)
    content_text = content.decode() if content_text is None else content_text
    with SessionLocal() as db:
        row = UploadedEvidence(
            symbol=symbol, title="Media report", source_url="https://news.example.test/report", published_at=published_at,
            observed_at=observed_at, credibility_stars=4, credibility_reason="fixture", impact_severity="high",
            filename="report.txt", content_sha256=hashlib.sha256(content).hexdigest(), raw_content=content,
            content_text=content_text, status="unconfirmed",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_observed_context_freezes_two_source_types_and_reuses_same_content_review_cache():
    filing_id = _filing()
    media_id = _media()
    cutoff = datetime(2026, 9, 11, tzinfo=UTC)
    with SessionLocal() as db:
        refs = [
            {"source_type": "official_filing", "source_id": filing_id},
            {"source_type": "uploaded_media", "source_id": media_id},
        ]
        first = freeze_evidence_context(db=db, symbol="AAPL", decision_at=cutoff, source_refs=refs)
        second = freeze_evidence_context(db=db, symbol="AAPL", decision_at=cutoff, source_refs=refs)
        rows = list(db.scalars(select(EvidenceEventVersionV2).where(EvidenceEventVersionV2.symbol == "AAPL")))

    assert {event.source_id for event in first.events} == {filing_id, media_id}
    assert [event.id for event in second.events] == [event.id for event in first.events]
    assert len(rows) == 2
    official = next(row for row in rows if row.source_id == filing_id)
    assert official.source_snapshot["content_excerpt"] == "official evidence"
    assert official.source_snapshot["coverage"] == "fetched_excerpt"
    media = next(event for event in first.events if event.source_id == media_id)
    assert media.user_rating_stars == 4
    assert media.source_url == "https://news.example.test/report"
    assert media.frozen_text == "media evidence"
    assert media.source_snapshot["analysis_locator"] == {
        "kind": "extracted_text_char_range", "start": 0, "end": len("media evidence")
    }
    assert media.source_snapshot["coverage_incomplete"] is False
    manifest = next(item for item in first.evidence_manifest() if item["source_id"] == str(media_id))
    assert manifest["event_version_id"] == str(media.id)
    assert "source_snapshot" not in manifest
    assert "content_text" not in manifest
    assert "analysis_text" not in manifest
    assert "media evidence" not in repr(manifest)


def test_8k_attachment_snapshot_preserves_accession_time_hash_locator_and_coverage():
    accepted_at = datetime(2026, 9, 10, 14, tzinfo=UTC)
    attachment_text = "Quarterly results exhibit: revenue increased."
    attachment_hash = hashlib.sha256(attachment_text.encode()).hexdigest()
    attachment_url = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000099/ex99-1.htm"
    with SessionLocal() as db:
        row = SecFilingInventory(
            symbol="AAPL", cik="0000320193", accession_number="0000320193-26-000099",
            form="8-K", filed_at=accepted_at.date(), accepted_at=accepted_at.isoformat(),
            primary_document="aapl-8k.htm",
            source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000099/aapl-8k.htm",
            source="sec-edgar", review_status="pending_review", human_review_note=None, reviewed_at=None,
            observed_at=accepted_at, content_status="fetched", content_observed_at=accepted_at,
            content_excerpt=attachment_text, content_excerpt_sha256=attachment_hash, content_truncated=False,
            content_error=None, content_source_url=attachment_url, content_document_name="ex99-1.htm",
            content_kind="exhibit_99_1", related_attachment_status="fetched", related_attachment_error=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        context = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=datetime(2026, 9, 11, tzinfo=UTC),
            source_refs=[{"source_type": "official_filing", "source_id": row.id}],
        )

    event = context.events[0]
    snapshot = event.source_snapshot
    assert event.source_url == attachment_url
    assert event.content_sha256 == attachment_hash
    assert event.published_at == accepted_at
    assert snapshot["filing_source_url"].endswith("/aapl-8k.htm")
    assert snapshot["publication_time_basis"] == "sec_accession_acceptance_time"
    assert snapshot["document_kind"] == "exhibit_99_1"
    assert snapshot["related_attachment_status"] == "fetched"
    assert snapshot["analysis_locator"] == {"kind": "extracted_text_char_range", "start": 0, "end": len(attachment_text)}
    assert snapshot["coverage"] == "8k_related_exhibit"
    assert snapshot["coverage_incomplete"] is False


def test_new_8k_with_unavailable_attachment_or_analysis_limit_marks_context_incomplete():
    accepted_at = datetime(2026, 9, 10, 14, tzinfo=UTC)
    content = "x" * 24_001
    with SessionLocal() as db:
        row = SecFilingInventory(
            symbol="AAPL", cik="0000320193", accession_number="0000320193-26-000098",
            form="8-K", filed_at=accepted_at.date(), accepted_at=accepted_at.isoformat(),
            primary_document="aapl-8k.htm",
            source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000098/aapl-8k.htm",
            source="sec-edgar", review_status="pending_review", human_review_note=None, reviewed_at=None,
            observed_at=accepted_at, content_status="fetched", content_observed_at=accepted_at,
            content_excerpt=content, content_excerpt_sha256=hashlib.sha256(content.encode()).hexdigest(),
            content_truncated=False, content_error=None,
            content_source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000098/aapl-8k.htm",
            content_document_name="aapl-8k.htm", content_kind="primary_document",
            related_attachment_status="unavailable", related_attachment_error="fixture directory error",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        context = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=datetime(2026, 9, 11, tzinfo=UTC),
            source_refs=[{"source_type": "official_filing", "source_id": row.id}],
        )

    event = next(event for event in context.events if event.source_id == row.id)
    assert event.source_snapshot["coverage_incomplete"] is True
    assert event.source_snapshot["analysis_text_truncated"] is True
    assert event.source_snapshot["related_attachment_status"] == "unavailable"
    assert context.coverage_incomplete is True


def test_observed_filter_excludes_late_source_while_historical_research_records_backfill():
    late = _filing(observed_at=datetime(2026, 9, 12, tzinfo=UTC))
    cutoff = datetime(2026, 9, 11, tzinfo=UTC)
    with SessionLocal() as db:
        with pytest.raises(EvidenceContextError) as observed_error:
            freeze_evidence_context(
                db=db, symbol="AAPL", decision_at=cutoff, mode="observed",
                source_refs=[{"source_type": "official_filing", "source_id": late}],
            )
        historical = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=cutoff, mode="historical_research",
            source_refs=[{"source_type": "official_filing", "source_id": late}],
        )

    assert observed_error.value.code == "future_source"
    assert late in {event.source_id for event in historical.events}
    assert historical.mode == "historical_research"


def test_explicit_missing_official_content_and_ambiguous_acceptance_time_fail_closed():
    accepted_at = datetime(2026, 9, 10, 14, tzinfo=UTC)
    with SessionLocal() as db:
        missing = SecFilingInventory(
            symbol="AAPL", cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
            form="8-K", filed_at=accepted_at.date(), accepted_at=accepted_at.isoformat(), primary_document="k.htm",
            source_url="https://www.sec.gov/Archives/example/k.htm", source="sec-edgar", review_status="pending_review",
            human_review_note=None, reviewed_at=None, observed_at=accepted_at, content_status="not_fetched",
            content_observed_at=None, content_excerpt=None, content_excerpt_sha256=None, content_truncated=False, content_error=None,
        )
        ambiguous = SecFilingInventory(
            symbol="AAPL", cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
            form="8-K", filed_at=accepted_at.date(), accepted_at="20260910140000", primary_document="k2.htm",
            source_url="https://www.sec.gov/Archives/example/k2.htm", source="sec-edgar", review_status="pending_review",
            human_review_note=None, reviewed_at=None, observed_at=accepted_at, content_status="fetched",
            content_observed_at=accepted_at, content_excerpt="text", content_excerpt_sha256=hashlib.sha256(b"text").hexdigest(),
            content_truncated=False, content_error=None,
        )
        db.add_all([missing, ambiguous])
        db.commit()
        db.refresh(missing)
        db.refresh(ambiguous)
        with pytest.raises(EvidenceContextError) as no_content:
            freeze_evidence_context(
                db=db, symbol="AAPL", decision_at=datetime(2026, 9, 11, tzinfo=UTC),
                source_refs=[{"source_type": "official_filing", "source_id": missing.id}],
            )
        assert no_content.value.code == "missing_official_content"
        with pytest.raises(EvidenceContextError) as ambiguous_time:
            freeze_evidence_context(
                db=db, symbol="AAPL", decision_at=datetime(2026, 9, 11, tzinfo=UTC),
                source_refs=[{"source_type": "official_filing", "source_id": ambiguous.id}],
            )
        assert ambiguous_time.value.code == "ambiguous_official_time"


def test_review_change_appends_event_version_and_incremental_context_keeps_background():
    first_source = _filing(content="first source")
    initial_cutoff = datetime(2026, 9, 11, tzinfo=UTC)
    with SessionLocal() as db:
        initial = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=initial_cutoff,
            source_refs=[{"source_type": "official_filing", "source_id": first_source}],
        )
        initial_id = next(event.id for event in initial.events if event.source_id == first_source)
        filing = db.get(SecFilingInventory, first_source)
        assert filing is not None
        filing.review_status = "rejected"
        filing.human_review_note = "revised review"
        filing.reviewed_at = datetime(2026, 9, 11, 1, tzinfo=UTC)
        db.commit()
        new_source = _media(
            published_at=datetime(2026, 9, 11, 2, tzinfo=UTC), content=b"new incremental media"
        )
        context = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=datetime(2026, 9, 12, tzinfo=UTC),
            previous_event_ids=[initial_id], previous_decision_at=initial_cutoff,
            source_refs=[
                {"source_type": "official_filing", "source_id": first_source},
                {"source_type": "uploaded_media", "source_id": new_source},
            ],
        )
        versions = list(db.scalars(select(EvidenceEventVersionV2).where(EvidenceEventVersionV2.source_id == first_source)))

    changed = next(event for event in context.events if event.source_id == first_source)
    new_media = next(event for event in context.events if event.source_id == new_source)
    assert changed.id != initial_id and changed.discovery_kind == "state_changed"
    assert new_media.is_new and new_media.discovery_kind == "new_publication"
    assert len(versions) == 2
    assert any(row.previous_version_id == initial_id for row in versions)


def test_duplicate_media_content_is_deterministically_one_event_and_cross_symbol_prior_is_rejected():
    first = _media(content=b"same report")
    second = _filing(content="same report")
    with SessionLocal() as db:
        context = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=datetime(2026, 9, 11, tzinfo=UTC),
            source_refs=[
                {"source_type": "uploaded_media", "source_id": first},
                {"source_type": "official_filing", "source_id": second},
            ],
        )
        same_report = [event for event in context.events if event.source_id in {first, second}]
        assert len(same_report) == 1
        selected_same_report = same_report[0]
        revised = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=datetime(2026, 9, 12, tzinfo=UTC),
            previous_event_ids=[selected_same_report.id], previous_decision_at=datetime(2026, 9, 11, tzinfo=UTC),
            source_refs=[
                {"source_type": "uploaded_media", "source_id": first},
                {"source_type": "official_filing", "source_id": second},
            ],
        )
        revised_same_report = next(event for event in revised.events if event.source_id == selected_same_report.source_id)
        assert revised_same_report.id == selected_same_report.id
        assert revised_same_report.discovery_kind == "inherited"
        other = _media(symbol="MSFT")
        other_context = freeze_evidence_context(
            db=db, symbol="MSFT", decision_at=datetime(2026, 9, 11, tzinfo=UTC),
            source_refs=[{"source_type": "uploaded_media", "source_id": other}],
        )
        with pytest.raises(EvidenceContextError) as cross_symbol:
            freeze_evidence_context(
                db=db, symbol="AAPL", decision_at=datetime(2026, 9, 11, tzinfo=UTC),
                previous_event_ids=[other_context.events[0].id],
            )
    assert cross_symbol.value.code == "cross_symbol"


def test_initial_automatic_context_prioritizes_latest_reports_and_recent_candidates_not_oldest_rows():
    symbol = "AMZN"
    old_8k = _filing(
        symbol=symbol, accepted_at=datetime(2026, 1, 2, tzinfo=UTC), content="old 8k inventory row"
    )
    latest_annual = _filing(
        symbol=symbol, accepted_at=datetime(2026, 2, 10, tzinfo=UTC), content="latest annual report"
    )
    latest_quarterly = _filing(
        symbol=symbol, accepted_at=datetime(2026, 8, 20, tzinfo=UTC), content="latest quarterly report"
    )
    recent_media = _media(
        symbol=symbol, published_at=datetime(2026, 9, 10, tzinfo=UTC), content=b"recent candidate"
    )
    with SessionLocal() as db:
        for row_id, form in ((latest_annual, "10-K"), (latest_quarterly, "10-Q")):
            row = db.get(SecFilingInventory, row_id)
            assert row is not None
            row.form = form
        db.commit()
        context = freeze_evidence_context(
            db=db, symbol=symbol, decision_at=datetime(2026, 9, 15, tzinfo=UTC)
        )

    selected_ids = {event.source_id for event in context.events}
    assert {latest_annual, latest_quarterly, recent_media} <= selected_ids
    assert old_8k not in selected_ids


def test_automatic_invalid_inventory_is_omitted_without_blocking_valid_sources_but_explicit_ref_fails():
    symbol = "NVDA"
    valid = _media(symbol=symbol, content=b"valid automatic evidence")
    accepted_at = datetime(2026, 9, 10, 14, tzinfo=UTC)
    with SessionLocal() as db:
        missing = SecFilingInventory(
            symbol=symbol, cik="0001045810", accession_number=f"0001045810-26-{uuid4().int % 1_000_000:06d}",
            form="8-K", filed_at=accepted_at.date(), accepted_at=accepted_at.isoformat(), primary_document="k.htm",
            source_url="https://www.sec.gov/Archives/example/nvda-k.htm", source="sec-edgar", review_status="pending_review",
            human_review_note=None, reviewed_at=None, observed_at=accepted_at, content_status="not_fetched",
            content_observed_at=None, content_excerpt=None, content_excerpt_sha256=None, content_truncated=False, content_error=None,
        )
        db.add(missing)
        db.commit()
        db.refresh(missing)
        missing_id = missing.id
        automatic = freeze_evidence_context(
            db=db, symbol=symbol, decision_at=datetime(2026, 9, 11, tzinfo=UTC)
        )
        with pytest.raises(EvidenceContextError) as explicit:
            freeze_evidence_context(
                db=db, symbol=symbol, decision_at=datetime(2026, 9, 11, tzinfo=UTC),
                source_refs=[{"source_type": "official_filing", "source_id": missing_id}],
            )

    assert [event.source_id for event in automatic.events] == [valid]
    assert automatic.coverage_incomplete is True
    assert {item["source_id"] for item in automatic.omitted_source_refs} == {str(missing_id)}
    assert explicit.value.code == "missing_official_content"


def test_later_legacy_review_is_unknown_at_old_decision_and_does_not_rewrite_frozen_prior():
    accepted_at = datetime(2026, 9, 10, 14, tzinfo=UTC)
    source_id = _filing(
        accepted_at=accepted_at,
        observed_at=accepted_at + timedelta(minutes=5),
        content="review time boundary",
        status="accepted",
    )
    old_cutoff = datetime(2026, 9, 11, tzinfo=UTC)
    late_review = datetime(2026, 9, 12, tzinfo=UTC)
    with SessionLocal() as db:
        source = db.get(SecFilingInventory, source_id)
        assert source is not None
        source.reviewed_at = late_review
        source.human_review_note = "reviewed later"
        db.commit()
        old_context = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=old_cutoff,
            source_refs=[{"source_type": "official_filing", "source_id": source_id}],
        )
        old_event = next(event for event in old_context.events if event.source_id == source_id)
        old_row = db.get(EvidenceEventVersionV2, old_event.id)
        assert old_row is not None
        later_context = freeze_evidence_context(
            db=db, symbol="AAPL", decision_at=datetime(2026, 9, 13, tzinfo=UTC),
            previous_event_ids=[old_row.id], previous_decision_at=old_cutoff,
            source_refs=[{"source_type": "official_filing", "source_id": source_id}],
        )
        reread_old = db.get(EvidenceEventVersionV2, old_row.id)

    old_event = next(event for event in old_context.events if event.source_id == source_id)
    later_event = next(event for event in later_context.events if event.source_id == source_id)
    assert old_event.review_status == "pending_review"
    assert old_row.review_snapshot["temporally_available"] is False
    assert later_event.review_status == "accepted"
    assert later_event.id != old_event.id
    assert reread_old.review_status == "pending_review"


def test_explicit_source_refs_supplement_initial_automatic_background():
    symbol = "GOOGL"
    automatic_report = _filing(
        symbol=symbol, accepted_at=datetime(2026, 9, 10, 14, tzinfo=UTC), content="automatic quarterly report"
    )
    selected_old_media = _media(
        symbol=symbol,
        published_at=datetime(2026, 1, 10, tzinfo=UTC),
        content=b"manually selected older media",
    )
    with SessionLocal() as db:
        context = freeze_evidence_context(
            db=db,
            symbol=symbol,
            decision_at=datetime(2026, 9, 11, tzinfo=UTC),
            source_refs=[{"source_type": "uploaded_media", "source_id": selected_old_media}],
        )

    assert {automatic_report, selected_old_media} <= {event.source_id for event in context.events}


def test_more_than_ten_explicit_new_sources_is_rejected_instead_of_silently_omitted():
    symbol = "NVDA"
    refs = [
        {
            "source_type": "uploaded_media",
            "source_id": _media(symbol=symbol, content=f"explicit source {index}".encode()),
        }
        for index in range(11)
    ]
    with SessionLocal() as db:
        with pytest.raises(EvidenceContextError) as error:
            freeze_evidence_context(
                db=db,
                symbol=symbol,
                decision_at=datetime(2026, 9, 11, tzinfo=UTC),
                source_refs=refs,
            )

    assert error.value.code == "explicit_source_limit"


def test_uploaded_pdf_keeps_raw_bytes_and_extracted_text_hashes_distinct():
    raw_pdf = b"%PDF-1.7\n\xff\x00binary payload"
    extracted_text = "Revenue grew 12 percent in the uploaded PDF."
    media_id = _media(symbol="GOOGL", content=raw_pdf, content_text=extracted_text)
    with SessionLocal() as db:
        context = freeze_evidence_context(
            db=db,
            symbol="GOOGL",
            decision_at=datetime(2026, 9, 11, tzinfo=UTC),
            source_refs=[{"source_type": "uploaded_media", "source_id": media_id}],
        )

    event = next(event for event in context.events if event.source_id == media_id)
    snapshot = event.source_snapshot
    raw_hash = hashlib.sha256(raw_pdf).hexdigest()
    text_hash = hashlib.sha256(extracted_text.encode()).hexdigest()
    assert event.content_sha256 == raw_hash
    assert snapshot["content_hash_basis"] == "raw_bytes"
    assert snapshot["raw_content_sha256"] == raw_hash
    assert snapshot["content_text_sha256"] == text_hash
    assert raw_hash != text_hash
    assert snapshot["content_text"] == extracted_text
