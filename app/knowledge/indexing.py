from __future__ import annotations
from datetime import UTC, datetime, timedelta
from uuid import UUID
from sqlalchemy.orm import Session
from .models import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexTask
from .repository import IndexLease, VectorReceipt, ack_task, claim_task

class InjectedIndexCrash(RuntimeError): pass

def index_pending(session_factory, embedder, vector_store, generation: str, *, max_tasks: int=100, crash_at: str|None=None, worker_id: str='indexer') -> dict[str,int]:
    counts={'indexed':0,'failed':0,'idle':0}
    vector_store.ensure_collection(generation)
    for _ in range(max_tasks):
        with session_factory() as session:
            lease=claim_task(session,generation,datetime.now(UTC),worker_id=worker_id)
            if lease is None:
                session.commit(); counts['idle']+=1; break
            session.commit()
        if crash_at=='after_pending': raise InjectedIndexCrash('after_pending')
        try:
            # Snapshot DB payload and close the read transaction before model/network work.
            with session_factory() as session:
                chunk=session.get(KnowledgeChunk,lease.chunk_id); doc=session.get(KnowledgeDocument,chunk.document_id) if chunk else None
                if not chunk or not doc or not doc.active or chunk.source_hash!=lease.source_hash:
                    raise ValueError('index_source_changed')
                payload={'id':str(chunk.id),'source_hash':chunk.source_hash,'content_hash':chunk.content_hash,
                  'generation':generation,'scope':chunk.scope,'visibility':chunk.visibility,
                  'owner_hash':chunk.owner_hash or '','symbol':chunk.symbol or '', 'source_type':chunk.source_type,
                  'available_at':int(doc.available_at.timestamp()*1000) if doc.available_at else 0,
                  'embedding_text':chunk.embedding_text}
            text=payload.pop('embedding_text')
            vector=embedder.encode([text])[0]
            payload['text']=text
            row={**payload,'embedding':vector}
            vector_store.upsert([row])
            if crash_at=='after_upsert': raise InjectedIndexCrash('after_upsert')
            stored=vector_store.get([str(lease.chunk_id)]).get(str(lease.chunk_id))
            receipt=VectorReceipt(str(stored.get('id')) if stored else '',stored.get('source_hash','') if stored else '',
              stored.get('content_hash','') if stored else '',stored.get('generation','') if stored else '')
            with session_factory() as session, session.begin():
                if not ack_task(session,lease,receipt): counts['failed']+=1; continue
            counts['indexed']+=1
        except InjectedIndexCrash: raise
        except Exception:
            with session_factory() as session, session.begin():
                task=session.get(KnowledgeIndexTask,lease.task_id)
                if task and task.lease_epoch==lease.lease_epoch and task.status=='leased':
                    task.status='failed'; task.error_code='index_operation_failed'
                    task.next_attempt_at=datetime.now(UTC)+timedelta(seconds=min(300,2**max(1,task.attempts)))
                    chunk=session.get(KnowledgeChunk,lease.chunk_id)
                    if chunk and chunk.status=='indexing': chunk.status='failed'
            counts['failed']+=1
    return counts
