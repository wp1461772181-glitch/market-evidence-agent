from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.evidence_revision import (
    EvidenceRevisionError,
    create_evidence_revision,
    create_evidence_revision_tables,
    evidence_revisions_for_symbol,
)
from app.evidence_revision_models import EvidenceRevision
from app.event_provider import ProviderResult
from app.models import ForecastSnapshot, SecFilingInventory, UploadedEvidence


_PARENT_TIME = datetime(2026, 9, 1, 21, tzinfo=UTC)
_TEXT = "Apple reported strong services revenue."


class _FixedProvider:
    def extract(self, *, system_prompt: str, document_payload: str, model: str) -> ProviderResult:
        return ProviderResult(
            content=(
                '{"events":[{"event_type":"earnings_release","event_date":"2026-09-02",'
                '"impact_direction":"positive","summary":"Services revenue was reported as strong.",'
                '"evidence_quote":"Apple reported strong services revenue."}]}'
            ),
            response_model="test-model",
            usage=None,
        )


class _FailingProvider:
    def extract(self, **_: object) -> ProviderResult:
        raise RuntimeError("provider unavailable")


@pytest.fixture(autouse=True)
def evidence_revision_tables():
    create_evidence_revision_tables()


def _snapshot(db, *, symbol: str = "AAPL", cutoff: datetime = _PARENT_TIME) -> ForecastSnapshot:
    row = ForecastSnapshot(
        symbol=symbol,
        feature_trading_date=cutoff.date(),
        feature_as_of_time=cutoff,
        model_version="week4-calibrated-test",
        model_sha256="a" * 64,
        model_manifest_sha256="b" * 64,
        feature_export_sha256="c" * 64,
        feature_version="market-features-v1",
        feature_source="yahoo-finance-chart",
        feature_snapshot_mode="observed",
        feature_values={"momentum_5d": 0.01},
        bearish_probability=0.2,
        neutral_probability=0.5,
        bullish_probability=0.3,
        created_at=cutoff,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _official_filing(db, *, symbol: str = "AAPL", accepted_at: str = "2026-09-02T20:00:00+00:00") -> SecFilingInventory:
    row = SecFilingInventory(
        symbol=symbol,
        cik="0000320193",
        accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
        form="10-Q",
        filed_at=datetime(2026, 9, 2, tzinfo=UTC).date(),
        accepted_at=accepted_at,
        primary_document="form10q.htm",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/form10q.htm",
        source="sec-edgar",
        review_status="accepted",
        human_review_note="Relevant earnings document",
        reviewed_at=datetime(2026, 9, 3, tzinfo=UTC),
        observed_at=datetime(2026, 9, 3, tzinfo=UTC),
        content_status="fetched",
        content_observed_at=datetime(2026, 9, 3, tzinfo=UTC),
        content_excerpt=_TEXT,
        content_excerpt_sha256=hashlib.sha256(_TEXT.encode()).hexdigest(),
        content_truncated=False,
        content_error=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _uploaded_media(db, *, symbol: str = "AAPL", published_at: datetime = datetime(2026, 9, 2, 8, tzinfo=UTC)) -> UploadedEvidence:
    row = UploadedEvidence(
        symbol=symbol,
        title="Major product issue reported",
        source_url="https://news.example.test/apple-product-issue",
        published_at=published_at,
        observed_at=published_at + timedelta(minutes=5),
        credibility_stars=4,
        credibility_reason="Established financial publication with a named source.",
        impact_severity="high",
        filename="report.txt",
        content_sha256=hashlib.sha256(_TEXT.encode()).hexdigest(),
        raw_content=_TEXT.encode(),
        content_text=_TEXT,
        status="unconfirmed",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _count(model) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_manual_official_revision_copies_probabilities_and_keeps_the_original_immutable():
    with SessionLocal() as db:
        parent = _snapshot(db)
        filing = _official_filing(db)
        result = create_evidence_revision(
            parent_snapshot_id=parent.id,
            source_type="official_filing",
            source_id=filing.id,
            mode="manual",
            db=db,
            provider_factory=_FixedProvider,
            model="test-model",
        )

        db.refresh(parent)
        assert result.revised_snapshot.id != parent.id
        assert result.revised_snapshot.feature_values == parent.feature_values
        assert (
            result.revised_snapshot.bearish_probability,
            result.revised_snapshot.neutral_probability,
            result.revised_snapshot.bullish_probability,
        ) == (parent.bearish_probability, parent.neutral_probability, parent.bullish_probability)
        assert result.revision.parent_snapshot_id == parent.id
        assert result.revision.source_id == filing.id
        assert result.revision.official_confirmation is True
        assert result.revision.credibility_stars is None
        assert result.revision.direction_status == "review_required"
        assert result.revision.evidence_quote in filing.content_excerpt
        from app.dashboard import dashboard_snapshot_entries

        timeline = dashboard_snapshot_entries([parent, result.revised_snapshot], db)
        by_id = {entry.id: entry for entry in timeline}
        assert by_id[result.revised_snapshot.id].parent_snapshot_id == parent.id
        assert by_id[result.revised_snapshot.id].root_snapshot_id == parent.id
        assert by_id[result.revised_snapshot.id].version == 2

    body = result.as_dict()
    assert body["probabilities"]["numeric_probability_changed"] is False
    assert body["evidence"]["direction_status"] == "review_required"


def test_two_different_evidence_sources_can_branch_from_one_parent_snapshot():
    with SessionLocal() as db:
        parent = _snapshot(db)
        first = _official_filing(db)
        second = _official_filing(db)
        first_result = create_evidence_revision(
            parent_snapshot_id=parent.id,
            source_type="official_filing",
            source_id=first.id,
            mode="manual",
            db=db,
            provider_factory=_FixedProvider,
            model="test-model",
        )
        second_result = create_evidence_revision(
            parent_snapshot_id=parent.id,
            source_type="official_filing",
            source_id=second.id,
            mode="manual",
            db=db,
            provider_factory=_FixedProvider,
            model="test-model",
        )

    assert first_result.revised_snapshot.id != second_result.revised_snapshot.id
    with SessionLocal() as db:
        results = evidence_revisions_for_symbol(symbol="AAPL", db=db)
    assert {row.revision.id for row in results} >= {first_result.revision.id, second_result.revision.id}


def test_rejects_old_or_cross_symbol_source_before_calling_the_provider():
    with SessionLocal() as db:
        parent = _snapshot(db)
        old = _official_filing(db, accepted_at="2026-08-30T20:00:00+00:00")
        cross_symbol = _official_filing(db, symbol="MSFT")
        before = _count(EvidenceRevision)
        with pytest.raises(EvidenceRevisionError, match="after the selected forecast"):
            create_evidence_revision(
                parent_snapshot_id=parent.id,
                source_type="official_filing",
                source_id=old.id,
                mode="manual",
                db=db,
                provider_factory=_FailingProvider,
                model="test-model",
            )
        with pytest.raises(EvidenceRevisionError, match="belong to the selected forecast symbol"):
            create_evidence_revision(
                parent_snapshot_id=parent.id,
                source_type="official_filing",
                source_id=cross_symbol.id,
                mode="manual",
                db=db,
                provider_factory=_FailingProvider,
                model="test-model",
            )
    assert _count(EvidenceRevision) == before


def test_rejects_official_filing_without_an_exact_acceptance_timestamp():
    with SessionLocal() as db:
        parent = _snapshot(db)
        filing = _official_filing(db, accepted_at=None)
        with pytest.raises(EvidenceRevisionError, match="exact SEC acceptance timestamp"):
            create_evidence_revision(
                parent_snapshot_id=parent.id,
                source_type="official_filing",
                source_id=filing.id,
                mode="manual",
                db=db,
                provider_factory=_FailingProvider,
                model="test-model",
            )


def test_uploaded_media_remains_unconfirmed_and_cannot_trigger_automatic_revision():
    with SessionLocal() as db:
        parent = _snapshot(db)
        media = _uploaded_media(db)
        result = create_evidence_revision(
            parent_snapshot_id=parent.id,
            source_type="uploaded_media",
            source_id=media.id,
            mode="manual",
            db=db,
            provider_factory=_FixedProvider,
            model="test-model",
        )
        assert result.revision.official_confirmation is False
        assert result.revision.source_status == "unconfirmed"
        assert result.revision.credibility_stars == 4
        assert result.revision.impact_severity == "high"
        with pytest.raises(EvidenceRevisionError, match="require an official filing"):
            create_evidence_revision(
                parent_snapshot_id=parent.id,
                source_type="uploaded_media",
                source_id=media.id,
                mode="automatic",
                db=db,
                provider_factory=_FixedProvider,
                model="test-model",
            )


def test_provider_failure_and_duplicate_do_not_persist_another_evidence_revision():
    with SessionLocal() as db:
        parent = _snapshot(db)
        filing = _official_filing(db)
        before = _count(ForecastSnapshot)
        with pytest.raises(RuntimeError, match="provider unavailable"):
            create_evidence_revision(
                parent_snapshot_id=parent.id,
                source_type="official_filing",
                source_id=filing.id,
                mode="manual",
                db=db,
                provider_factory=_FailingProvider,
                model="test-model",
            )
        assert _count(ForecastSnapshot) == before
        first = create_evidence_revision(
            parent_snapshot_id=parent.id,
            source_type="official_filing",
            source_id=filing.id,
            mode="manual",
            db=db,
            provider_factory=_FixedProvider,
            model="test-model",
        )
        first_id = first.revision.id
        after_first = _count(ForecastSnapshot)
        with pytest.raises(EvidenceRevisionError, match="already revised"):
            create_evidence_revision(
                parent_snapshot_id=parent.id,
                source_type="official_filing",
                source_id=filing.id,
                mode="manual",
                db=db,
                provider_factory=_FixedProvider,
                model="test-model",
            )
    assert first_id
    assert _count(ForecastSnapshot) == after_first


def test_automatic_revision_uses_the_forecast_creation_time_and_72_hour_window():
    with SessionLocal() as db:
        parent = _snapshot(db)
        filing = _official_filing(db)
        with pytest.raises(EvidenceRevisionError, match="limited to sources within three days"):
            create_evidence_revision(
                parent_snapshot_id=parent.id,
                source_type="official_filing",
                source_id=filing.id,
                mode="automatic",
                db=db,
                provider_factory=_FixedProvider,
                model="test-model",
                checked_at=_PARENT_TIME + timedelta(days=3, seconds=1),
            )

        result = create_evidence_revision(
            parent_snapshot_id=parent.id,
            source_type="official_filing",
            source_id=filing.id,
            mode="automatic",
            db=db,
            provider_factory=_FixedProvider,
            model="test-model",
            checked_at=_PARENT_TIME + timedelta(days=2),
        )
    assert result.revision.mode == "automatic"


def test_evidence_revision_http_contract_returns_the_saved_immutable_link(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "configured_deepseek_model", lambda: "test-model")
    monkeypatch.setattr(main, "create_deepseek_provider_from_env", lambda *, model: _FixedProvider())
    with SessionLocal() as db:
        parent = _snapshot(db)
        filing = _official_filing(db)

    created = client.post(
        "/evidence-revisions/AAPL",
        json={
            "parent_snapshot_id": str(parent.id),
            "source_type": "official_filing",
            "source_id": str(filing.id),
            "mode": "manual",
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["parent_snapshot_id"] == str(parent.id)
    assert body["source_id"] == str(filing.id)
    assert body["revised_snapshot_id"] != str(parent.id)
    assert body["review_status"] == "pending_review"
    assert body["model_probability_changed"] is False

    listed = client.get("/evidence-revisions/AAPL")
    assert listed.status_code == 200
    assert any(item["id"] == body["id"] for item in listed.json()["revisions"])
