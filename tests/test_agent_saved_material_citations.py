"""A material cited from the search used when knowledge retrieval is off stays live while it is unchanged."""
import uuid
from datetime import UTC,datetime
from app.database import Base,engine,SessionLocal

def _saved(owner_credential):
    from app.manual_evidence import create_uploaded_evidence
    from app.knowledge.models import KnowledgeUploadOwner
    from app.agent_chat_store import owner_hash
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        evidence,_=create_uploaded_evidence(symbol='MSFT',title='Copilot 改版观察',source_url='https://example.com/copilot',
            published_at=datetime(2026,9,25,tzinfo=UTC),credibility_stars=3,credibility_reason='整理摘要',impact_severity='medium',
            filename='copilot.md',content=('微软为 Copilot 增加代码生成和 Office 智能体工具；功能面向企业客户分批推出。编号 '+uuid.uuid4().hex).encode(),db=db)
        db.add(KnowledgeUploadOwner(upload_id=evidence.id,owner_hash=owner_hash(owner_credential)));db.commit()
        return str(evidence.id),evidence.content_text

def _citation(source_id,quote):
    return {'ordinal':1,'source_type':'saved_material','source_id':source_id,'title':'Copilot 改版观察','quote':quote,
            'source_url':'https://example.com/copilot','published_at':'2026-09-25T00:00:00+00:00','derived':False}

def test_a_saved_material_quote_from_its_text_is_live(disposable_database):
    from app.agent_chat_stream import _restore_citations
    source_id,text=_saved('owner-secret')
    restored=_restore_citations([_citation(source_id,text[:1200])],'owner-secret')
    assert restored[0]['source_type']=='saved_material' and restored[0]['title']=='Copilot 改版观察'

def test_a_saved_material_quote_that_is_not_in_its_text_expires(disposable_database):
    from app.agent_chat_stream import _restore_citations
    source_id,_=_saved('owner-secret')
    assert _restore_citations([_citation(source_id,'微软宣布裁员 50%。')],'owner-secret')[0]['source_type']=='unavailable'

def test_another_owners_material_is_not_restored(disposable_database):
    from app.agent_chat_stream import _restore_citations
    source_id,text=_saved('owner-secret')
    assert _restore_citations([_citation(source_id,text[:1200])],'someone-else')[0]['source_type']=='unavailable'
