"""What the agent reads about a saved forecast, and whether its citation survives the restore check.

Regression from 2026-10-20: asked for Google's stock forecast, the agent said the snapshot had "no
price target or range" because the read returned only three probabilities, not the target they are
about (close on a date against an anchor close, ±2%).  Its citation was also stored as "来源已失效":
the restore compared the whole cited row, including the gate's own notes, with the saved projection.
"""
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import agent_tools
from app.agent_chat_stream import _restore_citations
from app.agent_tools import forecast_snapshot
from app.database import Base, SessionLocal, engine
from app.forecast_jobs import enqueue_job
from app.forecast_v2_models import ForecastVersionV2

CONTRACT = {'anchor_date': '2026-09-25', 'anchor_close': 343.9200134277344, 'threshold': 0.02,
            'target_end_date': '2026-10-23', 'horizon_sessions': 20, 'target_spec_version': 'absolute-close-v1',
            'price_hash': 'x' * 64}


from test_forecast_revision_candidates import forget_symbols


@pytest.fixture
def tables(disposable_database):
    Base.metadata.create_all(bind=engine)
    yield
    forget_symbols(['SNA', 'SNB', 'SNC'])


def _version(symbol, *, parent=None, version_no=1, probabilities=None, brief=None):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol=symbol, kind='new' if parent is None else 'manual_revision',
                          idempotency_key=f'snapshot-{uuid.uuid4()}',
                          **({} if parent is None else {'root_version_id': parent, 'parent_version_id': parent}))
        row = ForecastVersionV2(
            id=uuid.uuid4(), root_id=parent or uuid.uuid4(), parent_version_id=parent, job_id=job.id,
            version_no=version_no, symbol=symbol, target_contract=CONTRACT, target_contract_hash='a' * 64,
            decision_at=now - timedelta(hours=10 - version_no), market_cutoff_at=now - timedelta(hours=11 - version_no),
            price_input_manifest={}, evidence_version_manifest=[], feature_snapshot={},
            decision_probabilities=probabilities or {'bearish': 0.03, 'bullish': 0.01, 'neutral': 0.96},
            model_status='experimental_jev', model_manifest={}, trigger_type='manual' if parent is None else 'manual_revision',
            research_brief=brief,
        )
        if parent is None:
            row.root_id = row.id
        db.add(row); db.commit()
        return row.id


def test_the_snapshot_says_what_the_probabilities_are_about():
    data = forecast_snapshot({'id': uuid.uuid4(), 'version_no': 1, 'target_contract': CONTRACT,
                              'decision_probabilities': {'bullish': 0.01, 'bearish': 0.03, 'neutral': 0.96}})
    target = data['target']
    assert target['anchor_close'] == 343.92 and target['threshold_pct'] == 2.0
    assert target['bullish_above'] == 350.8 and target['bearish_below'] == 337.04
    assert target['target_end_date'] == '2026-10-23' and target['anchor_date'] == '2026-09-25'
    assert '350.8' in target['meaning'] and '337.04' in target['meaning'] and '2026-10-23' in target['meaning']
    assert 'target_contract' not in data and 'price_hash' not in json.dumps(data)


def test_a_revised_forecast_is_read_with_the_version_it_revised(tables):
    root = _version('SNA')
    _version('SNA', parent=root, version_no=2, probabilities={'bearish': 0.09, 'bullish': 0.06, 'neutral': 0.85})

    rows = asyncio.run(agent_tools.query_latest_forecast.coroutine(symbol='SNA'))['data']

    assert [row['version_no'] for row in rows] == [2, 1]
    assert rows[0]['probabilities']['neutral'] == 0.85 and rows[1]['probabilities']['neutral'] == 0.96
    assert rows[1]['role'] == 'previous_version' and 'role' not in rows[0]


def _brief(tag):
    return {'supporting': [{'statement': f'{tag} 营收增长', 'citations': [{'source_id': 'x'}]}],
            'counter': [{'statement': f'{tag} 债务增加'}], 'new_facts': [{'statement': '长' * 400}] * 9,
            'background': [{'statement': '背景不进理由'}]}


def test_both_versions_come_with_their_reasons(tables):
    """Regression from 2026-10-26: asked to compare Google's two forecast versions, the agent saw each
    brief only as its first 1,200 characters of Python repr, so the reasons were cut off and it went
    hunting through other reads (80 s, 16 tool calls) before a partial answer."""
    root = _version('SNA', brief=_brief('v1'))
    _version('SNA', parent=root, version_no=2, brief=_brief('v2'))

    rows = asyncio.run(agent_tools.query_latest_forecast.coroutine(symbol='SNA'))['data']

    assert rows[0]['reasons']['supporting'] == ['v2 营收增长'] and rows[1]['reasons']['counter'] == ['v1 债务增加']
    assert len(rows[0]['reasons']['new_facts']) == 5 and all(len(fact) == 160 for fact in rows[0]['reasons']['new_facts'])
    # The cited quote is the row's JSON cut at 5,000 characters; a cut row would expire on restore.
    assert all(len(json.dumps(row, ensure_ascii=False, default=str)) < 5000 for row in rows)


def test_a_cited_version_keeps_its_reasons_through_the_restore(tables):
    version = _version('SNB', brief=_brief('v1'))
    row = asyncio.run(agent_tools.query_latest_forecast.coroutine(symbol='SNB'))['data'][0]
    cite = lambda quoted: {'ordinal': 1, 'source_type': 'saved_forecast', 'source_id': str(version), 'symbol': 'SNB',
                           'title': 'SNB forecast', 'quote': json.dumps(quoted, ensure_ascii=False, default=str)}

    assert _restore_citations([cite(row)], 'guest-session')[0]['source_type'] == 'saved_forecast'
    forged = {**row, 'reasons': {**row['reasons'], 'supporting': ['营收翻倍']}}
    assert _restore_citations([cite(forged)], 'guest-session')[0]['source_type'] == 'unavailable'


def test_a_forecast_citation_survives_the_gate_notes(tables):
    version = _version('SNB')
    with SessionLocal() as db:
        row = db.get(ForecastVersionV2, version)
        payload = {key: getattr(row, key) for key in ('id', 'root_id', 'version_no', 'decision_at', 'model_status',
                   'decision_probabilities', 'joint_probabilities', 'baseline_probabilities', 'research_brief',
                   'created_at', 'target_contract')}
    quoted = {**forecast_snapshot(payload), '_verified_symbol': 'SNB', 'gate_note': 'requested_metric_missing:股价预测'}
    item = {'ordinal': 1, 'source_type': 'saved_forecast', 'source_id': str(version), 'symbol': 'SNB',
            'title': 'SNB forecast', 'quote': json.dumps(quoted, ensure_ascii=False, default=str)}

    restored = _restore_citations([item], 'guest-session')

    assert restored[0]['source_type'] == 'saved_forecast'


def test_a_forecast_citation_whose_probabilities_changed_is_expired(tables):
    version = _version('SNC')
    with SessionLocal() as db:
        row = db.get(ForecastVersionV2, version)
        payload = {key: getattr(row, key) for key in ('id', 'root_id', 'version_no', 'decision_at', 'model_status',
                   'decision_probabilities', 'joint_probabilities', 'baseline_probabilities', 'research_brief',
                   'created_at', 'target_contract')}
    quoted = {**forecast_snapshot(payload), 'probabilities': {'bearish': 0.5, 'bullish': 0.5, 'neutral': 0.0}}
    item = {'ordinal': 1, 'source_type': 'saved_forecast', 'source_id': str(version), 'symbol': 'SNC',
            'title': 'SNC forecast', 'quote': json.dumps(quoted, ensure_ascii=False, default=str)}

    assert _restore_citations([item], 'guest-session')[0]['source_type'] == 'unavailable'
