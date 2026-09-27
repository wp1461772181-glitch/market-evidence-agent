from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from exchange_calendars import get_calendar

from app.agent_forecast_processor import AgentForecastProcessor
from app.database import Base, SessionLocal, engine
from app.forecast_contract import future_xnys_sessions
from app.forecast_jobs import enqueue_job
from app.forecast_v2_models import EvidenceEventVersionV2, ForecastJobV2, ForecastVersionV2
from app.forecast_worker import run_once
from app.market_data import DailyPrice, MarketDataFetchResult, PriceBasisMetadata, YAHOO_SOURCE
from app.models import SecFilingInventory
from app.research_brief import build_research_brief

ROOT_AT = datetime(2026, 9, 1, 22, tzinfo=UTC)
AUTO_AT = datetime(2026, 9, 2, 22, tzinfo=UTC)
MANUAL_AT = datetime(2026, 9, 2, 22, 30, tzinfo=UTC)


@dataclass(frozen=True)
class _ObservedPrice:
    symbol: str
    trading_date: date
    close: float
    volume: int
    open: float
    high: float
    low: float
    source: str
    content_hash: str
    revision_number: int
    available_at: datetime
    observed_at: datetime


class _MarketProvider:
    def __init__(self, rows_by_symbol):
        self.rows_by_symbol = rows_by_symbol

    def fetch_daily_prices_with_metadata(self, symbol, start_date, end_date):
        rows = tuple(
            DailyPrice(
                symbol=row.symbol,
                trading_date=row.trading_date,
                close=row.close,
                volume=row.volume,
                open=row.open,
                high=row.high,
                low=row.low,
            )
            for row in self.rows_by_symbol[symbol]
            if start_date <= row.trading_date <= end_date
        )
        return MarketDataFetchResult(
            prices=rows,
            price_basis=PriceBasisMetadata(
                adjusted_close_present=True,
                provider_behavior_verified=True,
                corporate_actions_available=True,
                corporate_actions_response_shape="events_object",
            ),
        )


class _BriefProvider:
    def extract(self, **_kwargs):
        return SimpleNamespace(
            content=json.dumps({
                "new_facts": [], "supporting": [], "counter": [], "background": [],
                "conflicts": [], "unknowns": [], "changes": [],
            }),
            response_model="fixture-brief-model",
            usage={},
        )


class _JevProvider:
    def __init__(self):
        self.calls = []

    def evaluate(self, brief):
        self.calls.append(brief)
        index = len(self.calls)
        return SimpleNamespace(
            probabilities={"bearish": 0.1, "neutral": 0.2 + index * 0.01, "bullish": 0.7 - index * 0.01},
            choice="bullish",
            confidence=0.7,
            requested_model="typesafe/jev-1.13",
            actual_model="typesafe/jev-1.13",
            request_id=f"fixture-jev-{index}",
            usage={"fixture": True},
            latency_ms=1,
            question_version="jev-direction-v1",
            input_sha256=f"{index:064x}",
        )


def _market_rows() -> dict[str, list[_ObservedPrice]]:
    calendar = get_calendar("XNYS")
    sessions = calendar.sessions_in_range("2026-07-01", "2026-09-02")
    result: dict[str, list[_ObservedPrice]] = {"AAPL": [], "SPY": []}
    for ticker in result:
        for index, session in enumerate(sessions):
            trading_date = session.date()
            close = 100.0 + index + (5.0 if ticker == "SPY" else 0.0)
            close_time = datetime.combine(trading_date, datetime.min.time(), UTC) + timedelta(hours=20)
            result[ticker].append(
                _ObservedPrice(
                    symbol=ticker,
                    trading_date=trading_date,
                    close=close,
                    volume=1_000_000 + index,
                    open=close - 0.5,
                    high=close + 1.0,
                    low=close - 1.0,
                    source=YAHOO_SOURCE,
                    content_hash=hashlib.sha256(f"{ticker}:{trading_date}".encode()).hexdigest(),
                    revision_number=1,
                    available_at=close_time,
                    observed_at=close_time + timedelta(minutes=30),
                )
            )
    return result


def _source(*, accepted_at: datetime, observed_at: datetime, content: str) -> UUID:
    with SessionLocal() as db:
        row = SecFilingInventory(
            symbol="AAPL",
            cik="0000320193",
            accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
            form="10-Q",
            filed_at=accepted_at.date(),
            accepted_at=accepted_at.isoformat(),
            primary_document="report.htm",
            source_url="https://www.sec.gov/Archives/example/report.htm",
            source="sec-edgar",
            review_status="pending_review",
            observed_at=observed_at,
            content_status="fetched",
            content_observed_at=observed_at,
            content_excerpt=content,
            content_excerpt_sha256=hashlib.sha256(content.encode()).hexdigest(),
            content_truncated=False,
            content_source_url="https://www.sec.gov/Archives/example/report.htm",
            content_document_name="report.htm",
            content_kind="primary_document",
            related_attachment_status="not_applicable",
        )
        db.add(row)
        db.commit()
        return row.id


def _agent_processor(*, now: datetime, rows_by_symbol, jev, analyses, briefs):
    def analysis_requester(*, source_type, source_id, **_kwargs):
        key = (source_type, str(source_id))
        if key not in analyses:
            with SessionLocal() as db:
                source = db.get(SecFilingInventory, source_id)
                analysis_id = uuid4()
                analyses[key] = {
                    "analysis_id": str(analysis_id),
                    "evidence_version_id": f"fixture-evidence-{source_id}",
                    "version_no": 1,
                    "status": "succeeded",
                    "payload": {
                        "schema_version": "material-analysis-v1",
                        "prompt_version": "material-analysis-prompt-v1",
                        "summary": "Frozen source summary.",
                        "facts": [], "supporting": [], "counter": [], "uncertainties": [], "key_numbers": [],
                    },
                    "source_manifest": {
                        "source_type": source_type,
                        "source_id": str(source_id),
                        "symbol": source.symbol,
                        "title": "Fixture filing",
                        "source_url": source.source_url,
                        "published_at": source.accepted_at,
                        "observed_at": source.content_observed_at.isoformat(),
                        "content_sha256": source.content_excerpt_sha256,
                        "analysis_text_sha256": hashlib.sha256(source.content_excerpt.encode()).hexdigest(),
                        "coverage": "fetched_excerpt",
                        "coverage_incomplete": False,
                        "truncated": False,
                    },
                }
        return {"status": "succeeded", "analysis_id": analyses[key]["analysis_id"]}

    def brief_builder(**kwargs):
        briefs.append(kwargs.get("parent_brief"))
        return build_research_brief(**kwargs)

    return AgentForecastProcessor(
        session_factory=SessionLocal,
        decision_mode="jev",
        market_provider_factory=lambda: _MarketProvider(rows_by_symbol),
        now_factory=lambda: now,
        market_ingester=lambda *_args, **_kwargs: None,
        market_loader=lambda symbol, *, as_of_time, **_kwargs: [
            row for row in rows_by_symbol[symbol]
            if row.trading_date <= as_of_time.date() and row.observed_at <= as_of_time
        ],
        analysis_requester=analysis_requester,
        analysis_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fixture analysis is already complete")),
        analysis_getter=lambda *, analysis_id, **_kwargs: next(
            row for row in analyses.values() if row["analysis_id"] == str(analysis_id)
        ),
        brief_provider_factory=_BriefProvider,
        brief_builder=brief_builder,
        jev_provider_factory=lambda: jev,
    )


def _cleanup(job_ids: list[UUID], source_ids: list[UUID]) -> None:
    with SessionLocal() as db:
        jobs = [db.get(ForecastJobV2, item) for item in job_ids]
        for job in jobs:
            if job is not None:
                job.root_version_id = None
                job.parent_version_id = None
                job.result_version_id = None
        db.flush()
        versions = list(
            db.query(ForecastVersionV2)
            .filter(ForecastVersionV2.job_id.in_(job_ids))
            .order_by(ForecastVersionV2.version_no.desc())
            .all()
        )
        for version in versions:
            db.delete(version)
            db.flush()
        for job in jobs:
            if job is not None:
                db.delete(job)
        db.query(EvidenceEventVersionV2).filter(EvidenceEventVersionV2.source_id.in_(source_ids)).delete(
            synchronize_session=False
        )
        db.query(SecFilingInventory).filter(SecFilingInventory.id.in_(source_ids)).delete(synchronize_session=False)
        db.commit()


def test_jev_agent_revision_keeps_the_root_target_and_preserves_manual_branch(disposable_database, monkeypatch):
    Base.metadata.create_all(bind=engine)
    import app.forecast_worker as forecast_worker

    real_publish = forecast_worker.publish_forecast_version

    def publish_at_fixture_time(*, db, job_id, **kwargs):
        job = db.get(ForecastJobV2, job_id)
        fixture_time = {"new": ROOT_AT, "automatic_revision": AUTO_AT, "manual_revision": MANUAL_AT}[job.kind]
        return real_publish(db=db, job_id=job_id, now=fixture_time, **kwargs)

    monkeypatch.setattr(forecast_worker, "publish_forecast_version", publish_at_fixture_time)
    rows_by_symbol = _market_rows()
    initial_source = _source(
        accepted_at=ROOT_AT - timedelta(hours=2),
        observed_at=ROOT_AT - timedelta(hours=1),
        content="Initial quarter report.",
    )
    automatic_source = _source(
        accepted_at=AUTO_AT - timedelta(hours=2),
        observed_at=AUTO_AT - timedelta(hours=1),
        content="New official update for automatic monitoring.",
    )
    manual_source = _source(
        accepted_at=MANUAL_AT - timedelta(hours=2),
        observed_at=MANUAL_AT - timedelta(hours=1),
        content="Additional source selected by the user.",
    )
    sources = [initial_source, automatic_source, manual_source]
    jobs: list[UUID] = []
    analyses: dict[tuple[str, str], dict] = {}
    briefs: list[dict | None] = []
    jev = _JevProvider()

    def submit(kind: str, *, now: datetime, parent: ForecastVersionV2 | None, refs: list[UUID]):
        with SessionLocal() as db:
            job = enqueue_job(
                db=db,
                symbol="AAPL",
                kind=kind,
                idempotency_key=f"jev-revision-{uuid4()}",
                source_refs=[{"source_type": "official_filing", "source_id": str(item)} for item in refs],
                root_version_id=parent.root_id if parent else None,
                parent_version_id=parent.id if parent else None,
            )
            jobs.append(job.id)
            return run_once(
                job_id=job.id,
                worker_id=f"jev-revision-{kind}",
                processor=_agent_processor(now=now, rows_by_symbol=rows_by_symbol, jev=jev, analyses=analyses, briefs=briefs),
            )

    try:
        root_result = submit("new", now=ROOT_AT, parent=None, refs=[initial_source])
        with SessionLocal() as db:
            root_job = db.get(ForecastJobV2, jobs[-1])
            assert root_result["status"] == "succeeded", {
                **root_result, "error_type": root_job.error_type, "error_message": root_job.error_message,
            }
        with SessionLocal() as db:
            root = db.get(ForecastVersionV2, db.get(ForecastJobV2, jobs[-1]).result_version_id)
            assert root is not None
            original_contract = dict(root.target_contract)
            assert original_contract["anchor_date"] == "2026-09-01"
            assert original_contract["horizon_sessions"] == 20
            assert original_contract["target_end_date"] == future_xnys_sessions(date(2026, 9, 1), 20)[-1].isoformat()
            original_root_probabilities = dict(root.decision_probabilities)

        auto_result = submit("automatic_revision", now=AUTO_AT, parent=root, refs=[automatic_source])
        with SessionLocal() as db:
            auto_job = db.get(ForecastJobV2, jobs[-1])
            assert auto_result["status"] == "succeeded", {
                **auto_result, "error_type": auto_job.error_type, "error_message": auto_job.error_message,
            }
        with SessionLocal() as db:
            automatic = db.get(ForecastVersionV2, db.get(ForecastJobV2, jobs[-1]).result_version_id)
            stored_root = db.get(ForecastVersionV2, root.id)
            assert automatic is not None and stored_root is not None
            automatic_probabilities = dict(automatic.decision_probabilities)
            assert automatic.parent_version_id == root.id
            assert automatic.target_contract == original_contract
            assert automatic.feature_snapshot["remaining_sessions"] == 19
            assert stored_root.target_contract == original_contract
            assert stored_root.decision_probabilities == original_root_probabilities

        manual_result = submit("manual_revision", now=MANUAL_AT, parent=root, refs=[manual_source])
        assert manual_result["status"] == "succeeded"
        with SessionLocal() as db:
            manual = db.get(ForecastVersionV2, db.get(ForecastJobV2, jobs[-1]).result_version_id)
            automatic_after_manual = db.get(ForecastVersionV2, automatic.id)
            assert manual is not None and automatic_after_manual is not None
            assert manual.parent_version_id == root.id
            assert manual.target_contract == original_contract
            assert manual.feature_snapshot["remaining_sessions"] == 19
            assert automatic_after_manual.decision_probabilities == automatic_probabilities
            assert automatic_after_manual.target_contract == original_contract
            assert db.query(ForecastVersionV2).filter_by(root_id=root.id).count() == 3

        assert jev.calls and len(jev.calls) == 3
        assert briefs[0] is None
        assert briefs[1] == root.research_brief
        assert briefs[2] == root.research_brief
        assert set(jev.calls[1]["material_refs"][i]["source_id"] for i in range(len(jev.calls[1]["material_refs"]))) == {
            str(initial_source), str(automatic_source)
        }
        assert briefs[1]["target_contract"] == original_contract
        assert briefs[2]["target_contract"] == original_contract
    finally:
        _cleanup(jobs, sources)
