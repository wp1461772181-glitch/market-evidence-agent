"""Seed one clearly synthetic FAQ into the isolated Chapter 3/4 demo database and index."""
import os,sys
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.agent_chat_models import AgentFAQ
from app.database import Base, engine, SessionLocal
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.indexing import index_pending
from app.knowledge.repository import ingest_documents
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.sources import adapt_faq
from app.knowledge.vector_store import MilvusIndex

settings=KnowledgeSettings.from_env(validate_model=True)
faq={'id':980041,'question':'What synthetic revenue does DEMO report for FY2025?',
     'answer':'Demo-only synthetic material: DEMO reported FY2025 revenue of USD 123 million. This is fictional and is not investment evidence.',
     'category':'isolated demo synthetic fixture'}
Base.metadata.create_all(bind=engine,tables=[AgentFAQ.__table__])
with SessionLocal() as session,session.begin():
    current=session.get(AgentFAQ,faq['id'])
    if current and (current.question,current.answer,current.category)!=(faq['question'],faq['answer'],faq['category']):
        raise RuntimeError('demo FAQ id is already occupied by different content')
    if not current:session.add(AgentFAQ(**faq))
embedder=DenseEmbedder(settings.model_path,cpu_threads=settings.cpu_threads)
with SessionLocal() as session,session.begin():
    ingest_documents(session,[adapt_faq(faq)],embedder.tokenizer,settings.generation)
index=MilvusIndex(settings.milvus_uri,settings.generation)
try:
    index.ensure_collection(settings.generation)
    result=index_pending(SessionLocal,embedder,index,settings.generation,max_tasks=100,worker_id='ch34-demo-seed')
    if result.get('failed'):raise RuntimeError('demo index task failed; inspect its safe error code in the test database')
finally:index.close()
print(f"Synthetic demo FAQ indexed: chunks={result.get('indexed',0)}, generation={settings.generation}")
