from datetime import UTC,datetime,timedelta
import pytest
from sqlalchemy.orm import Session
from app.database import engine
from app.knowledge.models import KnowledgeChunk,KnowledgeIndexTask,KnowledgeDocument
from app.knowledge.repository import ingest_documents
from app.knowledge.sources import SourceDocument
from scripts.migrate_knowledge import migrate

class Tok:
    def encode(self,text,**kwargs): return text.split()
class Embed:
    def encode(self,texts): return [[0.1]*1024 for _ in texts]
class Store:
    def __init__(self): self.rows={}
    def ensure_collection(self,generation=None):pass
    def upsert(self,rows):
        for row in rows:self.rows[row['id']]=row
    def get(self,ids):return {i:self.rows[i] for i in ids if i in self.rows}

@pytest.fixture
def indexed_source(disposable_database):
    migrate(engine)
    source=SourceDocument('fixture','index-fixture','v1','a'*64,'uploaded_media','DEMO','public','public',None,'synthetic','https://example.test',
      None,datetime.now(UTC),datetime.now(UTC),False,'markdown','# FY2025\nRevenue was USD 123 million.')
    with Session(engine) as s,s.begin():
        counts=ingest_documents(s,[source],Tok(),'g'*64)
        assert counts['chunks']==1 and counts['needs_review']==0
    yield
    with Session(engine) as s,s.begin():
      s.query(KnowledgeIndexTask).delete(); s.query(KnowledgeChunk).delete(); s.query(KnowledgeDocument).delete()

def test_two_interruption_points_resume_with_idempotent_upsert(indexed_source):
    from app.knowledge.indexing import index_pending,InjectedIndexCrash
    store=Store()
    with pytest.raises(InjectedIndexCrash):
        index_pending(lambda:Session(engine),Embed(),store,'g'*64,crash_at='after_pending')
    with Session(engine) as s,s.begin():
      task=s.query(KnowledgeIndexTask).filter_by(generation='g'*64).one(); task.lease_until=datetime.now(UTC)-timedelta(seconds=1)
    assert index_pending(lambda:Session(engine),Embed(),store,'g'*64)['indexed']==1
    with Session(engine) as s,s.begin():
      chunk=s.query(KnowledgeChunk).filter_by(generation='g'*64,status='ready').one()
      # Return it to pending to simulate a second item before the upsert/ack interruption.
      chunk.status='pending'; task=s.query(KnowledgeIndexTask).filter_by(generation='g'*64).one(); task.status='pending'; task.lease_epoch=0
    with pytest.raises(InjectedIndexCrash):
        index_pending(lambda:Session(engine),Embed(),store,'g'*64,crash_at='after_upsert')
    assert len(store.rows)==1
    with Session(engine) as s,s.begin():
      task=s.query(KnowledgeIndexTask).filter_by(generation='g'*64).one(); task.lease_until=datetime.now(UTC)-timedelta(seconds=1)
    result=index_pending(lambda:Session(engine),Embed(),store,'g'*64)
    assert result['indexed']==1 and len(store.rows)==1
    with Session(engine) as s:
      assert s.query(KnowledgeChunk).filter_by(generation='g'*64,status='ready').count()==1
      assert s.query(KnowledgeIndexTask).filter_by(generation='g'*64,status='complete').count()==1

def test_vector_receipt_metadata_must_match(indexed_source):
    from app.knowledge.repository import ack_task,claim_task,VectorReceipt
    with Session(engine) as s,s.begin(): lease=claim_task(s,'g'*64)
    with Session(engine) as s,s.begin():
      assert not ack_task(s,lease,VectorReceipt(str(lease.chunk_id),lease.source_hash,lease.content_hash,'wrong'))
      assert not ack_task(s,lease,VectorReceipt('other',lease.source_hash,lease.content_hash,'g'*64))


def test_index_pending_chunk_whitelist_leaves_unrelated_global_queue_untouched(indexed_source):
    from app.knowledge.indexing import index_pending
    from sqlalchemy import select
    store=Store()
    selected=SourceDocument('fixture','selected-analysis','analysis-v2','b'*64,'ai_analysis','AMD','public','public',None,
      'saved analysis','https://example.test/analysis',None,datetime.now(UTC),datetime.now(UTC),False,'test','Revenue rose 8%.')
    with Session(engine) as session,session.begin():
        ingest_documents(session,[selected],Tok(),'g'*64)
        selected_ids=set(session.scalars(select(KnowledgeChunk.id).join(KnowledgeDocument).where(KnowledgeDocument.source_id=='selected-analysis')).all())
        old_ids=set(session.scalars(select(KnowledgeChunk.id).join(KnowledgeDocument).where(KnowledgeDocument.source_id=='index-fixture')).all())
    result=index_pending(lambda:Session(engine),Embed(),store,'g'*64,max_tasks=10,chunk_ids=selected_ids)
    assert result['indexed']==len(selected_ids)
    with Session(engine) as session:
        assert set(session.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.status=='ready')))==selected_ids
        assert set(session.scalars(select(KnowledgeIndexTask.chunk_id).where(KnowledgeIndexTask.status=='pending')))==old_ids


def test_empty_chunk_whitelist_has_no_vector_store_side_effects():
    from app.knowledge.indexing import index_pending
    class ForbiddenStore:
        def ensure_collection(self,generation=None): raise AssertionError('must not create collection')
    assert index_pending(lambda:None,None,ForbiddenStore(),'g'*64,chunk_ids=set())=={'indexed':0,'failed':0,'idle':0}


def test_reconcile_repair_requeues_missing_vector_without_deleting(disposable_database,indexed_source):
    from sqlalchemy.orm import Session
    from app.database import engine
    from app.knowledge.repository import reconcile
    from app.knowledge.models import KnowledgeChunk,KnowledgeIndexTask
    store=Store()
    # Complete an indexed vector, then simulate an out-of-band vector loss.
    from app.knowledge.indexing import index_pending
    assert index_pending(lambda:Session(engine),Embed(),store,'g'*64)['indexed']==1
    with Session(engine) as session,session.begin():
        chunk=session.query(KnowledgeChunk).filter_by(generation='g'*64,status='ready').one()
        chunk_id=str(chunk.id);chunk_uuid=chunk.id;store.rows.pop(chunk_id)
        report=reconcile(session,store,'g'*64,repair=True)
        assert report['missing']==[chunk_id] and report['requeued']==1
    with Session(engine) as session:
        assert session.query(KnowledgeChunk).filter_by(id=chunk_uuid,status='pending').count()==1
        assert session.query(KnowledgeIndexTask).filter_by(chunk_id=chunk_uuid,status='pending').count()==1
