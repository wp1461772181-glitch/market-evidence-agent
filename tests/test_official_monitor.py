from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.database import Base, SessionLocal, engine
from app.evidence_revision_models import EvidenceRevision
from app.models import ForecastRevision, ForecastSnapshot, SecFilingInventory
from app.official_monitor import run_once
from app.sec_filings import SecFilingContent


_RUN_TIME = datetime(2026, 9, 12, 12, tzinfo=UTC)


class FakeSecProvider:
    def __init__(self, filings_by_symbol):
        self.filings_by_symbol = filings_by_symbol
        self.fetches: list[str] = []

    def discover(self, symbol):
        return list(self.filings_by_symbol[symbol])

    def fetch_primary_document(self, filing):
        self.fetches.append(filing.accession_number)
        return SecFilingContent(
            excerpt=f"Official text for {filing.accession_number}",
            excerpt_sha256=("a" if filing.accession_number.endswith("1") else "b") * 64,
            truncated=False,
        )


def _filing(symbol: str, accession: str, accepted_at: str):
    from app.sec_filings import DiscoveredSecFiling

    return DiscoveredSecFiling(
        cik="0000320193",
        accession_number=accession,
        form="8-K",
        filed_at=date(2026, 9, 12),
        accepted_at=accepted_at,
        primary_document="report.htm",
        source_url=f"https://www.sec.gov/Archives/edgar/data/320193/{accession.replace('-', '')}/report.htm",
    )


def _provider(*, aapl_filings):
    empty = {symbol: [] for symbol in ("AAPL", "AMZN", "GOOGL", "MSFT", "NVDA")}
    empty["AAPL"] = aapl_filings
    return FakeSecProvider(empty)


def _snapshot(*, created_at: datetime, symbol: str = "AAPL", mode: str = "observed") -> ForecastSnapshot:
    return ForecastSnapshot(
        symbol=symbol,
        feature_trading_date=created_at.date(),
        feature_as_of_time=created_at,
        model_version="test-model",
        model_sha256="c" * 64,
        model_manifest_sha256="d" * 64,
        feature_export_sha256="e" * 64,
        feature_version="test-features",
        feature_source="test",
        feature_snapshot_mode=mode,
        feature_values={"return_1d": 0.1},
        bearish_probability=0.2,
        neutral_probability=0.3,
        bullish_probability=0.5,
        created_at=created_at,
    )


def _clear_monitor_rows() -> None:
    with SessionLocal() as db:
        revision_snapshot_ids = [row[0] for row in db.query(EvidenceRevision.revised_snapshot_id).all()]
        if revision_snapshot_ids:
            db.query(EvidenceRevision).filter(EvidenceRevision.revised_snapshot_id.in_(revision_snapshot_ids)).delete(
                synchronize_session=False
            )
        db.query(ForecastRevision).filter(ForecastRevision.snapshot_id.in_(revision_snapshot_ids)).delete(
            synchronize_session=False
        )
        db.query(ForecastSnapshot).filter(ForecastSnapshot.model_version == "test-model").delete(
            synchronize_session=False
        )
        db.query(SecFilingInventory).filter(SecFilingInventory.cik == "0000320193").delete(
            synchronize_session=False
        )
        db.commit()


def _prepare_tables() -> None:
    Base.metadata.create_all(bind=engine)
    EvidenceRevision.__table__.create(bind=engine, checkfirst=True)


def _successful_callback(calls):
    def callback(**kwargs):
        calls.append(kwargs)
        db = kwargs["db"]
        parent = kwargs["parent_snapshot"]
        filing = kwargs["filing"]
        revised = _snapshot(created_at=kwargs["observed_at"])
        db.add(revised)
        db.flush()
        db.add(
            EvidenceRevision(
                parent_snapshot_id=parent.id,
                revised_snapshot_id=revised.id,
                symbol=parent.symbol,
                source_type="official_filing",
                source_id=filing.id,
                mode="automatic",
                source_title=f"{filing.symbol} official filing",
                source_url=filing.source_url,
                source_published_at=kwargs["observed_at"],
                source_observed_at=kwargs["observed_at"],
                source_content_sha256=filing.content_excerpt_sha256,
                analysis_text_characters=len(filing.content_excerpt),
                analysis_text_truncated=False,
                official_confirmation=True,
                credibility_stars=None,
                credibility_reason=None,
                impact_severity=None,
                source_status=filing.review_status,
                extraction_cache_key="f" * 64,
                evidence_summary="Test evidence summary.",
                evidence_quote="Official text",
                model_impact_direction="uncertain",
                direction_status="review_required",
            )
        )
        db.commit()

    return callback


def test_run_once_fetches_only_new_official_filings_and_calls_revision_for_recent_root():
    _prepare_tables()
    _clear_monitor_rows()
    filing = _filing("AAPL", "0000320193-26-000001", "2026-09-12T11:00:00Z")
    provider = _provider(aapl_filings=[filing])
    calls = []
    with SessionLocal() as db:
        parent = _snapshot(created_at=_RUN_TIME - timedelta(hours=24))
        db.add(parent)
        db.commit()

        result = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME,
            revision_callback=_successful_callback(calls),
        )
        repeated = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME + timedelta(hours=1),
            revision_callback=_successful_callback(calls),
        )

    aapl = next(item for item in result.symbols if item.symbol == "AAPL")
    assert aapl.created_count == 1
    assert aapl.filings[0].revision_status == "created"
    assert provider.fetches == ["0000320193-26-000001"]
    assert calls[0]["parent_snapshot"].id == parent.id
    assert calls[0]["filing"].content_status == "fetched"
    assert next(item for item in repeated.symbols if item.symbol == "AAPL").filings == []
    assert len(calls) == 1


def test_monitor_records_but_does_not_auto_revise_before_parent_future_or_unaccepted_filings():
    _prepare_tables()
    _clear_monitor_rows()
    filings = [
        _filing("AAPL", "0000320193-26-000011", "2026-09-12T11:00:00Z"),
        _filing("AAPL", "0000320193-26-000012", "2026-09-12T13:00:00Z"),
        _filing("AAPL", "0000320193-26-000013", "not-a-time"),
    ]
    provider = _provider(aapl_filings=filings)
    calls = []
    with SessionLocal() as db:
        db.add(_snapshot(created_at=_RUN_TIME - timedelta(hours=1)))
        db.commit()
        result = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME,
            revision_callback=lambda **kwargs: calls.append(kwargs),
        )

    aapl = next(item for item in result.symbols if item.symbol == "AAPL")
    assert aapl.created_count == 3
    by_accession = {item.accession_number: item for item in aapl.filings}
    assert by_accession["0000320193-26-000011"].content_status == "fetched"
    assert by_accession["0000320193-26-000012"].content_status == "not_fetched"
    assert by_accession["0000320193-26-000013"].content_status == "not_fetched"
    assert all(item.revision_status == "not_eligible" for item in aapl.filings)
    assert any("before the selected forecast" in (item.error or "") for item in aapl.filings)
    assert any("later than this monitor run" in (item.error or "") for item in aapl.filings)
    assert any("no valid SEC acceptance" in (item.error or "") for item in aapl.filings)
    assert provider.fetches == ["0000320193-26-000011"]
    assert calls == []


def test_monitor_keeps_new_filing_but_skips_an_expired_observed_root():
    _prepare_tables()
    _clear_monitor_rows()
    provider = _provider(aapl_filings=[_filing("AAPL", "0000320193-26-000014", "2026-09-12T11:00:00Z")])
    calls = []
    with SessionLocal() as db:
        db.add(_snapshot(created_at=_RUN_TIME - timedelta(hours=73)))
        db.commit()
        result = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME,
            revision_callback=lambda **kwargs: calls.append(kwargs),
        )

    aapl = next(item for item in result.symbols if item.symbol == "AAPL")
    assert aapl.filings[0].content_status == "fetched"
    assert aapl.filings[0].revision_status == "not_eligible"
    assert "older than 72 hours" in (aapl.filings[0].error or "")
    assert provider.fetches == ["0000320193-26-000014"]
    assert calls == []


def test_monitor_fetches_a_recent_official_filing_but_does_not_revise_outside_the_window():
    _prepare_tables()
    _clear_monitor_rows()
    provider = _provider(aapl_filings=[_filing("AAPL", "0000320193-26-000015", "2026-09-12T11:00:00Z")])
    with SessionLocal() as db:
        result = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME,
            revision_callback=lambda **_: (_ for _ in ()).throw(AssertionError("must not revise")),
        )

    aapl = next(item for item in result.symbols if item.symbol == "AAPL")
    assert aapl.created_count == 1
    assert aapl.filings[0].content_status == "fetched"
    assert aapl.filings[0].revision_status == "not_eligible"
    assert aapl.filings[0].error
    assert provider.fetches == ["0000320193-26-000015"]


def test_monitor_saves_old_inventory_metadata_without_fetching_when_no_observed_root_exists():
    _prepare_tables()
    _clear_monitor_rows()
    provider = _provider(aapl_filings=[_filing("AAPL", "0000320193-26-000016", "2026-09-07T11:00:00Z")])
    with SessionLocal() as db:
        result = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME,
            revision_callback=lambda **_: (_ for _ in ()).throw(AssertionError("must not revise")),
        )

    aapl = next(item for item in result.symbols if item.symbol == "AAPL")
    assert aapl.filings[0].content_status == "not_fetched"
    assert aapl.filings[0].revision_status == "not_eligible"
    assert "older than 72 hours" in (aapl.filings[0].error or "")
    assert provider.fetches == []


def test_monitor_surfaces_callback_failure_without_creating_a_revision():
    _prepare_tables()
    _clear_monitor_rows()
    provider = _provider(aapl_filings=[_filing("AAPL", "0000320193-26-000021", "2026-09-12T11:00:00Z")])
    with SessionLocal() as db:
        db.add(_snapshot(created_at=_RUN_TIME - timedelta(hours=2)))
        db.commit()
        result = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME,
            revision_callback=lambda **_: (_ for _ in ()).throw(ValueError("model unavailable")),
        )
        revision_count = db.query(EvidenceRevision).count()

    aapl = next(item for item in result.symbols if item.symbol == "AAPL")
    assert aapl.filings[0].revision_status == "failed"
    assert aapl.filings[0].error == "model unavailable"
    assert revision_count == 0


def test_monitor_retries_a_saved_eligible_filing_until_its_callback_creates_a_revision():
    _prepare_tables()
    _clear_monitor_rows()
    provider = _provider(aapl_filings=[_filing("AAPL", "0000320193-26-000031", "2026-09-12T11:00:00Z")])
    calls = []
    with SessionLocal() as db:
        db.add(_snapshot(created_at=_RUN_TIME - timedelta(hours=2)))
        db.commit()
        first = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME,
            revision_callback=lambda **_: (_ for _ in ()).throw(RuntimeError("temporary provider failure")),
        )
        second = run_once(
            db=db,
            provider=provider,
            observed_at=_RUN_TIME + timedelta(hours=1),
            revision_callback=_successful_callback(calls),
        )

    assert next(item for item in first.symbols if item.symbol == "AAPL").filings[0].revision_status == "failed"
    assert next(item for item in second.symbols if item.symbol == "AAPL").filings[0].revision_status == "created"
    assert provider.fetches == ["0000320193-26-000031"]
    assert len(calls) == 1
