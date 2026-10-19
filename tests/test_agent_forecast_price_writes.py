"""Confirmed forecast revisions and price initialization must report their real outcome.

Regression from the 2026-10-19 owner write suite: ``revise_forecast`` paired an AAPL version with
an MSFT filing, and the V2 API's HTTPException was swallowed into ``write_service_failed``;
``initialize_prices`` failed instantly because a function-local ``from datetime import datetime``
in another branch shadowed the module import, so the handler died with ``UnboundLocalError``
before the provider was called and wrote no rows.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.agent_workflow import consent, tools, workspace_tools
from app.agent_workflow.state import AgentRuntimeContext
from app.database import Base, SessionLocal, engine
from app.forecast_contract import latest_completed_xnys_session
from app.forecast_jobs import enqueue_job
from app.forecast_v2_models import ForecastJobV2, ForecastVersionV2
from app.market_data import DailyPrice, MarketDataError
from app.models import IngestionRun, MarketPrice, MarketPriceRevision, SecFilingInventory


def _runtime(conversation='forecast-price-write'):
    return AgentRuntimeContext('custom', 'test', 'dummy', identity_credential='owner',
                               owner_hash='o' * 64, conversation_id=conversation)


@pytest.fixture
def owner(monkeypatch, disposable_database):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(workspace_tools, 'owner_credential_matches', lambda credential: credential == 'owner')
    monkeypatch.setattr(tools, 'owner_credential_matches', lambda credential: credential == 'owner')
    return 'owner'


def _root_version(symbol='AAPL'):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol=symbol, kind='new', idempotency_key=f'price-write-{uuid.uuid4()}')
        version = ForecastVersionV2(
            id=uuid.uuid4(), root_id=uuid.uuid4(), job_id=job.id, version_no=1, symbol=symbol,
            target_contract={'target_end_date': (now.date() + timedelta(days=120)).isoformat()},
            target_contract_hash='a' * 64, decision_at=now - timedelta(hours=1),
            market_cutoff_at=now - timedelta(hours=2), price_input_manifest={},
            evidence_version_manifest=[], feature_snapshot={},
            baseline_probabilities={'bearish': 0.2, 'neutral': 0.4, 'bullish': 0.4},
            joint_probabilities=None, model_status='research_only', model_manifest={}, trigger_type='manual',
        )
        version.root_id = version.id
        db.add(version); db.commit(); db.refresh(version)
        return version.id, job.id


def _filing(symbol):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        source = SecFilingInventory(
            symbol=symbol, cik='0000789019', accession_number=f'0000789019-26-{uuid.uuid4().int % 1_000_000:06d}',
            form='10-K', filed_at=now.date(), accepted_at=(now - timedelta(hours=3)).isoformat(),
            primary_document='filing.htm', source_url='https://www.sec.gov/Archives/example/filing.htm',
            source='sec-edgar', review_status='accepted', human_review_note=None, reviewed_at=None,
            observed_at=now - timedelta(minutes=150), content_status='fetched',
            content_observed_at=now - timedelta(minutes=150), content_excerpt='fixture',
            content_excerpt_sha256='b' * 64, content_truncated=False, content_error=None,
        )
        db.add(source); db.commit(); db.refresh(source)
        return source.id


def _drop(version_id, job_id, filing_id):
    with SessionLocal() as db:
        db.query(SecFilingInventory).filter(SecFilingInventory.id == filing_id).delete(synchronize_session=False)
        db.query(ForecastVersionV2).filter(ForecastVersionV2.id == version_id).delete(synchronize_session=False)
        db.query(ForecastJobV2).filter(ForecastJobV2.id == job_id).delete(synchronize_session=False)
        db.commit()


def test_a_revision_source_from_another_symbol_keeps_its_own_reason(owner):
    version_id, job_id = _root_version('AAPL')
    filing_id = _filing('MSFT')
    operation = {'name': 'revise_forecast', 'arguments': {
        'version_id': str(version_id),
        'source_refs': [{'source_type': 'official_filing', 'source_id': str(filing_id)}]}}
    try:
        with pytest.raises(ValueError) as caught:
            workspace_tools.write_workspace('revise_forecast', operation['arguments'], 'revision-cross-symbol', _runtime())
        assert str(caught.value) == 'revision_source_symbol_mismatch'
        outcome = consent.failure_outcome(caught.value)
        assert outcome['error_code'] == 'revision_source_symbol_mismatch'
        assert '不属于这个预测版本的股票' in outcome['reason']
        assert 'MSFT' not in outcome['reason'] and 'write_service_failed' not in str(outcome)
    finally:
        _drop(version_id, job_id, filing_id)


@pytest.mark.parametrize('detail,code', [
    ('source does not belong to the forecast symbol', 'revision_source_symbol_mismatch'),
    ('forecast target has expired; create an evaluation instead', 'forecast_version_not_revisable'),
    ('forecast version was not found', 'forecast_version_not_found'),
    ('official filing source was not found', 'revision_source_not_found'),
    ('source is not available at this decision time', 'revision_source_unavailable'),
    ('manual revision source is unchanged from the selected forecast', 'revision_source_unchanged'),
    ('manual revision with this unchanged source snapshot is already queued; wait for it to finish',
     'revision_already_queued'),
])
def test_every_known_revision_rejection_maps_to_a_specific_code(detail, code):
    assert workspace_tools.revision_rejection(HTTPException(422, detail)) == code
    assert consent.failure_outcome(ValueError(code))['error_code'] == code


def test_an_unknown_revision_detail_is_not_guessed():
    assert workspace_tools.revision_rejection(HTTPException(422, 'something new')) is None


def test_confirmed_price_initialization_writes_rows_instead_of_dying_before_the_provider(owner, monkeypatch):
    from app import market_data_ingestion
    source = 'test-init-prices-' + uuid.uuid4().hex
    as_of_time = datetime.now(UTC)
    trading_date = latest_completed_xnys_session(as_of_time)
    rows = [DailyPrice(symbol='AMD', trading_date=trading_date, open=100.0, high=102.0,
                       low=99.0, close=101.0, volume=1_000_000, source=source)]
    real = market_data_ingestion.ingest_market_data

    def fake_ingest(symbols, start_date, end_date, as_of, **kwargs):
        return real(symbols, start_date, end_date, as_of, source=source,
                    fetcher=lambda *_: rows, now_factory=lambda: as_of, **kwargs)

    monkeypatch.setattr(market_data_ingestion, 'ingest_market_data', fake_ingest)
    outcome = tools.execute_costly(
        {'name': 'initialize_prices', 'arguments': {'symbol': 'AMD', 'days': 366}}, 'initialize-prices', _runtime())
    try:
        assert outcome['ok'] is True and outcome['inserted'] == 1
        with SessionLocal() as db:
            stored = db.scalar(select(func.count()).select_from(MarketPrice).where(
                MarketPrice.symbol == 'AMD', MarketPrice.source == source))
            run = db.get(IngestionRun, uuid.UUID(outcome['run_id']))
            assert stored == 1 and run.status == 'completed'
    finally:
        with SessionLocal() as db:
            db.query(MarketPriceRevision).filter(MarketPriceRevision.source == source).delete(synchronize_session=False)
            db.query(MarketPrice).filter(MarketPrice.source == source).delete(synchronize_session=False)
            db.query(IngestionRun).filter(IngestionRun.id == uuid.UUID(outcome['run_id'])).delete(synchronize_session=False)
            db.commit()


def test_a_market_data_outage_is_named_not_hidden():
    outcome = consent.failure_outcome(MarketDataError('Yahoo Finance request failed for AMD: HTTP Error 429: Too Many Requests'))
    assert outcome['error_code'] == 'market_data_unavailable'
    assert '行情数据源' in outcome['reason']
    assert 'Yahoo' not in outcome['reason'] and '429' not in outcome['reason']