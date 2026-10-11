"""Real approval -> chunk/index -> FAQ retrieval integration gate."""
import asyncio, hashlib, os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app.knowledge.api import router
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.indexing import index_pending
from app.knowledge.models import KnowledgeCandidate, KnowledgeChunk, KnowledgeDocument
from app.knowledge.runtime import KnowledgeRuntime, research_context
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.vector_store import MilvusIndex
from scripts.migrate_knowledge import migrate

pytestmark = pytest.mark.skipif(os.getenv('KNOWLEDGE_APPROVAL_REAL') != '1', reason='explicit real PG/Milvus/BGE approval gate')


def test_real_candidate_approval_indexes_and_retrieves_faq(disposable_database, monkeypatch):
    Base.metadata.create_all(engine, tables=[KnowledgeCandidate.__table__])
    migrate(engine)
    monkeypatch.setenv('APP_API_ACCESS_KEY', 'ch34-real-approval-test-key')
    owner = 'approval-owner-' + 'x' * 40
    candidate_id = uuid4()
    answer = 'Saved market notes remain private until explicitly approved, then become searchable FAQ evidence.'
    question = 'When does a saved note become searchable?'
    with Session(engine) as session, session.begin():
        session.add(KnowledgeCandidate(id=candidate_id, dedupe_key=uuid4().hex, scope='public', owner_hash=None,
            conversation_id=str(uuid4()), turn_id=str(uuid4()), evidence_ref='test:approval-flow',
            evidence_hash=hashlib.sha256(answer.encode()).hexdigest(), question=question, answer=answer,
            category='workflow', candidate_kind='product_faq', evidence_quote=answer, status='pending_review'))
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    approved = client.post(f'/knowledge/candidates/{candidate_id}/review',
        headers={'X-Chat-Session': owner, 'X-App-Access-Key': 'ch34-real-approval-test-key'},
        json={'approved': True, 'reviewer': 'integration-test'})
    assert approved.status_code == 200, approved.text
    generation = KnowledgeSettings.from_env(validate_model=True).generation
    embedder = DenseEmbedder('data/knowledge/models/bge-m3', cpu_threads=2)
    index = MilvusIndex('http://127.0.0.1:19530', generation)
    index.ensure_collection()
    indexed = index_pending(SessionLocal, embedder, index, generation, max_tasks=10, worker_id='approval-real-e2e')
    assert indexed['indexed'] >= 1 and indexed['failed'] == 0, indexed
    settings = KnowledgeSettings.from_env()
    runtime = KnowledgeRuntime(settings, embedder, index, 0.0)
    runtime.warm()
    import app.knowledge.runtime as runtime_module
    runtime_module._runtime = runtime
    from app.knowledge.retrieval import retrieve_faq
    async def retrieve():
        with research_context('0' * 64):
            return await retrieve_faq('How do approved saved notes become searchable?')
    try:
        result = asyncio.run(retrieve())
        assert result['ok'] and any(answer in row['answer'] for row in result['data']), result
        with Session(engine) as session:
            assert session.query(KnowledgeDocument).filter_by(source_id=str(candidate_id), status='active').count() == 1
            assert session.query(KnowledgeChunk).join(KnowledgeDocument).filter(KnowledgeDocument.source_id==str(candidate_id),KnowledgeChunk.status=='ready').count() >= 1
    finally:
        runtime.close(); runtime_module._runtime = None
