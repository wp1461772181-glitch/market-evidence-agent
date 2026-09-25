from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.forecast_contract import future_xnys_sessions
from app.forecast_jobs import enqueue_job
from app.forecast_v2_models import EvidenceEventVersionV2, ForecastJobV2, ForecastVersionV2
from app.forecast_v2_processor import ResearchOnlyForecastProcessor
from app.forecast_worker import run_once
from app.market_data import CorporateAction, DailyPrice, MarketDataFetchResult, PriceBasisMetadata
from app.forecast_v2_processor import _json_safe
from app.models import SecFilingInventory


@dataclass(frozen=True)
class _Revision:
    symbol: str
    trading_date: date
    close: float
    volume: int
    source: str = "yahoo-finance-chart"
    open: float = 100.0
    high: float = 102.0
    low: float = 99.0
    content_hash: str = "a" * 64
    revision_number: int = 1
    available_at: datetime = datetime(2025, 1, 1, tzinfo=UTC)
    observed_at: datetime = datetime(2025, 1, 1, tzinfo=UTC)


class _Provider:
    def __init__(self, rows_by_symbol):
        self._rows_by_symbol = rows_by_symbol

    def fetch_daily_prices_with_metadata(self, symbol, _start_date, _end_date):
        return MarketDataFetchResult(
            prices=tuple(self._rows_by_symbol[symbol]),
            price_basis=PriceBasisMetadata(
                adjusted_close_present=True,
                provider_behavior_verified=True,
                corporate_actions_available=True,
                corporate_actions_response_shape="events_object",
            ),
        )


def _rows(*, anchor: date, symbol: str, observed_at: datetime):
    dates = [anchor, *future_xnys_sessions(anchor, 21)]
    result = []
    for index, trading_date in enumerate(dates):
        close = 100.0 + index + (5.0 if symbol == "SPY" else 0.0)
        result.append(
            _Revision(
                symbol=symbol,
                trading_date=trading_date,
                close=close,
                volume=1_000_000 + index,
                open=close - 0.5,
                high=close + 1,
                low=close - 1,
                content_hash=hashlib.sha256(f"{symbol}:{trading_date}".encode()).hexdigest(),
                available_at=observed_at - timedelta(minutes=1),
                observed_at=observed_at - timedelta(seconds=30),
            )
        )
    return result


def _filing(*, symbol: str, accepted_at: datetime, observed_at: datetime, text: str):
    with SessionLocal() as db:
        row = SecFilingInventory(
            symbol=symbol,
            cik="0000320193",
            accession_number=f"0000320193-25-{uuid4().int % 1_000_000:06d}",
            form="8-K",
            filed_at=accepted_at.date(),
            accepted_at=accepted_at.isoformat(),
            primary_document="release.htm",
            source_url="https://www.sec.gov/Archives/example/release.htm",
            source="sec-edgar",
            review_status="pending_review",
            observed_at=observed_at,
            content_status="fetched",
            content_observed_at=observed_at,
            content_excerpt=text,
            content_excerpt_sha256=hashlib.sha256(text.encode()).hexdigest(),
            content_truncated=False,
            content_error=None,
            content_source_url="https://www.sec.gov/Archives/example/release.htm",
            content_document_name="release.htm",
            content_kind="primary_document",
            related_attachment_status="not_applicable",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _processor(*, now: datetime, rows_by_symbol):
    def fake_research(**kwargs):
        context = kwargs["context"]
        return SimpleNamespace(
            id=uuid4(),
            status="succeeded",
            error=None,
            source_ids=[str(event.id) for event in context.events],
            source_snapshot=[{"source_id": str(event.id)} for event in context.events],
            report={
                "supporting": [{"claim": "Fixture supporting claim", "evidence_quote": "Revenue grew."}],
                "counter": [{"claim": "Fixture counter claim", "evidence_quote": "Costs rose."}],
            },
        )

    return ResearchOnlyForecastProcessor(
        session_factory=SessionLocal,
        market_provider_factory=lambda: _Provider(
            {
                ticker: [
                    DailyPrice(
                        symbol=row.symbol,
                        trading_date=row.trading_date,
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        volume=row.volume,
                    )
                    for row in ticker_rows
                ]
                for ticker, ticker_rows in rows_by_symbol.items()
            }
        ),
        now_factory=lambda: now,
        market_ingester=lambda *_args, **_kwargs: None,
        market_loader=lambda ticker, **_kwargs: rows_by_symbol[ticker],
        research_runner=fake_research,
        research_model_factory=lambda: "fixture-model",
    )


def _clean_owned_rows(*, source_ids, job_ids):
    """Keep this session-scoped database isolated from sibling test modules."""
    with SessionLocal() as db:
        jobs = [db.get(ForecastJobV2, job_id) for job_id in job_ids]
        for job in jobs:
            if job is not None:
                job.result_version_id = None
                job.root_version_id = None
                job.parent_version_id = None
        db.flush()
        versions = list(
            db.scalars(
                select(ForecastVersionV2)
                .where(ForecastVersionV2.job_id.in_(job_ids))
                .order_by(ForecastVersionV2.version_no.desc())
            )
        )
        # ``root_id`` is a self-FK, so PostgreSQL must see each child removal
        # before it can remove its root.
        for version in versions:
            db.delete(version)
            db.flush()
        for job in jobs:
            if job is not None:
                db.delete(job)
        for event in db.scalars(
            select(EvidenceEventVersionV2).where(EvidenceEventVersionV2.source_id.in_(source_ids))
        ):
            db.delete(event)
        for source_id in source_ids:
            source = db.get(SecFilingInventory, source_id)
            if source is not None:
                db.delete(source)
        db.commit()


def test_real_input_processor_freezes_observed_sources_and_leaves_probabilities_empty(disposable_database):
    Base.metadata.create_all(bind=engine)
    decision_at = datetime(2025, 3, 4, 22, tzinfo=UTC)
    eligible = _filing(
        symbol="AAPL",
        accepted_at=decision_at - timedelta(hours=2),
        observed_at=decision_at - timedelta(hours=1),
        text="Revenue grew.",
    )
    # This document is public in the fixture but was not received before the
    # cutoff, so the observed workflow must not let it leak into the version.
    late = _filing(
        symbol="AAPL",
        accepted_at=decision_at - timedelta(days=1),
        observed_at=decision_at + timedelta(minutes=1),
        text="Costs rose.",
    )
    rows_by_symbol = {
        "AAPL": _rows(anchor=date(2025, 1, 31), symbol="AAPL", observed_at=decision_at),
        "SPY": _rows(anchor=date(2025, 1, 31), symbol="SPY", observed_at=decision_at),
    }
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol="AAPL", kind="new", idempotency_key=f"processor-root-{uuid4()}")
        job_id = job.id

    try:
        result = run_once(job_id=job_id, worker_id="processor-root", processor=_processor(now=decision_at, rows_by_symbol=rows_by_symbol))
        assert result["status"] == "succeeded"
        with SessionLocal() as db:
            job = db.get(ForecastJobV2, job_id)
            version = db.get(ForecastVersionV2, job.result_version_id)
            assert version is not None
            assert version.baseline_probabilities is None
            assert version.joint_probabilities is None
            assert version.model_status == "research_only"
            assert version.research_report["research_conclusions_status"] == "ready"
            assert version.price_input_manifest["latest_completed_session"] == "2025-03-04"
            selected_ids = {item["source_id"] for item in version.evidence_version_manifest}
            assert selected_ids == {str(eligible)}
            assert str(late) not in selected_ids
    finally:
        _clean_owned_rows(source_ids=[eligible, late], job_ids=[job_id])


def test_manual_revision_inherits_parent_and_appends_new_observed_source(disposable_database):
    Base.metadata.create_all(bind=engine)
    root_time = datetime(2025, 3, 4, 22, tzinfo=UTC)
    initial = _filing(
        symbol="MSFT", accepted_at=root_time - timedelta(hours=2), observed_at=root_time - timedelta(hours=1), text="Revenue grew."
    )
    root_rows = {
        "MSFT": _rows(anchor=date(2025, 1, 31), symbol="MSFT", observed_at=root_time),
        "SPY": _rows(anchor=date(2025, 1, 31), symbol="SPY", observed_at=root_time),
    }
    with SessionLocal() as db:
        root_job = enqueue_job(db=db, symbol="MSFT", kind="new", idempotency_key=f"processor-parent-{uuid4()}")
        root_job_id = root_job.id
    revision_job_id = None
    new_source = None
    try:
        assert run_once(job_id=root_job_id, worker_id="processor-parent", processor=_processor(now=root_time, rows_by_symbol=root_rows))["status"] == "succeeded"

        revision_time = datetime(2025, 3, 5, 22, tzinfo=UTC)
        new_source = _filing(
            symbol="MSFT", accepted_at=revision_time - timedelta(hours=2), observed_at=revision_time - timedelta(hours=1), text="Costs rose."
        )
        revision_rows = {
            "MSFT": _rows(anchor=date(2025, 2, 3), symbol="MSFT", observed_at=revision_time),
            "SPY": _rows(anchor=date(2025, 2, 3), symbol="SPY", observed_at=revision_time),
        }
        with SessionLocal() as db:
            root = db.get(ForecastVersionV2, db.get(ForecastJobV2, root_job_id).result_version_id)
            parent_manifest = list(root.evidence_version_manifest)
            revision_job = enqueue_job(
                db=db,
                symbol="MSFT",
                kind="manual_revision",
                idempotency_key=f"processor-child-{uuid4()}",
                root_version_id=root.id,
                parent_version_id=root.id,
                source_refs=[{"source_type": "official_filing", "source_id": str(new_source)}],
            )
            revision_job_id = revision_job.id
        result = run_once(
            job_id=revision_job_id,
            worker_id="processor-child",
            processor=_processor(now=revision_time, rows_by_symbol=revision_rows),
        )
        assert result["status"] == "succeeded"
        with SessionLocal() as db:
            root = db.get(ForecastVersionV2, db.get(ForecastJobV2, root_job_id).result_version_id)
            child = db.get(ForecastVersionV2, db.get(ForecastJobV2, revision_job_id).result_version_id)
            assert child.parent_version_id == root.id
            assert child.target_contract == root.target_contract
            assert root.evidence_version_manifest == parent_manifest
            assert {item["source_id"] for item in child.evidence_version_manifest} == {str(initial), str(new_source)}
            assert child.feature_snapshot["remaining_sessions"] == 19
            assert child.research_report["automatic_selection"]["backfill_events"] == 0
    finally:
        _clean_owned_rows(
            source_ids=[initial, *([new_source] if new_source is not None else [])],
            job_ids=[root_job_id, *([revision_job_id] if revision_job_id is not None else [])],
        )


def test_price_basis_metadata_with_corporate_action_dates_is_json_safe():
    metadata = PriceBasisMetadata(
        provider_behavior_verified=True,
        corporate_actions=(CorporateAction(kind="cash_dividend", effective_date=date(2025, 3, 7), known=True),),
    )
    rendered = _json_safe(asdict(metadata))
    assert rendered["corporate_actions"][0]["effective_date"] == "2025-03-07"
