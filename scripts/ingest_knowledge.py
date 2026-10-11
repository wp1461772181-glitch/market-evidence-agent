"""Ingest only explicitly supplied source adapters; never scans legacy uploads."""
import argparse
from app.database import SessionLocal
from app.knowledge.repository import embedding_generation, ingest_documents
from app.knowledge.chunking import CHUNKER_VERSION, FORMAT_VERSION
from app.knowledge.sources import adapt_uploaded, adapt_sec, adapt_faq
from app.knowledge.runtime import get_tokenizer
from app.models import UploadedEvidence, SecFilingInventory
from app.agent_chat_models import AgentFAQ
def main():
 p=argparse.ArgumentParser(); p.add_argument('--source',choices=['faq','upload','sec'],required=True); p.add_argument('--id',action='append',required=True); p.add_argument('--allow-public',action='store_true'); a=p.parse_args()
 gen=embedding_generation('BAAI/bge-m3','5617a9f61b028005a4858fdac845db406aefb181',FORMAT_VERSION,CHUNKER_VERSION)
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
    row=s.get(SecFilingInventory,key)
    if row:
     doc=adapt_sec({c.key:getattr(row,c.key) for c in row.__table__.columns})
     if doc:docs.append(doc)
  print(ingest_documents(s,docs,get_tokenizer(),gen))
if __name__=='__main__':main()
