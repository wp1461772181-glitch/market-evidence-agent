import asyncio,uuid
from datetime import UTC,datetime,timedelta
import pytest
from app.database import Base,engine,SessionLocal
from app.agent_workflow import tools
from app.agent_workflow.models import AgentWebSource
from app.agent_workflow.state import AgentRuntimeContext
from app.knowledge.runtime import current_research_context

@pytest.fixture
def ready(disposable_database):Base.metadata.create_all(engine)

def test_company_free_prelude_reads_faq_and_preserves_dense_citation_manifest(ready,monkeypatch):
    import app.agent_tools as legacy
    calls=[]
    async def faq(args):
        calls.append(args);context=current_research_context()
        context.citations.append({'ordinal':1,'source_id':'42','title':'可信度星级','source_type':'research_faq','chunk_id':str(uuid.uuid4()),'quote':'Stars rate source credibility.'})
        return {'ok':True,'data':[{'question':'可信度星级','answer':'[1] Stars rate source credibility.'}]}
    from types import SimpleNamespace
    monkeypatch.setattr(legacy,'query_research_faq',SimpleNamespace(ainvoke=faq))
    result=asyncio.run(tools.search_knowledge({'query':'可信度星级'},AgentRuntimeContext('custom','test','dummy',owner_hash='owner')))
    assert calls and result['data'][0]['question']=='可信度星级'
    assert result['_citations'][0]['source_id']=='42'

def test_faq_chunk_is_bound_to_its_row_when_the_row_has_no_source_id():
    from app.agent_workflow.direct import Turn
    async def emit(_):pass
    turn=Turn('可信度星级',AgentRuntimeContext('custom','test','dummy'),emit,[],{})
    outcome={'ok':True,'data':[{'question':'可信度星级','answer':'[1] Stars rate credibility.'}],
        '_citations':[{'ordinal':1,'source_id':'42','title':'可信度星级','source_type':'research_faq','chunk_id':'chunk','quote':'Stars rate credibility.'}]}
    turn.accept('search_knowledge',{'query':'可信度星级'},outcome)
    assert turn.citations[0]['chunk_id']=='chunk' and turn.citations[0]['source_id']=='42'
