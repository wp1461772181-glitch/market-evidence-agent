"""An agent's confirmed save lands in the left-hand 资料库, through the manual upload's own function.

Regression (UX batch 2, 问题 3): ``ingest_web_source`` wrote only ``agent_web_sources`` plus a vector-store
document, so Levi's 「存进资料库」 never showed in 资料库 › 材料库.  It now writes ``uploaded_evidence`` with
``create_uploaded_evidence`` (as 上传媒体 does), and search keeps one copy: the 资料库 one.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.agent_workflow import consent, direct, tools
from app.agent_workflow.models import AgentWebSource
from app.agent_workflow.state import AgentRuntimeContext
from app.agent_workflow.web_sources import freeze_snapshot, save_snapshot
from app.database import Base, SessionLocal, engine
from app.knowledge.models import KnowledgeDocument, KnowledgeUploadOwner
from app.models import UploadedEvidence

OWNER_HASH = 'l' * 64


def _runtime(conversation='library-save', **extra):
    return AgentRuntimeContext('custom', 'test', 'dummy', identity_credential='owner', owner_hash=OWNER_HASH,
                               conversation_id=conversation, **extra)


def _page(url=None, body=None):
    return {'url': url or 'https://example.org/aapl/' + uuid.uuid4().hex, 'title': 'Apple 新品发布会要点',
            'excerpt': body or ('苹果发布新款 iPhone，强调端侧 AI。编号 ' + uuid.uuid4().hex),
            'published_at': '2026-10-20T08:00:00+00:00', 'fetched_at': datetime.now(UTC).isoformat(),
            'body_provenance': 'public_page_fetch'}


def _operation(page, **arguments):
    return {'name': 'ingest_web_source', 'arguments': {'url': page['url'], 'symbol': 'AAPL', 'company': 'Apple', **arguments},
            'snapshot': freeze_snapshot(page), 'entity_aliases': []}


@pytest.fixture
def owner(monkeypatch, disposable_database):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(consent, 'owner_credential_matches', lambda credential: credential == 'owner')
    monkeypatch.setattr(tools, 'owner_credential_matches', lambda credential: credential == 'owner')
    return 'owner'


def _confirm(owner, operation):
    conversation = 'library-' + uuid.uuid4().hex[:8]
    card = consent.propose(owner, conversation, [operation], {'title': '保存网页为来源'})
    token = card['choices'][0]['value'].split(':', 1)[1]
    return consent.execute(owner, conversation, token, [operation],
                           lambda op, key: tools.execute_costly(op, key, _runtime(conversation)))['data'][0]


def test_a_confirmed_web_save_is_listed_in_the_material_library(owner, client):
    page = _page()
    outcome = _confirm(owner, _operation(page))
    result = outcome.get('outcome', outcome)
    assert result['ok'] is True and result['destination'] == 'material_library'
    assert result['material_link'] == f"#materials?symbol=AAPL&focus={result['uploaded_evidence_id']}"

    listed = client.get('/v3/materials', params={'symbol': 'AAPL', 'source_type': 'uploaded_media', 'limit': 50}).json()
    items = listed.get('items') or listed.get('data') or []
    row = next(item for item in items if str(item.get('source_id') or item.get('id')) == result['uploaded_evidence_id'])
    assert row['source_type'] == 'uploaded_media' and row['title'] == 'Apple 新品发布会要点'
    with SessionLocal() as db:
        evidence = db.get(UploadedEvidence, uuid.UUID(result['uploaded_evidence_id']))
        assert page['excerpt'] in evidence.content_text and evidence.source_url == page['url']
        assert db.scalar(select(KnowledgeUploadOwner).where(KnowledgeUploadOwner.upload_id == evidence.id)).owner_hash == OWNER_HASH


def test_saving_the_same_page_twice_makes_one_library_row(owner):
    page = _page()
    first = _confirm(owner, _operation(page))
    second = tools.execute_costly(_operation(page), 'request.again', _runtime())
    first = first.get('outcome', first)
    assert second['status'] == 'already_exists' and second['uploaded_evidence_id'] == first['uploaded_evidence_id']
    with SessionLocal() as db:
        digest_rows = db.scalar(select(func.count()).select_from(UploadedEvidence).where(
            UploadedEvidence.source_url == page['url']))
        assert digest_rows == 1


def test_search_keeps_only_the_library_copy(owner):
    page = _page()
    result = tools.execute_costly(_operation(page), 'request.0', _runtime())
    with SessionLocal() as db:
        web = db.scalar(select(AgentWebSource).where(AgentWebSource.id == uuid.UUID(result['source_id'])))
        assert web.uploaded_evidence_id == uuid.UUID(result['uploaded_evidence_id'])
        active_web_docs = db.scalar(select(func.count()).select_from(KnowledgeDocument).where(
            KnowledgeDocument.source_table == 'agent_web_sources', KnowledgeDocument.source_id == str(web.id),
            KnowledgeDocument.active.is_(True)))
        assert active_web_docs == 0
    # The source sync would index web rows without chunks; one filed in 资料库 is indexed from uploaded_evidence.
    from app.knowledge.source_sync import sync_source_batch
    from app.knowledge import source_sync
    seen = []
    original = source_sync.ingest_documents
    def capture(session, docs, tokenizer, generation):
        seen.extend(docs)
        return {}
    source_sync.ingest_documents = capture
    try:
        for _ in range(20):
            batch = sync_source_batch(SessionLocal, None, 'test-generation', source_type='web', batch_size=10)
            if not batch['cursor']:
                break
    finally:
        source_sync.ingest_documents = original
    assert all(doc.source_id != result['source_id'] for doc in seen)


def test_an_old_vector_only_save_is_filed_into_the_library_on_the_next_save(owner):
    page = _page()
    with SessionLocal() as db:
        old = save_snapshot(db, OWNER_HASH, freeze_snapshot(page), symbol='AAPL', company='Apple', library=False)
        db.commit()
        assert old['destination'] == 'knowledge_only'
    again = tools.execute_costly(_operation(page), 'request.1', _runtime())
    assert again['destination'] == 'material_library' and again['status'] == 'created'
    with SessionLocal() as db:
        doc = db.get(KnowledgeDocument, uuid.UUID(old['document_id']))
        assert doc.active is False


def test_knowledge_only_keeps_the_page_out_of_the_library(owner):
    page = _page()
    result = tools.execute_costly(_operation(page, knowledge_only=True), 'request.2', _runtime())
    assert result['destination'] == 'knowledge_only' and 'uploaded_evidence_id' not in result


def test_a_save_without_a_stock_asks_which_library_first(monkeypatch):
    async def emit(_):
        pass
    turn = direct.Turn('把这篇存进资料库', _runtime(watchlist=('NVDA', 'MSFT')), emit, [], {})
    turn.owner = True
    turn.scope = {}
    assert turn.library_symbol({'url': 'https://example.org/x'}) is None
    outcome = turn.library_symbol_required()
    assert outcome['ok'] is False and outcome['error_code'] == 'library_symbol_required'
    assert outcome['candidates'] == ['NVDA', 'MSFT'] and '哪只股票' in outcome['question']


def test_the_report_links_to_the_library_row(monkeypatch):
    monkeypatch.setenv('FINBOT_WEB_URL', 'https://finbot.example/')
    value = {'ok': True, 'data': [{'ok': True, 'outcome': {'ok': True, 'symbol': 'AAPL',
             'material_link': '#materials?symbol=AAPL&focus=abc'}}]}
    note = direct.library_note(value)
    assert note.strip() == '在资料库查看：[资料库 · AAPL](https://finbot.example/#materials?symbol=AAPL&focus=abc)'
    assert direct.library_note({'ok': True, 'data': [{'ok': True}]}) == ''


def test_the_backfill_dry_run_writes_nothing_and_apply_files_once(owner):
    from scripts import backfill_web_sources_to_materials as backfill
    page = _page()
    with SessionLocal() as db:
        old = save_snapshot(db, OWNER_HASH, freeze_snapshot(page), symbol='AAPL', library=False)
        db.commit()
    dry = backfill.run(apply=False)
    assert dry['would_file'] >= 1
    with SessionLocal() as db:
        assert db.get(AgentWebSource, uuid.UUID(old['source_id'])).uploaded_evidence_id is None
    applied = backfill.run(apply=True)
    assert applied.get('filed', 0) + applied.get('linked_existing', 0) >= 1
    with SessionLocal() as db:
        row = db.get(AgentWebSource, uuid.UUID(old['source_id']))
        assert row.uploaded_evidence_id is not None
        assert db.get(KnowledgeDocument, uuid.UUID(old['document_id'])).active is False
    with SessionLocal() as db:
        assert row.id not in {pending.id for pending in backfill.pending(db)}
