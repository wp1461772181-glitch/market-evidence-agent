"""Ingest only explicitly supplied source adapters; never scans legacy uploads."""
import argparse
from sqlalchemy import select,or_,and_
from app.database import SessionLocal
from app.knowledge.repository import embedding_generation, ingest_documents
from app.knowledge.chunking import CHUNKER_VERSION, FORMAT_VERSION
from app.knowledge.sources import adapt_uploaded, adapt_sec, adapt_faq
from app.knowledge.analysis_sources import load_analysis_document
from app.knowledge.models import KnowledgeChunk,KnowledgeDocument
from app.knowledge.runtime import get_tokenizer
from app.models import UploadedEvidence, SecFilingInventory
from app.agent_chat_models import AgentFAQ
def main():
 p=argparse.ArgumentParser(); p.add_argument('--source','--type',dest='source',choices=['faq','upload','sec','analysis'],required=True); p.add_argument('--id','--source-id',dest='id',action='append',required=True); p.add_argument('--allow-public',action='store_true'); p.add_argument('--index',action='store_true',help='index only chunks from the explicitly selected imported sources'); a=p.parse_args()
 gen=embedding_generation('BAAI/bge-m3','5617a9f61b028005a4858fdac845db406aefb181',FORMAT_VERSION,CHUNKER_VERSION)
 chunk_ids=set()
 with SessionLocal.begin() as s:
  docs=[]
  for key in a.id:
   if a.source=='faq':
    row=s.get(AgentFAQ,int(key))
    if row: docs.append(adapt_faq({'id':row.id,'question':row.question,'answer':row.answer,'category':row.category}))
   elif a.source=='upload':
    row=s.get(UploadedEvidence,key)
    if row:
     owner=s.execute(__import__('sqlalchemy').text('SELECT owner_hash FROM knowledge_upload_owners WHERE upload_id=:id'),{'id':key}).scalar()
     doc=adapt_uploaded({'id':row.id,'symbol':row.symbol,'title':row.title,'source_url':row.source_url,'published_at':row.published_at,'observed_at':row.observed_at,'content_sha256':row.content_sha256,'content_text':row.content_text},owner_hash=owner,allowlist={key} if a.allow_public else set())
     if doc:docs.append(doc)
   else:
    if a.source=='analysis':
     doc=load_analysis_document(s,key)
     if doc:docs.append(doc)
     continue
    row=s.get(SecFilingInventory,key)
    if row:
     doc=adapt_sec({c.key:getattr(row,c.key) for c in row.__table__.columns})
     if doc:docs.append(doc)
  counts=ingest_documents(s,docs,get_tokenizer(),gen)
  print(counts)
  if a.index and docs:
   selected=or_(*[and_(KnowledgeDocument.source_table==d.source_table,KnowledgeDocument.source_id==d.source_id,KnowledgeDocument.source_version==d.source_version) for d in docs])
   pairs=s.execute(select(KnowledgeChunk.id).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id).where(
    selected,KnowledgeChunk.generation==gen)).all()
   chunk_ids={row[0] for row in pairs}
 if a.index and chunk_ids:
   from app.knowledge.embedding import DenseEmbedder
   from app.knowledge.indexing import index_pending
   from app.knowledge.settings import KnowledgeSettings
   from app.knowledge.vector_store import MilvusIndex
   settings=KnowledgeSettings.from_env(validate_model=True); embedder=DenseEmbedder(settings.model_path,cpu_threads=settings.cpu_threads)
   index=MilvusIndex(settings.milvus_uri,gen)
   try: print(index_pending(SessionLocal,embedder,index,gen,max_tasks=200,chunk_ids=chunk_ids))
   finally:index.close()
if __name__=='__main__':main()
