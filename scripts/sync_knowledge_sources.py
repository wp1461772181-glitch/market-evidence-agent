#!/usr/bin/env python3
"""Queue explicitly selected saved sources; repeated runs are idempotent."""
from __future__ import annotations
import argparse
from uuid import UUID

from app.database import SessionLocal
from app.agent_chat_models import AgentFAQ
from app.knowledge.repository import ingest_documents
from app.knowledge.runtime import get_tokenizer
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.sources import adapt_faq, adapt_sec
from app.knowledge.analysis_sources import load_analysis_document
from sqlalchemy import and_, or_, select
from app.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.models import SecFilingInventory
from app.material_analysis_models import MaterialAnalysisVersion

def _selected_chunk_ids(session, docs, generation):
    if not docs:
        return set()
    identities=[and_(KnowledgeDocument.source_table==doc.source_table,
                     KnowledgeDocument.source_id==doc.source_id,
                     KnowledgeDocument.source_version==doc.source_version,
                     KnowledgeDocument.source_hash==doc.source_hash) for doc in docs]
    return set(session.scalars(select(KnowledgeChunk.id).join(
        KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id
    ).where(KnowledgeChunk.generation==generation,or_(*identities))).all())

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--type',choices=('sec','faq','analysis'),required=True)
    parser.add_argument('--source-id',action='append',required=True,help='repeat for each exact database source ID')
    parser.add_argument('--index',action='store_true',help='process queued tasks using the pinned local BGE/Milvus runtime')
    parser.add_argument('--batch-size',type=int,default=25)
    args=parser.parse_args()
    if len(args.source_id)>min(100,args.batch_size):parser.error('source batch exceeds the configured bound')
    settings=KnowledgeSettings.from_env(validate_model=False)
    docs=[];ignored=[]
    chunk_ids=set()
    with SessionLocal() as session:
        for raw_id in args.source_id:
            try:key=UUID(raw_id) if args.type not in {'faq'} else int(raw_id)
            except ValueError:ignored.append({'id':raw_id,'status':'invalid_id'});continue
            if args.type=='analysis':
                doc=load_analysis_document(session,key)
            elif args.type=='sec':
                row=session.get(SecFilingInventory,key)
                doc=adapt_sec({c.key:getattr(row,c.key) for c in row.__table__.columns}) if row else None
            elif args.type=='faq':
                row=session.get(AgentFAQ,key)
                doc=adapt_faq({'id':row.id,'question':row.question,'answer':row.answer,'category':row.category}) if row else None
            if doc:docs.append(doc)
            else:ignored.append({'id':raw_id,'status':'not_eligible_or_not_owned'})
        counts=ingest_documents(session,docs,get_tokenizer(),settings.generation)
        chunk_ids=_selected_chunk_ids(session,docs,settings.generation)
        session.commit()
    result={'requested':len(args.source_id),'accepted_sources':len(docs),'ignored':ignored,**counts}
    if args.index:
        if not docs or not chunk_ids:
            result['index']={'indexed':0,'failed':0,'idle':0}
            print(result)
            return 0
        from app.knowledge.embedding import DenseEmbedder
        from app.knowledge.indexing import index_pending
        from app.knowledge.vector_store import MilvusIndex
        from app.knowledge.runtime import get_runtime
        runtime=get_runtime();embedder=runtime.embedder if runtime else DenseEmbedder(settings.model_path,cpu_threads=settings.cpu_threads)
        index=runtime.index if runtime else MilvusIndex(settings.milvus_uri,settings.generation)
        try:result['index']=index_pending(SessionLocal,embedder,index,settings.generation,max_tasks=min(200,args.batch_size),chunk_ids=chunk_ids)
        finally:
            if runtime is None:
                index.close();embedder.release_model()
    print(result)
    return 0

if __name__=='__main__':raise SystemExit(main())
