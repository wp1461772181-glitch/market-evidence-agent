"""Tests for the Agent overhaul: regex barrier removal, 9 tool capability expansion,
and natural language synthesis (no raw JSON outputs).
"""
import uuid
from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import select

from app.agent_tool_catalog import (
    definitions,
    write_tool_names,
    SaveMaterialEvidenceArgs,
    WithdrawDocumentArgs,
    RetrievalDiagnosticsArgs,
    WorkspaceArgs,
)
from app.agent_workflow.direct import execution_facts, execution_fallback, Turn
from app.agent_workflow.references import assistant_item_catalogue
from app.agent_workflow.workspace_tools import find_revision_candidates
from app.database import Base, SessionLocal, engine
from app.models import UploadedEvidence, SecFilingInventory
from app.forecast_v2_models import ForecastVersionV2, ForecastJobV2


@pytest.fixture(autouse=True)
def setup_tables(disposable_database):
    Base.metadata.create_all(bind=engine)


def test_tool_catalog_contains_new_tools():
    """Verify that save_material_evidence, withdraw_knowledge_document and test_retrieval_diagnostics
    are properly registered with correct permissions and schemas."""
    defs = definitions()
    assert 'save_material_evidence' in defs
    assert defs['save_material_evidence'].level == 'costly'
    assert defs['save_material_evidence'].owner is True

    assert 'withdraw_knowledge_document' in defs
    assert defs['withdraw_knowledge_document'].level == 'costly'
    assert defs['withdraw_knowledge_document'].owner is True

    assert 'test_retrieval_diagnostics' in defs
    assert defs['test_retrieval_diagnostics'].level == 'read'
    assert defs['test_retrieval_diagnostics'].owner is True

    # Check write tool names
    writes = write_tool_names()
    assert 'save_material_evidence' in writes
    assert 'withdraw_knowledge_document' in writes
    assert 'revise_forecast' in writes
    assert 'initialize_prices' in writes
    assert 'start_historical_replay' in writes


def test_save_material_evidence_schema():
    """Verify SaveMaterialEvidenceArgs validation."""
    valid = SaveMaterialEvidenceArgs.model_validate({
        'symbol': 'INTC',
        'title': 'Intel Announces New Xeon 6 Processors',
        'source_url': 'https://newsroom.intel.com/press-releases/xeon-6',
        'published_at': '2026-10-15T08:00:00Z',
        'credibility_stars': 5,
        'credibility_reason': 'Official Intel press release',
        'impact_severity': 'high',
        'content': '# Intel Xeon 6\nIntel officially launched Xeon 6 today...',
    })
    assert valid.symbol == 'INTC'
    assert valid.credibility_stars == 5


def test_execution_facts_carry_the_recorded_outcome_and_no_raw_json():
    """The report the model narrates is built from these facts; none of them is prose or raw JSON."""
    upload = execution_facts({'ok': True, 'data': [{
        'id': 'c3c274a0-0000-0000-0000-000000000001', 'symbol': 'INTC',
        'title': 'Intel IFS Foundry Roadmap Update', 'created': True, 'status': 'created'}]})
    assert upload[0]['title'] == 'Intel IFS Foundry Roadmap Update'
    assert upload[0]['symbol'] == 'INTC' and upload[0]['created'] is True

    prices = execution_facts({'ok': True, 'data': [{'status': 'completed', 'inserted': 365}]})
    assert prices[0]['inserted'] == 365 and prices[0]['status'] == 'completed'

    revision = execution_facts({'ok': True, 'data': [{'revision_id': 'rev-1234', 'job_id': 'job-5678', 'status': 'queued'}]})
    assert revision[0]['job_id'] == 'job-5678' and revision[0]['status'] == 'queued'

    withdrawn = execution_facts({'ok': True, 'data': [{'document_id': 'doc-1234', 'status': 'withdrawn'}]})
    assert withdrawn[0]['status'] == 'withdrawn'

    for facts in (upload, prices, revision, withdrawn):
        assert '{' not in execution_fallback(facts) and 'revision_id' not in str(facts)


def test_assistant_item_catalogue_supports_bullets():
    """Verify that assistant_item_catalogue parses bulleted lists (- * • ·) as well as numbered lists."""
    messages = [
        {
            'role': 'assistant',
            'content': (
                "为您找到以下候选材料：\n"
                "- 英特尔发布第三季度业绩预告\n"
                "* 苹果新一代 M5 芯片发布\n"
                "• 微软与英伟达达成算力深化合作\n"
                "· 谷歌发布最新 Gemini 2.5 大模型\n"
            ),
        }
    ]
    catalogue = assistant_item_catalogue(messages)
    assert len(catalogue) == 1
    items = catalogue[0]['items']
    assert len(items) == 4
    assert items[0]['position'] == 1
    assert '英特尔' in items[0]['text']
    assert items[1]['position'] == 2
    assert '苹果' in items[1]['text']
    assert items[2]['position'] == 3
    assert '微软' in items[2]['text']
    assert items[3]['position'] == 4
    assert '谷歌' in items[3]['text']


def test_find_revision_candidates_filtering():
    """Verify the 3-day forward (new material) + 30-day backward (past backfill) rule in find_revision_candidates."""
    test_symbol = 'TREV'
    now = datetime.now(UTC)
    decision_at = now - timedelta(days=10)

    with SessionLocal() as db:
        # Create a mock forecast version
        root_id = uuid.uuid4()
        job_id = uuid.uuid4()
        job = ForecastJobV2(
            id=job_id,
            symbol=test_symbol,
            kind='new',
            idempotency_key=f'test-rev-job-{uuid.uuid4()}',
            request_fingerprint='fp1',
            status='succeeded',
            time_mode='observed',
        )
        db.add(job)
        db.flush()

        version = ForecastVersionV2(
            id=root_id,
            job_id=job_id,
            root_id=root_id,
            symbol=test_symbol,
            version_no=1,
            decision_at=decision_at,
            market_cutoff_at=decision_at,
            target_contract={'symbol': test_symbol, 'horizon': '3m'},
            target_contract_hash='hash1',
            price_input_manifest={'source': 'test'},
            evidence_version_manifest=[],
            feature_snapshot={},
            model_status='baseline_only',
            model_manifest={},
            trigger_type='manual',
        )
        db.add(version)



        # 1. New material: pub within [decision_at, decision_at + 3 days]
        u1 = UploadedEvidence(
            symbol=test_symbol,
            title='New material within 3 days',
            source_url='https://example.com/1',
            published_at=decision_at + timedelta(days=2),
            observed_at=now,
            credibility_stars=5,
            credibility_reason='test',
            impact_severity='high',
            filename='m1.md',
            content_sha256='hash1',
            raw_content=b'test1',
            content_text='test1',
        )
        # 2. Past supplementary material: obs > decision_at, pub in [decision_at - 30 days, decision_at)
        u2 = UploadedEvidence(
            symbol=test_symbol,
            title='Backfilled past material within 30 days',
            source_url='https://example.com/2',
            published_at=decision_at - timedelta(days=15),
            observed_at=now,  # Observed AFTER decision_at
            credibility_stars=4,
            credibility_reason='test',
            impact_severity='medium',
            filename='m2.md',
            content_sha256='hash2',
            raw_content=b'test2',
            content_text='test2',
        )
        # 3. Too old past material: pub < decision_at - 30 days -> should NOT appear
        u3 = UploadedEvidence(
            symbol=test_symbol,
            title='Too old past material',
            source_url='https://example.com/3',
            published_at=decision_at - timedelta(days=45),
            observed_at=now,
            credibility_stars=3,
            credibility_reason='test',
            impact_severity='low',
            filename='m3.md',
            content_sha256='hash3',
            raw_content=b'test3',
            content_text='test3',
        )
        # 4. Too late material: pub > decision_at + 3 days -> should NOT appear
        u4 = UploadedEvidence(
            symbol=test_symbol,
            title='Too late material',
            source_url='https://example.com/4',
            published_at=decision_at + timedelta(days=5),
            observed_at=now,
            credibility_stars=4,
            credibility_reason='test',
            impact_severity='medium',
            filename='m4.md',
            content_sha256='hash4',
            raw_content=b'test4',
            content_text='test4',
        )
        db.add_all([u1, u2, u3, u4])
        db.commit()

        try:
            res = find_revision_candidates(test_symbol, str(version.id), db)
            assert res['status'] == 'success'
            titles = [c['title'] for c in res['candidates']]
            assert 'New material within 3 days' in titles
            assert 'Backfilled past material within 30 days' in titles
            assert 'Too old past material' not in titles
            assert 'Too late material' not in titles

            new_mat = next(c for c in res['candidates'] if c['title'] == 'New material within 3 days')
            assert new_mat['category'] == 'new_material'
            assert new_mat['badge'] == '新发布材料'

            past_mat = next(c for c in res['candidates'] if c['title'] == 'Backfilled past material within 30 days')
            assert past_mat['category'] == 'past_supplementary'
            assert past_mat['badge'] == '过往补充材料'
        finally:
            db.delete(u1)
            db.delete(u2)
            db.delete(u3)
            db.delete(u4)
            db.delete(version)
            db.flush()
            db.delete(job)
            db.commit()


def test_execution_facts_flatten_nested_consent_results():
    """A multi-item consent result is flattened one fact per executed item."""
    val = {'ok': True, 'status': 'complete', 'data': [
        {'ok': True, 'data': [{'id': '2085f271-74c6-4676-8f34-7dc0d68cd458', 'symbol': 'AMD',
                               'title': 'Advanced Micro Devices Inc(AMD)股票10月14日盤中下跌3.47%：背後推手曝光',
                               'created': True, 'status': 'created'}]},
        {'ok': True, 'data': [{'id': 'f2000e34-3c85-41e2-804f-1b5b4e1d993e', 'symbol': 'AMD',
                               'title': 'Advanced Micro Devices Inc(AMD)股票10月16日盤中下跌3.79%：原因全解讀',
                               'created': True, 'status': 'created'}]},
    ]}
    facts = execution_facts(val)
    assert len(facts) == 2 and {f['symbol'] for f in facts} == {'AMD'}
    fallback = execution_fallback(facts)
    assert '10月14日盤中下跌3.47%' in fallback and '10月16日盤中下跌3.79%' in fallback
