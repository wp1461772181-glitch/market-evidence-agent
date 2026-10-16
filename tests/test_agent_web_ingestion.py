import asyncio,uuid
from datetime import UTC,datetime
from sqlalchemy import select
from app.database import Base,engine,SessionLocal
from app.agent_workflow.models import AgentWebSource
from app.knowledge.models import KnowledgeDocument

def test_web_snapshot_deduplicates_url_and_body_and_keeps_type(disposable_database):
    from app.agent_workflow.web_sources import save_snapshot,as_document
    Base.metadata.create_all(engine,tables=[AgentWebSource.__table__,KnowledgeDocument.__table__])
    identity='a'*64;url='https://example.org/news?b=2&a=1#top'
    page={'url':url,'title':'Micron revenue news','excerpt':'Micron revenue increased.','published_at':'2026-10-14T12:00:00Z','fetched_at':datetime.now(UTC).isoformat(),'body_provenance':'public_page_fetch'}
    with SessionLocal() as db:
        one=save_snapshot(db,identity,page,symbol='MU',company='Micron');db.commit()
        two=save_snapshot(db,identity,{**page,'url':'https://example.org/news?a=1&b=2'},symbol='MU',company='Micron');db.commit()
        three=save_snapshot(db,identity,{**page,'url':'https://example.org/alias'},symbol='MU');db.commit()
        assert one['document_id']==two['document_id']==three['document_id']
        assert two['status']=='already_exists'
        doc=db.get(KnowledgeDocument,uuid.UUID(one['document_id']))
        assert doc.source_type=='web_news' and doc.scope=='private'
        assert doc.source_url.startswith('https://example.org')
        row=db.get(AgentWebSource,uuid.UUID(one['source_id']))
        assert row.published_at and row.fetched_at and row.content_hash
        from app.knowledge.retrieval import _source_hash
        assert _source_hash(db,doc)==doc.source_hash

def test_ingestion_rejects_blank_or_script_only_content(disposable_database):
    from app.agent_workflow.web_sources import save_snapshot
    with SessionLocal() as db:
        import pytest
        with pytest.raises(ValueError):save_snapshot(db,'a'*64,{'url':'https://example.org','excerpt':''})

def test_saved_web_survives_sync_index_retrieval_and_owner_citation_restore(disposable_database,monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from app.agent_chat_store import owner_hash
    from app.agent_workflow.web_sources import save_snapshot
    from app.knowledge.source_sync import sync_source_batch
    from app.knowledge.indexing import index_pending
    from app.knowledge.runtime import research_context
    from app.knowledge import retrieval
    from app.agent_chat_stream import _restore_citations
    from test_knowledge_indexing import Tok,Embed,Store
    Base.metadata.create_all(engine)
    generation=uuid.uuid4().hex*2;credential='web-pipeline-owner';identity=owner_hash(credential)
    page={'url':'https://example.org/pipeline/'+uuid.uuid4().hex,'title':'CWEB update','excerpt':'CWEB published its engineering update.','published_at':'2026-10-14T00:00:00Z','fetched_at':datetime.now(UTC).isoformat()}
    with SessionLocal() as db:
        saved=save_snapshot(db,identity,page,symbol='CWEB',company='CWEB');db.commit()
    factory=lambda:Session(engine)
    for _ in range(12):sync_source_batch(factory,Tok(),generation,source_type='web',batch_size=10)
    store=Store();out=index_pending(factory,Embed(),store,generation,max_tasks=200)
    assert out['indexed']>=1 and out['failed']==0
    class Runtime:
        threshold=0.0;settings=SimpleNamespace(strategy='dense',generation=generation)
        async def encode(self,query):return [0.1]*1024
        async def search(self,vector,filter_expr,limit):
            assert '"web_news"' in filter_expr
            return [{'id':k,'distance':1.0} for k in store.rows]
    monkeypatch.setattr(retrieval,'get_runtime',lambda:Runtime())
    async def find(owner):
        citations=[]
        with research_context(owner,citations=citations):found=await retrieval.retrieve_materials('CWEB','CWEB engineering update')
        return found,citations
    found,citations=asyncio.run(find(identity))
    assert found['data'][0]['source_type']=='web_news' and found['data'][0]['source_url']==page['url']
    assert citations and _restore_citations(citations,credential)[0]['source_type']=='web_news'
    assert _restore_citations(citations,'other-owner')[0]['source_type']=='unavailable'
    assert asyncio.run(find(owner_hash('other-owner')))[0]['data']==[]
