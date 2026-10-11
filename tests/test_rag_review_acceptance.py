"""Independent acceptance probes; only the existing disposable test DB is used."""
from datetime import UTC, datetime, timedelta
from uuid import UUID,uuid4
import pytest
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import Base, engine
from app.agent_chat_models import AgentFAQ
from app.knowledge.sources import adapt_faq, SourceDocument
from app.knowledge.models import KnowledgeDocument, KnowledgeChunk
from app.knowledge.repository import ingest_documents, claim_task, ack_task, VectorReceipt
from app.knowledge.retrieval import _current_rows
from scripts.migrate_knowledge import migrate

class Tokenizer:
    def encode(self, text, **kwargs): return text.split()

@pytest.fixture(autouse=True)
def isolate_acceptance_rows(disposable_database):
    # This probe uses the repository's disposable test_market_evidence_* DB;
    # clear only this test's tables so other suite tests see no fixture residue.
    def clear():
        with engine.begin() as connection:
            existing=set(__import__('sqlalchemy').inspect(connection).get_table_names())
            tables=[name for name in ('knowledge_index_tasks','knowledge_chunks','knowledge_documents','knowledge_candidates',
              'knowledge_extraction_runs','knowledge_upload_owners','agent_faq','sec_filing_inventory') if name in existing]
            if tables:connection.execute(text('TRUNCATE TABLE '+', '.join('"'+name+'"' for name in tables)+' CASCADE'))
    clear()
    yield
    clear()

@pytest.fixture
def public_faq(disposable_database):
    Base.metadata.create_all(engine, tables=[AgentFAQ.__table__])
    migrate(engine)
    key=950000
    with Session(engine) as s, s.begin():
        s.add(AgentFAQ(id=key, question='Reviewer fixture', answer='Reviewed product instructions.', category='usage'))
        source=adapt_faq({'id':key,'question':'Reviewer fixture','answer':'Reviewed product instructions.','category':'usage'})
        ingest_documents(s,[source],Tokenizer(),'r'*64)
        chunk=s.query(KnowledgeChunk).filter_by(generation='r'*64).one()
        doc=s.get(KnowledgeDocument,chunk.document_id)
        doc.status='active';chunk.status='ready'; cid=str(chunk.id);did=doc.id
    return cid,did

def test_pg_document_revocation_blocks_stale_public_faq(public_faq):
    cid,did=public_faq
    assert _current_rows([cid],'r'*64,None,None,True)
    with Session(engine) as s,s.begin():
        doc=s.get(KnowledgeDocument,did);doc.scope='private';doc.visibility='private';doc.owner_hash='b'*64
    assert _current_rows([cid],'r'*64,None,None,True)==[], 'authoritative document permissions must block stale public vectors/chunks'

def test_frozen_document_provenance_tampering_fails_closed(public_faq):
    cid,did=public_faq
    assert _current_rows([cid],'r'*64,None,None,True)
    with Session(engine) as s,s.begin():s.get(KnowledgeDocument,did).title='tampered provenance'
    assert _current_rows([cid],'r'*64,None,None,True)==[]

def test_ack_cannot_complete_after_source_withdrawal(disposable_database):
    migrate(engine)
    now=datetime.now(UTC)
    src=SourceDocument('fixture',str(uuid4()),'v1','b'*64,'uploaded_media','DEMO','public','public',None,'review fixture',None,None,now,now,False,'plain','A complete source sentence.')
    with Session(engine) as s,s.begin():
        ingest_documents(s,[src],Tokenizer(),'w'*64);lease=claim_task(s,'w'*64)
    with Session(engine) as s,s.begin():
        chunk=s.get(KnowledgeChunk,lease.chunk_id);doc=s.get(KnowledgeDocument,chunk.document_id);doc.status='withdrawn';doc.active=False
    with Session(engine) as s,s.begin():
        assert not ack_task(s,lease,VectorReceipt(str(lease.chunk_id),lease.source_hash,lease.content_hash,lease.generation)), 'withdrawn authority must invalidate outstanding lease acknowledgement'

def test_chunk_locating_metadata_is_persisted():
    fields=set(KnowledgeChunk.__table__.columns.keys())
    assert 'is_key_clause' in fields, 'approved metadata missing: is_key_clause'
    assert any(name in fields for name in ('previous_chunk_id','prev_chunk_id')), 'approved previous block pointer missing'
    assert 'next_chunk_id' in fields, 'approved next block pointer missing'

def test_sec_provenance_edit_invalidates_stale_chunk(disposable_database):
    import hashlib
    from datetime import date
    from app.models import SecFilingInventory
    from app.knowledge.sources import adapt_sec
    Base.metadata.create_all(engine,tables=[SecFilingInventory.__table__]);migrate(engine)
    ident=uuid4();now=datetime.now(UTC);body='Revenue was USD 123 million in FY2025.'
    with Session(engine) as s,s.begin():
        row=SecFilingInventory(id=ident,symbol='DEMO',cik='0000000001',accession_number='0000000001-26-999999',form='10-K',filed_at=date(2026,1,1),accepted_at=None,primary_document='10k.htm',source_url='https://example.test/10k',source='synthetic',review_status='accepted',reviewed_at=now,observed_at=now,content_status='fetched',content_observed_at=now,content_excerpt=body,content_excerpt_sha256=hashlib.sha256(body.encode()).hexdigest(),content_truncated=False,content_source_url='https://example.test/original-exhibit',content_document_name='ex99.htm',content_kind='exhibit')
        s.add(row);s.flush();source=adapt_sec({c.key:getattr(row,c.key) for c in row.__table__.columns});ingest_documents(s,[source],Tokenizer(),'s'*64)
        chunk=s.query(KnowledgeChunk).filter_by(generation='s'*64).one();chunk.status='ready';s.get(KnowledgeDocument,chunk.document_id).status='active';cid=str(chunk.id)
    assert _current_rows([cid],'s'*64,None,'DEMO',False)
    with Session(engine) as s,s.begin():
        row=s.get(SecFilingInventory,ident);row.content_source_url='https://example.test/replaced-exhibit'
    assert _current_rows([cid],'s'*64,None,'DEMO',False)==[], 'stale citation provenance must be rejected, not validated solely by excerpt raw hash'

def test_approved_public_candidate_can_be_retrieved(disposable_database,monkeypatch):
    from app.knowledge.extraction import review_candidate
    from app.knowledge.models import KnowledgeCandidate
    migrate(engine)
    with Session(engine) as s,s.begin():
        row=KnowledgeCandidate(dedupe_key=uuid4().hex,scope='private',owner_hash='a'*64,conversation_id=str(uuid4()),turn_id=str(uuid4()),evidence_ref='review:source',evidence_hash='c'*64,question='How do I read a saved source?',answer='Open its original source link.',category='usage',candidate_kind='product_faq',evidence_quote='Open its original source link.',status='pending_review')
        s.add(row);s.flush();review_candidate(s,row.id,'approve','independent-reviewer','public',Tokenizer(),'p'*64)
        chunk=s.query(KnowledgeChunk).filter_by(generation='p'*64).one();chunk.status='ready';doc=s.get(KnowledgeDocument,chunk.document_id);doc.status='active';cid=str(chunk.id);did=doc.id
    assert _current_rows([cid],'p'*64,None,None,True), 'human approved public product FAQ must survive authority validation'
    import asyncio
    import app.knowledge.retrieval as retrieval
    class Runtime:
        threshold=0.0
        settings=type('Settings',(),{'generation':'p'*64})()
        async def encode(self,_):return [0.1,0.2]
        async def search(self,*_):return [{'id':cid,'distance':0.9}]
    monkeypatch.setattr(retrieval,'get_runtime',lambda:Runtime())
    positive=asyncio.run(retrieval.retrieve_faq('How do I read a saved source?'))
    assert positive['data'] and positive['data'][0]['answer']=='[1] Open its original source link.'
    with Session(engine) as s,s.begin():
        doc=s.get(KnowledgeDocument,did)
        s.get(KnowledgeCandidate,UUID(doc.source_id)).status='rejected'
    negative=asyncio.run(retrieval.retrieve_faq('How do I read a saved source?'))
    assert negative['data']==[], 'revoked approval must block an otherwise matching ANN candidate'

def test_historical_selection_uses_frozen_sec_version_but_live_uses_newest(disposable_database):
    import hashlib
    from datetime import date
    from app.models import SecFilingInventory
    from app.knowledge.sources import adapt_sec
    cut=datetime(2026,5,1,tzinfo=UTC); ident=uuid4()
    Base.metadata.create_all(engine,tables=[SecFilingInventory.__table__]);migrate(engine)
    def row(body,url,observed):
        return {'id':ident,'symbol':'DEMO','cik':'0000000001','accession_number':'0000000001-26-888888','form':'10-K',
          'filed_at':date(2026,1,1),'accepted_at':None,'primary_document':'10k.htm','source_url':'https://example.test/10k',
          'source':'synthetic','review_status':'accepted','reviewed_at':observed,'observed_at':observed,'content_status':'fetched',
          'content_observed_at':observed,'content_excerpt':body,'content_excerpt_sha256':hashlib.sha256(body.encode()).hexdigest(),
          'content_truncated':False,'content_source_url':url,'content_document_name':'ex99.htm','content_kind':'exhibit'}
    old_time=cut-timedelta(days=10);new_time=cut+timedelta(days=2)
    with Session(engine) as s,s.begin():
        live=row('Revenue USD 100 million FY2024.','https://example.test/old',old_time)
        s.add(SecFilingInventory(**live));s.flush();old=adapt_sec(live);ingest_documents(s,[old],Tokenizer(),'h'*64)
        new=row('Revenue USD 200 million FY2025.','https://example.test/new',new_time)
        db=s.get(SecFilingInventory,ident)
        for key,value in new.items():setattr(db,key,value)
        current=adapt_sec(new);ingest_documents(s,[current],Tokenizer(),'h'*64)
        chunks=s.query(KnowledgeChunk).filter_by(generation='h'*64).all()
        for chunk in chunks:
            chunk.status='ready';chunk.created_at=old_time if '100' in chunk.answer else new_time
            doc=s.get(KnowledgeDocument,chunk.document_id);doc.created_at=old_time if '100' in chunk.answer else new_time
            doc.status='active' if '200' in chunk.answer else 'superseded'
        old_id=next(str(c.id) for c in chunks if '100' in c.answer)
        new_id=next(str(c.id) for c in chunks if '200' in c.answer)
    from app.knowledge.runtime import ResearchContext
    from app.knowledge.retrieval import _eligible_ann_ids
    historical=_current_rows([old_id,new_id],'h'*64,ResearchContext('a'*64,cut),'DEMO',False,historical=True)
    live=_current_rows([old_id,new_id],'h'*64,None,'DEMO',False)
    assert set(_eligible_ann_ids('h'*64,ResearchContext('a'*64,cut),'DEMO',False,True,cut))=={old_id}
    assert set(_eligible_ann_ids('h'*64,None,'DEMO',False,False,datetime.now(UTC)))=={new_id}
    assert [chunk.answer for chunk,_ in historical]==['Revenue USD 100 million FY2024.']
    assert [chunk.answer for chunk,_ in live]==['Revenue USD 200 million FY2025.']

def test_extraction_cli_progresses_past_first_batch(disposable_database,monkeypatch):
    import scripts.extract_knowledge as cli
    migrate(engine)
    called=[]
    def provider(payload):called.append(payload['turn_id']);return {'candidates':[]}
    turns=[{'turn_id':f'turn-{i:02d}','created_at':f'2026-10-01T00:00:{i:02d}+00:00','status':'complete','messages':[{'role':'user','content':'How do I use a source?','status':'complete'},{'role':'assistant','content':'Open its source link.','status':'complete'}]} for i in range(52)]
    convo={'conversation_id':str(uuid4()),'owner_hash':'a'*64,'opted_in':True,'turns':turns}
    monkeypatch.setattr(cli,'create_operator_provider',lambda:provider)
    monkeypatch.setattr(cli,'opted_in_turns',lambda ids:[convo])
    cli.run_once([convo['conversation_id']]);cli.run_once([convo['conversation_id']])
    assert len(set(called))==52 and len(called)==52, 'scoped persistent cursor must advance beyond 50 and avoid paying for repeated first-batch calls'
