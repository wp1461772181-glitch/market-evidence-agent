from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.database import Base, SessionLocal, engine
from app.forecast_contract import create_root_contract
from app.forecast_v2_models import ForecastJobV2, ForecastVersionV2, OfficialMonitorRunV2
from app.models import SecFilingInventory
from app.official_monitor_v2 import MONITOR_LOCK_KEY, run_once
from sqlalchemy import text
from app.sec_filings import DiscoveredSecFiling, SecDiscoveryCoverage, SecFilingContent, SecFilingsError


NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
SYMBOLS = ("AAPL", "AMZN", "GOOGL", "MSFT", "NVDA")


class _Provider:
    def __init__(self, filings_by_symbol=None, failing_symbols=(), failing_fetch_symbols=()):
        self.filings_by_symbol = filings_by_symbol or {}
        self.failing_symbols = set(failing_symbols)
        self.failing_fetch_symbols = set(failing_fetch_symbols)
        self.calls: list[tuple[str, date, date]] = []
        self.fetches: list[str] = []

    def discover_between(self, symbol, *, start_date, end_date, max_pages, max_filings, resume_page=None):
        self.calls.append((symbol, start_date, end_date))
        if symbol in self.failing_symbols:
            raise SecFilingsError(f"fixture failure for {symbol}")
        return SecDiscoveryCoverage(
            filings=tuple(self.filings_by_symbol.get(symbol, ())),
            complete=True,
            pages_read=1,
            next_page=None,
        )

    def fetch_primary_document(self, filing):
        self.fetches.append(filing.accession_number)
        if filing.symbol in self.failing_fetch_symbols:
            raise SecFilingsError(f"fixture text failure for {filing.symbol}")
        text = f"Official source text for {filing.accession_number}."
        return SecFilingContent(
            excerpt=text,
            excerpt_sha256=hashlib.sha256(text.encode()).hexdigest(),
            truncated=False,
        )


def _filing(symbol: str, accession: str, accepted_at: datetime) -> DiscoveredSecFiling:
    return DiscoveredSecFiling(
        cik="0000320193",
        accession_number=accession,
        form="8-K",
        filed_at=accepted_at.date(),
        accepted_at=accepted_at.isoformat().replace("+00:00", "Z"),
        primary_document="report.htm",
        source_url=f"https://www.sec.gov/Archives/edgar/data/320193/{accession.replace('-', '')}/report.htm",
    )


def _root(*, symbol: str, created_at: datetime, decision_at: datetime | None = None, target_end: date | None = None):
    contract = create_root_contract(
        anchor_date=created_at.date(),
        anchor_close=100.0,
        price_source="fixture",
        price_version="fixture-v1",
        price_hash="a" * 64,
        price_basis_metadata={
            "basis": "provider_quote_close_v1",
            "provider_behavior_verified": True,
            "adjusted_close_present": False,
            "corporate_actions": (),
        },
    ).as_dict()
    if target_end is not None:
        contract["target_end_date"] = target_end.isoformat()
    job = ForecastJobV2(
        id=uuid4(),
        symbol=symbol,
        kind="new",
        source_refs=[],
        status="succeeded",
        current_stage="succeeded",
        attempts=[],
        idempotency_key=f"monitor-v2-root-{uuid4()}",
        request_fingerprint="a" * 64,
        lease_epoch=0,
    )
    root_id = uuid4()
    version = ForecastVersionV2(
        id=root_id,
        root_id=root_id,
        job_id=job.id,
        parent_version_id=None,
        version_no=1,
        symbol=symbol,
        target_contract=contract,
        target_contract_hash=hashlib.sha256(str(contract).encode()).hexdigest(),
        decision_at=decision_at or created_at,
        market_cutoff_at=created_at - timedelta(minutes=1),
        price_input_manifest={"fixture": True},
        evidence_version_manifest=[],
        feature_snapshot={},
        baseline_probabilities=None,
        joint_probabilities=None,
        model_status="research_only",
        model_manifest={"fixture": True},
        trigger_type="manual",
        created_at=created_at,
    )
    return job, version


def _fetched_source(*, symbol: str, filing: DiscoveredSecFiling, observed_at: datetime) -> SecFilingInventory:
    text = "Official source text."
    return SecFilingInventory(
        symbol=symbol,
        cik=filing.cik,
        accession_number=filing.accession_number,
        form=filing.form,
        filed_at=filing.filed_at,
        accepted_at=filing.accepted_at,
        primary_document=filing.primary_document,
        source_url=filing.source_url,
        source="sec-edgar",
        review_status="pending_review",
        observed_at=observed_at,
        content_status="fetched",
        content_observed_at=observed_at,
        content_excerpt=text,
        content_excerpt_sha256=hashlib.sha256(text.encode()).hexdigest(),
        content_truncated=False,
        content_source_url=filing.source_url,
        content_document_name=filing.primary_document,
        content_kind="primary_document",
        related_attachment_status="not_found",
    )


def _clear_monitor_runs() -> None:
    with SessionLocal() as db:
        db.query(OfficialMonitorRunV2).delete(synchronize_session=False)
        db.commit()


def test_persisted_monitor_queues_exact_72_hour_fetched_source_once(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    filing = _filing("AAPL", "0000320193-26-000101", NOW - timedelta(hours=1))
    provider = _Provider({"AAPL": [filing]})
    with SessionLocal() as db:
        job, root = _root(symbol="AAPL", created_at=NOW - timedelta(hours=72))
        db.add_all((job, root, _fetched_source(symbol="AAPL", filing=filing, observed_at=NOW)))
        db.commit()
        first = run_once(db=db, provider=provider, observed_at=NOW, received_at_factory=lambda: NOW)
        repeated = run_once(
            db=db, provider=provider, observed_at=NOW + timedelta(hours=1),
            received_at_factory=lambda: NOW + timedelta(hours=1),
        )
        jobs = list(
            db.query(ForecastJobV2)
            .filter(ForecastJobV2.kind == "automatic_revision", ForecastJobV2.root_version_id == root.id)
            .all()
        )
        runs = list(db.query(OfficialMonitorRunV2).filter(OfficialMonitorRunV2.id.in_((first.run_id, repeated.run_id))))

    assert first.status == "succeeded"
    assert next(item for item in first.symbols if item.symbol == "AAPL").queued_count == 1
    assert repeated.status == "succeeded"
    assert next(item for item in repeated.symbols if item.symbol == "AAPL").queued_count == 0
    assert len(jobs) == 1
    assert jobs[0].source_refs == [{"source_type": "official_filing", "source_id": str(jobs[0].source_refs[0]["source_id"])}]
    assert len(runs) == 2
    assert all(run.completed_at is not None and run.next_due_at is not None for run in runs)
    assert provider.calls[5][1] == NOW.date() - timedelta(days=1)


def test_failed_symbol_retains_its_watermark_while_other_symbols_advance(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    first_provider = _Provider(failing_symbols={"MSFT"})
    with SessionLocal() as db:
        first = run_once(db=db, provider=first_provider, observed_at=NOW, received_at_factory=lambda: NOW)
        stored_first = db.get(OfficialMonitorRunV2, first.run_id)
        assert stored_first is not None
        assert "AAPL" in stored_first.last_success_watermark
        assert "MSFT" not in stored_first.last_success_watermark

        second_provider = _Provider()
        second = run_once(
            db=db, provider=second_provider, observed_at=NOW + timedelta(hours=1),
            received_at_factory=lambda: NOW + timedelta(hours=1),
        )
        stored_second = db.get(OfficialMonitorRunV2, second.run_id)

    assert first.status == "partial"
    assert second.status == "succeeded"
    starts = {symbol: start for symbol, start, _end in second_provider.calls}
    assert starts["AAPL"] == NOW.date() - timedelta(days=1)
    assert starts["MSFT"] == (NOW + timedelta(hours=1)).date() - timedelta(days=3)
    assert stored_second is not None and "MSFT" in stored_second.last_success_watermark


def test_monitor_rejects_old_or_expired_roots(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    check_at = NOW + timedelta(days=6)
    old_filing = _filing("AAPL", "0000320193-26-000201", check_at - timedelta(hours=1))
    expired_filing = _filing("GOOGL", "0000320193-26-000203", check_at - timedelta(hours=1))
    provider = _Provider({"AAPL": [old_filing], "GOOGL": [expired_filing]})
    with SessionLocal() as db:
        root_a_job, root_a = _root(symbol="AAPL", created_at=check_at - timedelta(hours=73))
        root_g_job, root_g = _root(
            symbol="GOOGL", created_at=check_at - timedelta(hours=1), target_end=date(2026, 9, 10)
        )
        db.add_all((root_a_job, root_a, root_g_job, root_g))
        db.add(_fetched_source(symbol="AAPL", filing=old_filing, observed_at=check_at))
        db.add(_fetched_source(symbol="GOOGL", filing=expired_filing, observed_at=check_at))
        db.commit()
        result = run_once(db=db, provider=provider, observed_at=check_at, received_at_factory=lambda: check_at)
        automatic = (
            db.query(ForecastJobV2)
            .filter(
                ForecastJobV2.kind == "automatic_revision",
                ForecastJobV2.root_version_id.in_((root_a.id, root_g.id)),
            )
            .count()
        )

    assert result.status == "succeeded"
    assert automatic == 0
    by_symbol = {item.symbol: item for item in result.symbols}
    assert by_symbol["AAPL"].skipped_count == 1
    assert by_symbol["GOOGL"].skipped_count == 1


def test_new_filing_fetches_text_before_queueing_and_fetch_failure_retries(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    check_at = NOW + timedelta(days=12)
    filing = _filing("AAPL", "0000320193-26-000301", check_at - timedelta(hours=1))
    with SessionLocal() as db:
        root_job, root = _root(symbol="AAPL", created_at=check_at - timedelta(hours=2))
        db.add_all((root_job, root))
        db.commit()
        failing = _Provider({"AAPL": [filing]}, failing_fetch_symbols={"AAPL"})
        first = run_once(
            db=db,
            provider=failing,
            observed_at=check_at,
            received_at_factory=lambda: check_at,
        )
        failed_run = db.get(OfficialMonitorRunV2, first.run_id)
        recovered = _Provider({"AAPL": [filing]})
        second = run_once(
            db=db,
            provider=recovered,
            observed_at=check_at + timedelta(hours=1),
            received_at_factory=lambda: check_at + timedelta(hours=1),
        )
        jobs = list(
            db.query(ForecastJobV2)
            .filter(ForecastJobV2.kind == "automatic_revision", ForecastJobV2.root_version_id == root.id)
        )

    assert first.status == "partial"
    assert failed_run is not None and "AAPL" not in failed_run.last_success_watermark
    assert failing.fetches == [filing.accession_number]
    assert second.status == "succeeded"
    assert recovered.fetches == [filing.accession_number]
    assert len(jobs) == 1 and jobs[0].status == "queued"


def test_blocked_automatic_job_gets_one_idempotent_monitor_recovery(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    check_at = NOW + timedelta(days=18)
    filing = _filing("AAPL", "0000320193-26-000401", check_at - timedelta(hours=1))
    provider = _Provider({"AAPL": [filing]})
    with SessionLocal() as db:
        root_job, root = _root(symbol="AAPL", created_at=check_at - timedelta(hours=2))
        source = _fetched_source(symbol="AAPL", filing=filing, observed_at=check_at)
        db.add_all((root_job, root, source))
        db.commit()
        first = run_once(db=db, provider=provider, observed_at=check_at, received_at_factory=lambda: check_at)
        job = (
            db.query(ForecastJobV2)
            .filter(ForecastJobV2.kind == "automatic_revision", ForecastJobV2.root_version_id == root.id)
            .one()
        )
        job.status = "blocked_data"
        job.current_stage = "blocked_data"
        job.completed_at = check_at + timedelta(minutes=1)
        job.attempts = [{"epoch": 1, "started_at": check_at.isoformat()}]
        db.commit()
        second = run_once(
            db=db, provider=provider, observed_at=check_at + timedelta(hours=1),
            received_at_factory=lambda: check_at + timedelta(hours=1),
        )
        db.refresh(job)
        matching = list(
            db.query(ForecastJobV2)
            .filter(ForecastJobV2.kind == "automatic_revision", ForecastJobV2.root_version_id == root.id)
        )

    assert first.status == "succeeded"
    assert next(item for item in second.symbols if item.symbol == "AAPL").queued_count == 1
    assert job.status == "queued"
    assert len(matching) == 1


def test_new_text_uses_its_actual_receive_time_not_the_scan_start(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    check_at = NOW + timedelta(days=20)
    received_at = check_at + timedelta(minutes=2)
    filing = _filing("AAPL", "0000320193-26-000501", check_at - timedelta(minutes=1))
    provider = _Provider({"AAPL": [filing]})
    clock_values = iter(
        (check_at + timedelta(minutes=1), received_at, received_at, received_at, received_at, received_at,
         received_at + timedelta(seconds=1))
    )
    with SessionLocal() as db:
        root_job, root = _root(symbol="AAPL", created_at=check_at - timedelta(hours=2))
        db.add_all((root_job, root))
        db.commit()
        result = run_once(
            db=db,
            provider=provider,
            observed_at=check_at,
            received_at_factory=lambda: next(clock_values),
        )
        source = db.query(SecFilingInventory).filter_by(accession_number=filing.accession_number).one()
        jobs = list(
            db.query(ForecastJobV2)
            .filter(ForecastJobV2.kind == "automatic_revision", ForecastJobV2.root_version_id == root.id)
        )

    assert result.status == "succeeded"
    assert source.content_observed_at == received_at
    assert len(jobs) == 1


def test_same_stock_sources_are_merged_into_one_automatic_job(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    check_at = NOW + timedelta(days=26)
    first = _filing("AAPL", "0000320193-26-000601", check_at - timedelta(hours=2))
    second = _filing("AAPL", "0000320193-26-000602", check_at - timedelta(hours=1))
    provider = _Provider({"AAPL": [first, second]})
    with SessionLocal() as db:
        root_job, root = _root(symbol="AAPL", created_at=check_at - timedelta(hours=3))
        db.add_all(
            (
                root_job,
                root,
                _fetched_source(symbol="AAPL", filing=first, observed_at=check_at),
                _fetched_source(symbol="AAPL", filing=second, observed_at=check_at),
            )
        )
        db.commit()
        result = run_once(db=db, provider=provider, observed_at=check_at, received_at_factory=lambda: check_at)
        jobs = list(
            db.query(ForecastJobV2)
            .filter(ForecastJobV2.kind == "automatic_revision", ForecastJobV2.root_version_id == root.id)
        )

    assert next(item for item in result.symbols if item.symbol == "AAPL").queued_count == 1
    assert len(jobs) == 1 and len(jobs[0].source_refs) == 2


def test_concurrent_monitor_lock_records_a_visible_non_scanning_run(disposable_database):
    Base.metadata.create_all(bind=engine)
    _clear_monitor_runs()
    with engine.connect() as lock_connection:
        lock_connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": MONITOR_LOCK_KEY})
        with SessionLocal() as db:
            result = run_once(db=db, provider=_Provider(), observed_at=NOW, received_at_factory=lambda: NOW)
            stored = db.get(OfficialMonitorRunV2, result.run_id)
        lock_connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": MONITOR_LOCK_KEY})

    assert result.status == "failed" and result.symbols == ()
    assert stored is not None and stored.error_summary["monitor"] == "another V2 monitor invocation is running"
