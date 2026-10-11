from datetime import UTC, datetime
from uuid import uuid4
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.testclient import TestClient

def _client():
    from app.knowledge.api import router
    app=FastAPI();app.include_router(router);return TestClient(app)


def test_knowledge_list_is_chat_session_scoped(disposable_database):
    client=_client()
    from app.database import engine
    from app.knowledge.models import KnowledgeDocument
    from app.agent_chat_store import owner_hash
    from scripts.migrate_knowledge import migrate
    migrate(engine)
    private_id=uuid4(); public_id=uuid4()
    with Session(engine) as session, session.begin():
        session.add_all([
            KnowledgeDocument(id=private_id,source_table='fixture',source_id='private',source_version='v1',source_hash='a'*64,source_type='uploaded_media',scope='private',visibility='private',owner_hash=owner_hash('A'*32),title='private title',raw_length=1,truncated=False,parser='test',source_text='secret body',active=True,status='active',created_at=datetime.now(UTC)),
            KnowledgeDocument(id=public_id,source_table='fixture',source_id='public',source_version='v1',source_hash='b'*64,source_type='uploaded_media',scope='public',visibility='public',owner_hash=None,title='public title',raw_length=1,truncated=False,parser='test',source_text='public body',active=True,status='active',created_at=datetime.now(UTC)),
        ])
    own=client.get('/knowledge/documents',headers={'X-Chat-Session':'A'*32})
    other=client.get('/knowledge/documents',headers={'X-Chat-Session':'B'*32})
    assert own.status_code==other.status_code==200
    assert {x['title'] for x in own.json()['items']}=={'private title','public title'}
    assert {x['title'] for x in other.json()['items']}=={'public title'}
    assert 'secret body' not in own.text and 'secret body' not in other.text
    with Session(engine) as session, session.begin():
        session.query(KnowledgeDocument).filter(KnowledgeDocument.id.in_([private_id,public_id])).delete(synchronize_session=False)


def test_knowledge_write_requires_operation_key_and_owned_session(disposable_database, monkeypatch):
    client=_client()
    from app.database import engine
    from app.knowledge.models import KnowledgeDocument
    from app.agent_chat_store import owner_hash
    from scripts.migrate_knowledge import migrate
    migrate(engine)
    doc_id=uuid4()
    with Session(engine) as session, session.begin():
        session.add(KnowledgeDocument(id=doc_id,source_table='fixture',source_id='private-write',source_version='v1',source_hash='c'*64,source_type='uploaded_media',scope='private',visibility='private',owner_hash=owner_hash('A'*32),title='owned',raw_length=1,truncated=False,parser='test',source_text='body',active=True,status='active',created_at=datetime.now(UTC)))
    monkeypatch.setenv('APP_API_ACCESS_KEY','test-operation-key')
    url=f'/knowledge/documents/{doc_id}/withdraw'
    assert client.post(url,headers={'X-Chat-Session':'A'*32,'X-App-Access-Key':'wrong'}).status_code==401
    foreign=client.post(url,headers={'X-Chat-Session':'B'*32,'X-App-Access-Key':'test-operation-key'}); assert foreign.status_code==404, foreign.text
    assert client.post(url,headers={'X-Chat-Session':'A'*32,'X-App-Access-Key':'test-operation-key'}).json()['status']=='withdrawn'
    with Session(engine) as session, session.begin():session.query(KnowledgeDocument).filter(KnowledgeDocument.id==doc_id).delete(synchronize_session=False)


def test_citation_endpoint_checks_owner_and_current_source(disposable_database,monkeypatch):
    # Isolated router client avoids global middleware; the test DB still comes from conftest.
    client=_client();monkeypatch.setenv('APP_API_ACCESS_KEY','test-operation-key')
    from datetime import UTC,datetime
    from uuid import uuid4
    import hashlib
    from app.database import engine
    from app.agent_chat_models import AgentFAQ
    from app.knowledge.models import KnowledgeDocument,KnowledgeChunk
    from app.agent_chat_store import owner_hash
    from app.knowledge.repository import document_integrity
    from app.knowledge.settings import KnowledgeSettings
    from scripts.migrate_knowledge import migrate
    Base=__import__("app.database",fromlist=["Base"]).Base;Base.metadata.create_all(engine,tables=[AgentFAQ.__table__]);migrate(engine);now=datetime.now(UTC);answer='Synthetic citation excerpt.';question='Synthetic citation question';category='synthetic'
    doc_id,chunk_id=uuid4(),uuid4();source_hash=hashlib.sha256('\n'.join((category,question,answer)).encode()).hexdigest()
    with Session(engine) as session,session.begin():
        session.add(AgentFAQ(id=991,question=question,answer=answer,category=category));doc=KnowledgeDocument(id=doc_id,source_table='agent_faq',source_id='991',source_version=source_hash,source_hash=source_hash,source_type='research_faq',symbol=None,scope='public',visibility='public',owner_hash=None,title=question,source_url=None,published_at=None,observed_at=None,content_observed_at=None,available_at=now,raw_length=len(answer),truncated=False,parser='faq',source_text=answer,active=True,status='active',created_at=now);session.add(doc);session.flush();doc.snapshot_integrity=document_integrity(doc)
        session.add(KnowledgeChunk(id=chunk_id,document_id=doc_id,generation=KnowledgeSettings.from_env().generation,ordinal=0,start_offset=0,end_offset=len(answer),source_hash=source_hash,scope='public',visibility='public',owner_hash=None,symbol=None,source_type='research_faq',available_at=now,content_hash=hashlib.sha256(answer.encode()).hexdigest(),category=category,questions=question,answer=answer,embedding_text=answer,section_path='FAQ',content_type='text',needs_review=False,status='ready'))
    url=f'/knowledge/citations/{chunk_id}'
    good=client.get(url,headers={'X-Chat-Session':'A'*32});assert good.status_code==200 and good.json()['excerpt']==answer
    assert client.get(url,headers={'X-Chat-Session':'B'*32}).status_code==200 # public citations are intentionally readable by any chat identity
    chunks_url=f'/knowledge/documents/{doc_id}/chunks'
    admin_headers={'X-Chat-Session':'A'*32,'X-App-Access-Key':'test-operation-key'}
    assert client.get(chunks_url,headers=admin_headers).status_code==200
    with Session(engine) as session,session.begin():session.get(KnowledgeChunk,chunk_id).visibility='private'
    assert client.get(url,headers={'X-Chat-Session':'A'*32}).status_code==404
    with Session(engine) as session,session.begin():session.get(KnowledgeChunk,chunk_id).visibility='public';session.get(KnowledgeChunk,chunk_id).answer='corrupted excerpt'
    assert client.get(url,headers={'X-Chat-Session':'A'*32}).status_code==404
    assert client.get(chunks_url,headers=admin_headers).status_code==404
    with Session(engine) as session,session.begin():
        chunk=session.get(KnowledgeChunk,chunk_id);chunk.answer=answer;chunk.generation='e'*64
    assert client.get(url,headers={'X-Chat-Session':'A'*32}).status_code==404
    with Session(engine) as session,session.begin():
        chunk=session.get(KnowledgeChunk,chunk_id);chunk.generation=KnowledgeSettings.from_env().generation
        doc=session.get(KnowledgeDocument,doc_id);doc.active=False;doc.status='withdrawn'
    assert client.get(url,headers={'X-Chat-Session':'A'*32}).status_code==404
    assert client.get(chunks_url,headers=admin_headers).status_code==404
    with Session(engine) as session,session.begin():doc=session.get(KnowledgeDocument,doc_id);doc.active=True;doc.status='active';doc.snapshot_integrity=document_integrity(doc)
    # Change the authoritative FAQ row; the old citation must fail closed.
    with Session(engine) as session,session.begin():session.get(AgentFAQ,991).answer='Replacement source text.'
    assert client.get(url,headers={'X-Chat-Session':'A'*32}).status_code==404
    with Session(engine) as session,session.begin():
        session.query(KnowledgeChunk).filter(KnowledgeChunk.id==chunk_id).delete(synchronize_session=False);session.query(KnowledgeDocument).filter(KnowledgeDocument.id==doc_id).delete(synchronize_session=False);session.query(AgentFAQ).filter(AgentFAQ.id==991).delete(synchronize_session=False)

def test_admin_can_inspect_pending_and_failed_chunks_but_not_foreign_or_withdrawn(disposable_database,monkeypatch):
    import hashlib
    from app.database import Base,engine
    from app.agent_chat_models import AgentFAQ
    from app.knowledge.models import KnowledgeDocument,KnowledgeChunk
    from app.agent_chat_store import owner_hash
    from app.knowledge.repository import document_integrity
    from app.knowledge.settings import KnowledgeSettings
    from scripts.migrate_knowledge import migrate
    client=_client();monkeypatch.setenv('APP_API_ACCESS_KEY','test-operation-key')
    Base.metadata.create_all(engine,tables=[AgentFAQ.__table__]);migrate(engine);now=datetime.now(UTC)
    rows=[]
    with Session(engine) as session,session.begin():
        for i,status in enumerate(('pending','failed')):
            faq_id=992+i;doc_id,chunk_id=uuid4(),uuid4();question=f'admin inspect {status}';answer=f'{status} source body';category='inspection'
            source_hash=hashlib.sha256('\n'.join((category,question,answer)).encode()).hexdigest()
            session.add(AgentFAQ(id=faq_id,question=question,answer=answer,category=category))
            doc=KnowledgeDocument(id=doc_id,source_table='agent_faq',source_id=str(faq_id),source_version=source_hash,source_hash=source_hash,source_type='research_faq',scope='private',visibility='private',owner_hash=owner_hash('A'*32),title=question,raw_length=len(answer),truncated=False,parser='faq',source_text=answer,active=True,status=status,created_at=now)
            session.add(doc);session.flush();doc.snapshot_integrity=document_integrity(doc)
            session.add(KnowledgeChunk(id=chunk_id,document_id=doc_id,generation=KnowledgeSettings.from_env().generation,ordinal=0,start_offset=0,end_offset=len(answer),source_hash=source_hash,scope='private',visibility='private',owner_hash=owner_hash('A'*32),symbol=None,source_type='research_faq',available_at=now,content_hash=hashlib.sha256(answer.encode()).hexdigest(),category=category,questions=question,answer=answer,embedding_text=answer,section_path='FAQ',content_type='text',needs_review=False,status=status))
            rows.append((doc_id,chunk_id,status))
    for doc_id,chunk_id,status in rows:
        url=f'/knowledge/documents/{doc_id}/chunks'
        response=client.get(url,headers={'X-Chat-Session':'A'*32,'X-App-Access-Key':'test-operation-key'})
        assert response.status_code==200 and response.json()['chunks'][0]['status']==status
        assert client.get(url,headers={'X-Chat-Session':'B'*32,'X-App-Access-Key':'test-operation-key'}).status_code==404
    with Session(engine) as session,session.begin():
        doc=session.get(KnowledgeDocument,rows[0][0]);doc.active=False;doc.status='withdrawn';session.get(KnowledgeChunk,rows[0][1]).status='withdrawn'
    assert client.get(f'/knowledge/documents/{rows[0][0]}/chunks',headers={'X-Chat-Session':'A'*32,'X-App-Access-Key':'test-operation-key'}).status_code==404

def test_candidate_api_publishes_only_product_faq_and_queues_index_tasks(disposable_database,monkeypatch,tmp_path):
    from uuid import uuid4
    from app.database import engine
    from app.agent_chat_store import owner_hash
    from app.knowledge.models import KnowledgeCandidate,KnowledgeDocument,KnowledgeChunk,KnowledgeIndexTask
    from app.knowledge.settings import KnowledgeSettings
    from scripts.migrate_knowledge import migrate
    client=_client();migrate(engine)
    import base64,hashlib,json
    login=tmp_path/'personal-login.json'
    salt=b'knowledge-api-fixture-salt-32b'
    login.write_text(json.dumps({'salt':base64.b64encode(salt).decode(),'hash':base64.b64encode(hashlib.pbkdf2_hmac('sha256',b'fixture',salt,100000)).decode(),'iterations':100000,'chat_credential':'A'*72}))
    monkeypatch.setenv('PERSONAL_CHAT_LOGIN_PATH',str(login))
    monkeypatch.setenv('APP_API_ACCESS_KEY','')
    class Tok:
        def encode(self,text,**kwargs):return text.split()
    import app.knowledge.runtime as runtime
    monkeypatch.setattr(runtime,'get_tokenizer',lambda:Tok())
    original_settings=KnowledgeSettings.from_env
    monkeypatch.setattr(KnowledgeSettings,'from_env',classmethod(lambda cls,**kwargs: original_settings(validate_model=False)))
    owner='A'*72;candidate_ids=[uuid4(),uuid4()]
    with Session(engine) as session,session.begin():
        session.add_all([KnowledgeCandidate(id=candidate_ids[0],dedupe_key=uuid4().hex,scope='private',owner_hash=owner_hash(owner),conversation_id=str(uuid4()),turn_id=str(uuid4()),evidence_ref='demo:one',evidence_hash='a'*64,question='How do I read this note?',answer='Open its source.',category='usage',candidate_kind='product_faq',evidence_quote='Open its source.',status='pending_review'),KnowledgeCandidate(id=candidate_ids[1],dedupe_key=uuid4().hex,scope='private',owner_hash=owner_hash(owner),conversation_id=str(uuid4()),turn_id=str(uuid4()),evidence_ref='demo:two',evidence_hash='b'*64,question='Will the stock rise?',answer='Definitely.',category='finance',candidate_kind='financial_prediction',evidence_quote='Will the stock rise?',status='pending_review')])
    headers={'X-Chat-Session':owner}
    assert client.get('/knowledge/candidates',headers={'X-Chat-Session':'B'*32}).json()['items']==[]
    assert len(client.get('/knowledge/candidates',headers={'X-Chat-Session':owner}).json()['items'])==2
    refused=client.post(f'/knowledge/candidates/{candidate_ids[1]}/review',headers=headers,json={'approved':True,'reviewer':'operator'})
    assert refused.status_code==422
    approved=client.post(f'/knowledge/candidates/{candidate_ids[0]}/review',headers=headers,json={'approved':True,'reviewer':'operator'})
    assert approved.status_code==200 and approved.json()['indexing']=='queued'
    with Session(engine) as session:
        candidate=session.get(KnowledgeCandidate,candidate_ids[0]);doc=session.scalar(__import__('sqlalchemy').select(KnowledgeDocument).where(KnowledgeDocument.source_id==str(candidate_ids[0])))
        chunk=session.scalar(__import__('sqlalchemy').select(KnowledgeChunk).where(KnowledgeChunk.document_id==doc.id))
        task=session.scalar(__import__('sqlalchemy').select(KnowledgeIndexTask).where(KnowledgeIndexTask.chunk_id==chunk.id))
        assert candidate.status=='approved_private' and doc.status=='pending' and chunk.status=='pending' and task.status=='pending'
        assert doc.owner_hash==candidate.owner_hash and doc.scope=='private'
        session.delete(task);session.flush();session.delete(chunk);session.delete(doc);session.delete(candidate)
        session.delete(session.get(KnowledgeCandidate,candidate_ids[1]));session.commit()

def test_permission_scoped_retrieval_test_endpoint_uses_production_retrieval(monkeypatch):
    import asyncio
    import app.knowledge.runtime as runtime
    import app.knowledge.retrieval as retrieval
    from app.knowledge.api import RetrievalTestRequest,retrieval_test
    monkeypatch.setattr(runtime,'get_runtime',lambda:object())
    async def fake_materials(symbol,keyword):return {'ok':True,'data':[{'symbol':symbol,'excerpt':keyword}],'source':'fixture','as_of':None}
    monkeypatch.setattr(retrieval,'retrieve_materials',fake_materials)
    response=asyncio.run(retrieval_test(RetrievalTestRequest(kind='material',keyword='saved report',symbol='DEMO'),'A'*32))
    assert response['result']['data']==[{'symbol':'DEMO','excerpt':'saved report'}]
    assert response['citations']==[]
