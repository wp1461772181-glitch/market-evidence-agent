from datetime import UTC,datetime
import hashlib
import pytest
from sqlalchemy.orm import Session
from app.database import engine
from app.knowledge.extraction import extract_once,review_candidate
from app.knowledge.models import KnowledgeCandidate,KnowledgeDocument,KnowledgeChunk,KnowledgeIndexTask,KnowledgeExtractionRun
from app.knowledge.chunking import CHUNKER_VERSION,FORMAT_VERSION
from app.knowledge.repository import embedding_generation
from scripts.migrate_knowledge import migrate

class Tok:
    def encode(self,text,**kwargs):return text.split()

@pytest.fixture
def extraction_db(disposable_database):
    migrate(engine)
    yield
    with Session(engine) as s,s.begin():
        s.query(KnowledgeIndexTask).delete();s.query(KnowledgeChunk).delete();s.query(KnowledgeDocument).delete()
        s.query(KnowledgeExtractionRun).delete();s.query(KnowledgeCandidate).delete()

def test_candidates_are_staged_deduplicated_and_cursor_advances_after_commit(extraction_db):
    owner=hashlib.sha256(b'opaque').hexdigest()
    turns=[{'conversation_id':'conv-1','owner_hash':owner,'opted_in':True,'turns':[
      {'turn_id':'turn-1','created_at':'2026-01-01T00:00:00+00:00','status':'complete','messages':[
        {'role':'user','content':'How does saved research work?','status':'complete'},
        {'role':'assistant','content':'Local saved material queries do not trigger external fetching.','status':'complete'},
        {'role':'assistant','content':'tool data ignored','status':'complete','reasoning_content':'hidden'}]},
      {'turn_id':'turn-failed','created_at':'2026-01-02T00:00:00+00:00','status':'failed','messages':[{'role':'assistant','content':'must skip'}]}]}]
    def provider(_):
        return {'candidates':[
          {'question':'Will saved queries fetch online?','answer':'Local saved material queries do not trigger external fetching.','category':'data access','candidate_kind':'product_faq','evidence_quote':'Local saved material queries do not trigger external fetching.'},
          {'question':'What revenue was mentioned?','answer':'USD 123 million','category':'financial','candidate_kind':'financial_claim','evidence_quote':'How does saved research work?'},
          {'question':'What is my preference?','answer':'prefers concise replies','category':'preference','candidate_kind':'user_preference','evidence_quote':'How does saved research work?'},
          {'question':'Credential?','answer':'password: hidden','category':'sensitive','candidate_kind':'other','evidence_quote':'How does saved research work?'}]}
    first=extract_once(provider,None,turns,provider,max_turns=20,session_factory=lambda:Session(engine))
    assert first['processed']==1 and first['candidates']==2 and first['skipped']==2 and first['cursor']
    again=extract_once(provider,None,turns,provider,max_turns=20,session_factory=lambda:Session(engine))
    assert again['candidates']==0 and again['skipped']==4
    with Session(engine) as s:
        rows=s.query(KnowledgeCandidate).order_by(KnowledgeCandidate.candidate_kind).all()
        assert {r.candidate_kind for r in rows}=={'product_faq','financial_claim'}
        assert all(r.status=='pending_review' and r.owner_hash==owner for r in rows)
        run=s.query(__import__('app.knowledge.models',fromlist=['KnowledgeExtractionRun']).KnowledgeExtractionRun).order_by(__import__('app.knowledge.models',fromlist=['KnowledgeExtractionRun']).KnowledgeExtractionRun.created_at.desc()).first()
        assert run.cursor==first['cursor'] and run.batch_status=='complete'

def test_review_only_publishes_product_faq_and_binds_private_owner(extraction_db):
    owner=hashlib.sha256(b'opaque').hexdigest()
    turns=[{'conversation_id':'conv-2','owner_hash':owner,'opted_in':True,'turns':[{'turn_id':'t2','created_at':'2026-02-01T00:00:00Z','status':'complete','messages':[{'role':'user','content':'Fee?','status':'complete'},{'role':'assistant','content':'Saved materials stay local.','status':'complete'}]}]}]
    def provider(_):return {'candidates':[{'question':'Are saved materials local?','answer':'Saved materials stay local.','category':'data handling','candidate_kind':'product_faq','evidence_quote':'Saved materials stay local.'},{'question':'Financial result?','answer':'Revenue rose 10%.','category':'finance','candidate_kind':'financial_claim','evidence_quote':'Fee?'}]}
    extract_once(provider,None,turns,provider,session_factory=lambda:Session(engine))
    gen=embedding_generation('BAAI/bge-m3','r1',FORMAT_VERSION,CHUNKER_VERSION)
    with Session(engine) as s,s.begin():
        rows=s.query(KnowledgeCandidate).all()
        product=next(x for x in rows if x.candidate_kind=='product_faq')
        financial=next(x for x in rows if x.candidate_kind=='financial_claim')
        with pytest.raises(ValueError,match='only_product_faq'):
            review_candidate(s,financial.id,'approve','reviewer','public',Tok(),gen)
        product_id=str(product.id)
        status=review_candidate(s,product.id,'approve','reviewer','private',Tok(),gen)
        assert status=='approved_private'
    with Session(engine) as s:
        doc=s.query(KnowledgeDocument).filter_by(source_table='knowledge_candidates').one()
        assert doc.scope=='private' and doc.owner_hash==owner and doc.source_id==product_id
        assert s.query(KnowledgeCandidate).filter_by(status='pending_review').count()==1

def test_operator_failure_records_safe_failed_batch_and_retries(extraction_db):
    owner=hashlib.sha256(b'operator-retry').hexdigest()
    conversations=[{'conversation_id':'retry-conv','owner_hash':owner,'opted_in':True,'turns':[{'turn_id':'turn','created_at':'2026-01-01T00:00:00Z','status':'complete','messages':[{'role':'user','content':'How do saved materials work?','status':'complete'}]}]}]
    def broken(_):raise RuntimeError('provider details must not be persisted')
    failed=extract_once(broken,None,conversations,broken,session_factory=lambda:Session(engine))
    assert failed['status']=='failed' and failed['error_code']=='operator_failed'
    with Session(engine) as session:
        run=session.query(KnowledgeExtractionRun).filter_by(batch_status='failed').one()
        assert run.error_code=='operator_failed' and 'provider details' not in str(run.error_code)
    recovered=extract_once(broken,None,conversations,lambda _: {'candidates':[]},session_factory=lambda:Session(engine))
    assert recovered['status']=='complete' and recovered['processed']==1

def test_extraction_cursor_is_scoped_to_selected_conversation_batch(extraction_db):
    owner=hashlib.sha256(b'scope-cursor').hexdigest()
    def conversation(name,created):
        return {'conversation_id':name,'owner_hash':owner,'opted_in':True,'turns':[{'turn_id':'turn','created_at':created,'status':'complete','messages':[{'role':'user','content':'Question?','status':'complete'}]}]}
    provider=lambda _: {'candidates':[]}
    first=extract_once(provider,None,[conversation('older','2026-01-01T00:00:00Z')],provider,session_factory=lambda:Session(engine),scope_key='a'*64)
    second=extract_once(provider,None,[conversation('newer','2026-02-01T00:00:00Z')],provider,session_factory=lambda:Session(engine),scope_key='b'*64)
    assert first['processed']==1 and second['processed']==1
    with Session(engine) as session:
        runs=session.query(KnowledgeExtractionRun).filter(KnowledgeExtractionRun.scope_key.in_(['a'*64,'b'*64])).all()
        assert {run.scope_key for run in runs}=={'a'*64,'b'*64}
