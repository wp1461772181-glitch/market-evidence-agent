from datetime import UTC, datetime, timedelta, date
from uuid import uuid4
import hashlib
from sqlalchemy.orm import Session
from app.database import Base, engine
from app.models import SecFilingInventory
from app.knowledge.sources import adapt_sec
from app.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeCandidate
from app.knowledge.repository import ingest_documents
from app.knowledge.retrieval import _current_rows
from app.knowledge.extraction import review_candidate
from scripts.migrate_knowledge import migrate
class Tok:
    def encode(self, value, **kwargs): return value.split()
def test_sec_future_observation_invalidates_current_snapshot(disposable_database):
    Base.metadata.create_all(engine,tables=[SecFilingInventory.__table__]);migrate(engine)
    ident=uuid4(); now=datetime.now(UTC); body='Synthetic revenue was USD 123 million in FY2025.'
    with Session(engine) as s,s.begin():
        row=SecFilingInventory(id=ident,symbol='DEMO',cik='0000000001',accession_number='0000000001-26-'+uuid4().hex[:6],form='10-K',filed_at=date(2026,1,1),primary_document='10k.htm',source_url='https://example.test/10k',source='synthetic',review_status='accepted',reviewed_at=now,observed_at=now,content_status='fetched',content_observed_at=now,content_excerpt=body,content_excerpt_sha256=hashlib.sha256(body.encode()).hexdigest(),content_truncated=False,content_source_url='https://example.test/exhibit',content_document_name='ex99.htm',content_kind='exhibit')
        s.add(row);s.flush();doc=adapt_sec({c.key:getattr(row,c.key) for c in row.__table__.columns});ingest_documents(s,[doc],Tok(),'1'*64)
        chunk=s.query(KnowledgeChunk).filter_by(generation='1'*64).one();chunk.status='ready';s.get(KnowledgeDocument,chunk.document_id).status='active';cid=str(chunk.id)
    assert _current_rows([cid],'1'*64,None,'DEMO',False)
    with Session(engine) as s,s.begin():s.get(SecFilingInventory,ident).observed_at=now+timedelta(days=1)
    assert _current_rows([cid],'1'*64,None,'DEMO',False)==[], 'current source availability change must invalidate frozen current snapshot'
def test_candidate_future_review_invalidates_old_publication(disposable_database):
    migrate(engine)
    with Session(engine) as s,s.begin():
        candidate=KnowledgeCandidate(dedupe_key=uuid4().hex,scope='private',owner_hash='a'*64,conversation_id=str(uuid4()),turn_id=str(uuid4()),evidence_ref='fixture',evidence_hash='b'*64,question='Where is the source?',answer='Open the original source link.',category='usage',candidate_kind='product_faq',evidence_quote='Open the original source link.',status='pending_review')
        s.add(candidate);s.flush();review_candidate(s,candidate.id,'approve','reviewer','public',Tok(),'2'*64);identity=candidate.id
        chunk=s.query(KnowledgeChunk).filter_by(generation='2'*64).one();chunk.status='ready';s.get(KnowledgeDocument,chunk.document_id).status='active';cid=str(chunk.id)
    assert _current_rows([cid],'2'*64,None,None,True)
    with Session(engine) as s,s.begin():s.get(KnowledgeCandidate,identity).reviewed_at=datetime.now(UTC)+timedelta(days=1)
    assert _current_rows([cid],'2'*64,None,None,True)==[], 'publication time change must invalidate old approved QA'
