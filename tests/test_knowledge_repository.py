def test_generation_and_repository_contract_placeholder():
    from app.knowledge.repository import embedding_generation
    one=embedding_generation('BAAI/bge-m3','revision','v1','v1')
    assert one==embedding_generation('BAAI/bge-m3','revision','v1','v1')
    assert one!=embedding_generation('BAAI/bge-m3','revision2','v1','v1')

def test_faq_first_import_and_idempotent_reingest_share_availability(disposable_database):
    from datetime import UTC, datetime
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.database import engine
    from app.agent_chat_models import AgentFAQ
    from app.knowledge.models import KnowledgeDocument, KnowledgeChunk
    from app.knowledge.repository import ingest_documents
    from app.knowledge.sources import adapt_faq
    from scripts.migrate_knowledge import migrate
    class Tok:
        def encode(self, text, **kwargs): return text.split()
    migrate(engine)
    Base = __import__('app.database', fromlist=['Base']).Base
    Base.metadata.create_all(engine, tables=[AgentFAQ.__table__])
    key=976531
    with Session(engine) as session, session.begin():
        session.add(AgentFAQ(id=key,question='FAQ timing fixture',answer='Use the source link.',category='usage'))
        source=adapt_faq({'id':key,'question':'FAQ timing fixture','answer':'Use the source link.','category':'usage'})
        ingest_documents(session,[source],Tok(),'e'*64)
        chunk=session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.generation=='e'*64))
        doc=session.get(KnowledgeDocument,chunk.document_id)
        assert doc.available_at is not None
        assert chunk.available_at == doc.available_at
        # Reproduce legacy FAQ rows where only document availability was repaired.
        chunk.available_at=None
    with Session(engine) as session, session.begin():
        ingest_documents(session,[source],Tok(),'e'*64)
        chunk=session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.generation=='e'*64))
        doc=session.get(KnowledgeDocument,chunk.document_id)
        assert chunk.available_at == doc.available_at
        assert doc.available_at is not None
        from app.knowledge.models import KnowledgeIndexTask
        session.query(KnowledgeIndexTask).filter(KnowledgeIndexTask.chunk_id==chunk.id).delete(synchronize_session=False)
        session.delete(chunk);session.delete(doc);session.query(AgentFAQ).filter(AgentFAQ.id==key).delete(synchronize_session=False)

def test_migration_ingest_idempotency_and_lease_epoch(disposable_database):
    import uuid
    from datetime import UTC, datetime, timedelta
    from sqlalchemy import select, func
    from sqlalchemy.orm import Session
    from app.database import engine
    from app.knowledge.models import KnowledgeDocument, KnowledgeChunk, KnowledgeIndexTask
    from app.knowledge.repository import ingest_documents, claim_task, ack_task, VectorReceipt
    from app.knowledge.sources import SourceDocument
    from scripts.migrate_knowledge import migrate
    class Tok:
        def encode(self, text, **kwargs): return text.split()
    migrate(engine)
    source=SourceDocument('fixture','fixture-1','v1','a'*64,'uploaded_media','DEMO','public','public',None,
      'Synthetic report','https://example.test',None,datetime.now(UTC),datetime.now(UTC),False,'markdown',
      '# FY2025\nRevenue was USD 123 million in FY2025.')
    gen='f'*64
    with Session(engine) as session, session.begin():
        counts=ingest_documents(session,[source],Tok(),gen)
        assert counts['documents']==1 and counts['chunks']==1
    with Session(engine) as session, session.begin():
        assert ingest_documents(session,[source],Tok(),gen)['skipped']==1
        lease1=claim_task(session,gen,datetime.now(UTC),lease_seconds=1,worker_id='old')
        assert lease1
    with Session(engine) as session, session.begin():
        lease2=claim_task(session,gen,datetime.now(UTC)+timedelta(seconds=2),worker_id='new')
        assert lease2 and lease2.lease_epoch==lease1.lease_epoch+1
        wrong=VectorReceipt(str(lease2.chunk_id),lease2.source_hash,'0'*64,gen)
        assert not ack_task(session,lease2,wrong)
    with Session(engine) as session, session.begin():
        task=session.get(KnowledgeIndexTask,lease1.task_id)
        assert task.lease_epoch==lease2.lease_epoch and task.status=='leased'
        chunk=session.get(KnowledgeChunk,lease2.chunk_id)
        receipt=VectorReceipt(str(lease2.chunk_id),lease2.source_hash,lease2.content_hash,gen)
        assert not ack_task(session,lease1,receipt)
        from dataclasses import replace
        assert not ack_task(session,replace(lease2,generation='z'*64),receipt)
        task=session.get(KnowledgeIndexTask,lease2.task_id)
        task.lease_until=datetime.now(UTC)-timedelta(seconds=1)
        assert not ack_task(session,lease2,receipt)
        task.lease_until=datetime.now(UTC)+timedelta(seconds=30)
        chunk=session.get(KnowledgeChunk,lease2.chunk_id);doc=session.get(KnowledgeDocument,chunk.document_id)
        doc.active=False
        assert not ack_task(session,lease2,receipt)
        doc.active=True
        assert ack_task(session,lease2,receipt)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeDocument))==1
        assert session.scalar(select(KnowledgeChunk.status))=='ready'

def test_generation_and_scope_changes_keep_separate_pg_snapshots(disposable_database):
    from dataclasses import replace
    from datetime import UTC,datetime
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.database import engine
    from app.knowledge.models import KnowledgeChunk,KnowledgeDocument
    from app.knowledge.repository import ingest_documents
    from app.knowledge.sources import SourceDocument
    from scripts.migrate_knowledge import migrate
    class Tok:
        def encode(self,text,**kwargs):return text.split()
    migrate(engine)
    now=datetime.now(UTC)
    source=SourceDocument('fixture','scope-rebuild','v1','c'*64,'uploaded_media','DEMO','public','public',None,'Synthetic','https://example.test',None,now,now,False,'plain','# FY2025\nRevenue was USD 123 million.')
    with Session(engine) as session,session.begin():
        ingest_documents(session,[source],Tok(),'a'*64)
        first=session.scalar(select(KnowledgeChunk).join(KnowledgeDocument).where(KnowledgeDocument.source_id=='scope-rebuild'))
        first_id=first.id
        assert first.previous_chunk_id is None and first.next_chunk_id is None
        assert first.is_key_clause
        assert ingest_documents(session,[source],Tok(),'b'*64)['chunks']==1
        rebuilt=session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.generation=='b'*64,KnowledgeChunk.document_id==first.document_id))
        assert rebuilt.id!=first_id and first_id!=rebuilt.id
        private=replace(source,source_version='v2',source_hash='d'*64,scope='private',visibility='private',owner_hash='a'*64)
        ingest_documents(session,[private],Tok(),'a'*64)
        rows=session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.source_id=='scope-rebuild').order_by(KnowledgeDocument.created_at)).all()
        assert len(rows)==2 and rows[0].active is False and rows[1].active is True
        assert session.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id==rows[1].id))!=first_id
