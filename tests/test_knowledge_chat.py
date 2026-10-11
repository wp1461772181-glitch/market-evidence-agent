from app.agent_tools import TOOL_REGISTRY,tool_schemas
import pytest

def test_tool_contract_and_disabled_runtime_failure():
    from app.agent_tools import query_materials,query_research_faq
    import asyncio
    material=TOOL_REGISTRY['query_materials'].args_schema.model_json_schema()
    faq=TOOL_REGISTRY['query_research_faq'].args_schema.model_json_schema()
    assert set(material['properties'])=={'symbol','keyword','limit'}
    assert material['properties']['limit']['default']==5
    assert set(faq['properties'])=={'keyword'}
    assert [x['function']['name'] for x in tool_schemas()]==list(TOOL_REGISTRY)
    result=asyncio.run(query_research_faq.ainvoke({'keyword':'费用'}))
    assert result['error_code']=='knowledge_unavailable' and result['data'] is None
    result=asyncio.run(query_materials.ainvoke({'symbol':'DEMO','keyword':'FY2025收入'}))
    assert result['error_code']=='knowledge_unavailable' and result['data'] is None

def test_research_context_is_scoped_and_reset():
    from app.knowledge.runtime import current_research_context,research_context
    assert current_research_context() is None
    with research_context('a'*64):
        assert current_research_context().owner_hash=='a'*64
        with research_context('b'*64):
            assert current_research_context().owner_hash=='b'*64
        assert current_research_context().owner_hash=='a'*64
    assert current_research_context() is None

def test_startup_never_creates_knowledge_tables_implicitly(monkeypatch):
    import app.main as main
    captured={}
    monkeypatch.setenv('RAG_ENABLED','false')
    monkeypatch.setattr(main.Base.metadata,'create_all',lambda bind,tables:captured.update(names=[t.name for t in tables]))
    monkeypatch.setattr(main,'create_sec_filing_inventory_table',lambda:None)
    monkeypatch.setattr(main,'create_uploaded_evidence_table',lambda:None)
    monkeypatch.setattr(main,'create_evidence_revision_tables',lambda:None)
    monkeypatch.setattr(main,'seed_faq',lambda session:None)
    class FakeSession:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def commit(self):pass
    monkeypatch.setattr(main,'SessionLocal',FakeSession)
    main.create_tables()
    assert not any(name.startswith('knowledge_') for name in captured['names'])

def test_historical_cutoff_rejects_naive_and_future_values(monkeypatch):
    import asyncio
    from datetime import UTC,datetime,timedelta
    import app.knowledge.retrieval as retrieval
    from app.knowledge.runtime import ResearchContext
    class Runtime: pass
    monkeypatch.setattr(retrieval,'get_runtime',lambda:Runtime())
    for cutoff in (datetime.now(),datetime.now(UTC)+timedelta(days=1)):
        monkeypatch.setattr(retrieval,'current_research_context',lambda cutoff=cutoff:ResearchContext('a'*64,cutoff))
        with pytest.raises(retrieval.KnowledgeUnavailable) as error:
            asyncio.run(retrieval.retrieve_materials('DEMO','historical question'))
        assert error.value.code=='invalid_historical_cutoff'

def test_tool_budget_keeps_complete_rows_recomputes_as_of_and_reports_no_fit():
    from app.knowledge.retrieval import _fit_tool_budget
    oversized={'ok':True,'data':[{'observed_at':'2026-10-01','excerpt':'x'*9000}],'source':'fixture','as_of':'stale'}
    assert _fit_tool_budget(oversized)['error_code']=='insufficient_result_budget'
    result={'ok':True,'data':[{'observed_at':'2026-10-02','excerpt':'x'*4100},{'observed_at':'2026-10-01','excerpt':'y'*4100}],
      'source':'fixture','as_of':'stale'}
    fitted=_fit_tool_budget(result)
    assert fitted['data']==[result['data'][0]] and fitted['as_of']=='2026-10-02'

def test_knowledge_health_reports_safe_disabled_and_ready_states(monkeypatch):
    import app.knowledge.runtime as runtime_module
    import app.main as main
    monkeypatch.setenv('RAG_ENABLED','false')
    monkeypatch.setattr(runtime_module,'get_runtime',lambda:None)
    assert main.knowledge_health()=={'enabled':False,'ready':False}
    class Runtime:
        def health(self):return {'ready':True,'generation':'g'*64,'milvus':{'ok':True}}
    monkeypatch.setattr(runtime_module,'get_runtime',lambda:Runtime())
    assert main.knowledge_health()=={'enabled':True,'ready':True,'generation':'g'*64,'milvus':{'ok':True}}

def test_retrieved_faq_evidence_numbering_matches_retained_chunks_and_duplicate_titles(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import app.knowledge.retrieval as retrieval
    from app.knowledge.runtime import ResearchContext
    generation='g'*64;eligible={};hits=[]
    for source_index in range(7):
        source_id=f'source-{source_index}';title='Duplicate FAQ title' if source_index in (1,2) else f'Question {source_index}'
        chunks=4 if source_index==0 else 1
        for chunk_index in range(chunks):
            chunk_id=f'{source_id}-{chunk_index}'
            chunk=SimpleNamespace(id=chunk_id,answer=f'Answer from {source_id} chunk {chunk_index}',category='fixture',generation=generation,content_hash='c'*64,section_path='section')
            doc=SimpleNamespace(id=f'doc-{source_id}',source_id=source_id,source_table='agent_faq',title=title,source_hash='s'*64,content_observed_at=None,observed_at=None,available_at=None,source_type='research_faq',truncated=False)
            eligible[chunk_id]=(chunk,doc);hits.append({'id':chunk_id,'distance':1.0})
    class Runtime:
        threshold=0.0
        settings=SimpleNamespace(strategy='dense',generation=generation)
        async def encode(self,query):return [0.0]
        async def search(self,*args):return hits
    context=ResearchContext('a'*64,citations=[])
    monkeypatch.setattr(retrieval,'get_runtime',lambda:Runtime())
    monkeypatch.setattr(retrieval,'_eligible_ann_ids',lambda *args:eligible)
    monkeypatch.setattr(retrieval,'current_research_context',lambda:context)
    result=asyncio.run(retrieval._retrieve('faq',faq=True,limit=10))
    assert result['ok'] and len(result['data'])==7
    assert result['data'][1]['question']==result['data'][6]['question']=='Duplicate FAQ title'
    assert result['data'][0]['question']=='Question 0' and result['data'][3]['question']=='Question 6'
    assert len(context.citations)==9
    assert [item['ordinal'] for item in context.citations]==list(range(1,10))
    assert sum(item['chunk_id'].startswith('source-0-') for item in context.citations)==3
    assert all(f'[{item["ordinal"]}] {eligible[item["chunk_id"]][0].answer}' in '\n'.join(row['answer'] for row in result['data']) for item in context.citations)
