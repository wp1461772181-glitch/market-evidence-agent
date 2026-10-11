from __future__ import annotations
import hashlib, json, uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlalchemy import and_, or_, select, update, func
from sqlalchemy.orm import Session
from .chunking import build_chunks
from .models import KnowledgeCandidate, KnowledgeChunk, KnowledgeDocument, KnowledgeExtractionRun, KnowledgeIndexTask, KnowledgeUploadOwner
from .sources import ChunkDraft, SourceDocument

def embedding_generation(model_id: str, revision: str, format_version: str, chunker_version: str) -> str:
    if not revision or revision.lower()=='latest': raise ValueError('embedding revision must be immutable')
    material='|'.join((model_id.strip().lower(),revision,format_version,chunker_version))
    return hashlib.sha256(material.encode()).hexdigest()

def document_integrity(doc):
    fields={key:getattr(doc,key) for key in ('source_table','source_id','source_version','source_hash','source_type','symbol','scope','visibility','owner_hash','title','source_url','published_at','observed_at','content_observed_at','available_at','raw_length','truncated','parser','source_text')}
    for key,value in fields.items():
        if isinstance(value,datetime): fields[key]=value.isoformat()
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def repair_faq_availability(session: Session) -> int:
    """Repair legacy FAQ timestamps using their original import time.

    FAQ rows have no business publication timestamp.  The document's original
    creation time is therefore the stable availability boundary; reruns must
    never advance it to the current time.
    """
    documents=session.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.source_type=='research_faq')).all()
    repaired=0
    for doc in documents:
        if doc.available_at is None:
            doc.available_at=doc.created_at
            doc.snapshot_integrity=document_integrity(doc)
            repaired+=1
        chunks=session.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.document_id==doc.id,KnowledgeChunk.available_at.is_(None))).all()
        for chunk in chunks:
            chunk.available_at=doc.available_at
            repaired+=1
    return repaired

@dataclass(frozen=True)
class IndexLease:
    task_id: uuid.UUID
    chunk_id: uuid.UUID
    generation: str
    lease_epoch: int
    lease_owner: str
    source_hash: str
    content_hash: str
    embedding_text: str

@dataclass(frozen=True)
class VectorReceipt:
    vector_id: str
    source_hash: str
    content_hash: str
    generation: str

def ingest_documents(session: Session, documents: list[SourceDocument], tokenizer, generation: str) -> dict[str,int]:
    repair_faq_availability(session)
    counts={'documents':0,'chunks':0,'needs_review':0,'skipped':0}
    for source in documents:
        existing=session.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.source_table==source.source_table,KnowledgeDocument.source_id==source.source_id,
            KnowledgeDocument.source_version==source.source_version,KnowledgeDocument.source_hash==source.source_hash))
        if existing:
            # A source snapshot can be reused only when this generation already has chunks.
            if session.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id==existing.id,KnowledgeChunk.generation==generation)):
                counts['skipped']+=1; continue
            doc=existing
            session.execute(update(KnowledgeDocument).where(KnowledgeDocument.source_table==source.source_table,
              KnowledgeDocument.source_id==source.source_id,KnowledgeDocument.id!=existing.id).values(active=False,status='superseded'))
            doc.active=True;doc.status='pending'
        else:
            doc=None
        if doc is None:
            session.execute(update(KnowledgeDocument).where(KnowledgeDocument.source_table==source.source_table,
              KnowledgeDocument.source_id==source.source_id,KnowledgeDocument.active.is_(True)).values(active=False,status='superseded'))
            doc=KnowledgeDocument(source_table=source.source_table,source_id=source.source_id,source_version=source.source_version,
              source_hash=source.source_hash,source_type=source.source_type,symbol=source.symbol,scope=source.scope,visibility=source.visibility,
              owner_hash=source.owner_hash,title=source.title,source_url=source.source_url,published_at=source.published_at,observed_at=source.observed_at,
              content_observed_at=source.content_observed_at,available_at=source.available_at or (datetime.now(UTC) if source.source_type=='research_faq' else None),raw_length=len(source.text),truncated=source.truncated,
              parser=source.parser,source_text=source.text,active=True,status='pending',created_at=datetime.now(UTC))
            session.add(doc); session.flush(); counts['documents']+=1
        doc.snapshot_integrity=document_integrity(doc)
        drafts=build_chunks(source,tokenizer)
        persisted=[]
        for draft in drafts:
            collision_key='|'.join((draft.id,generation,source.scope,source.owner_hash or ''))
            chunk_id=uuid.uuid5(uuid.UUID('d26df052-bc20-4d4d-8b9c-3156b7e9e31f'),collision_key)
            chunk=KnowledgeChunk(id=chunk_id,document_id=doc.id,generation=generation,ordinal=draft.ordinal,
              start_offset=draft.start_offset,end_offset=draft.end_offset,source_hash=source.source_hash,scope=source.scope,visibility=source.visibility,
              owner_hash=source.owner_hash,symbol=source.symbol,source_type=source.source_type,available_at=doc.available_at,content_hash=draft.content_hash,
              category=draft.category,questions=draft.questions,answer=draft.answer,embedding_text=draft.embedding_text,
              section_path=draft.section_path,content_type=draft.content_type,needs_review=draft.needs_review,
              status='needs_review' if draft.needs_review else 'pending')
            chunk.is_key_clause=any(term in draft.answer.casefold() for term in ('revenue','income','cash flow','ebitda','毛利','营业收入','现金流','shall ','must ','may not '))
            session.add(chunk); session.flush(); persisted.append(chunk); counts['chunks']+=1
            if draft.needs_review: counts['needs_review']+=1
            else: session.add(KnowledgeIndexTask(chunk_id=chunk.id,generation=generation,status='pending'))
        for index,chunk in enumerate(persisted):
            chunk.previous_chunk_id=persisted[index-1].id if index else None
            chunk.next_chunk_id=persisted[index+1].id if index+1<len(persisted) else None
    return counts

def claim_task(session: Session, generation: str, now: datetime | None=None, *, lease_seconds: int=90, worker_id: str='worker') -> IndexLease | None:
    now=now or datetime.now(UTC)
    for task in session.scalars(select(KnowledgeIndexTask).where(KnowledgeIndexTask.generation==generation,
      or_(KnowledgeIndexTask.status=='pending',and_(KnowledgeIndexTask.status=='failed',KnowledgeIndexTask.attempts<4,
      or_(KnowledgeIndexTask.next_attempt_at.is_(None),KnowledgeIndexTask.next_attempt_at<=now)),
      and_(KnowledgeIndexTask.status=='leased',KnowledgeIndexTask.lease_until<=now)))
      .order_by(KnowledgeIndexTask.created_at,KnowledgeIndexTask.id).with_for_update(skip_locked=True).limit(100)).all():
        chunk=session.get(KnowledgeChunk,task.chunk_id)
        doc=session.get(KnowledgeDocument,chunk.document_id) if chunk else None
        if not chunk or not doc or chunk.status in {'superseded','withdrawn','needs_review'} or not doc.active or doc.status=='withdrawn':
            task.status='complete'; task.error_code='source_not_indexable'
            continue
        break
    else:return None
    task.status='leased'; task.lease_epoch+=1; task.lease_owner=worker_id; task.lease_until=now+timedelta(seconds=lease_seconds); task.attempts+=1;task.next_attempt_at=None
    chunk.status='indexing'
    return IndexLease(task.id,chunk.id,generation,task.lease_epoch,worker_id,chunk.source_hash,chunk.content_hash,chunk.embedding_text)

def ack_task(session: Session, lease: IndexLease, receipt: VectorReceipt) -> bool:
    if (receipt.vector_id!=str(lease.chunk_id) or receipt.source_hash!=lease.source_hash or
        receipt.content_hash!=lease.content_hash or receipt.generation!=lease.generation): return False
    with session.begin_nested() as savepoint:
        authority=session.execute(select(KnowledgeChunk,KnowledgeDocument).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id)
          .where(KnowledgeChunk.id==lease.chunk_id,KnowledgeChunk.generation==lease.generation)).first()
        if not authority:
            savepoint.rollback();return False
        chunk,doc=authority
        if (chunk.status!='indexing' or not doc.active or doc.status in {'withdrawn','superseded'} or
            chunk.source_hash!=doc.source_hash or chunk.scope!=doc.scope or chunk.visibility!=doc.visibility or
            chunk.owner_hash!=doc.owner_hash or chunk.symbol!=doc.symbol or chunk.source_type!=doc.source_type):
            savepoint.rollback();return False
        changed=session.execute(update(KnowledgeIndexTask).where(KnowledgeIndexTask.id==lease.task_id,
          KnowledgeIndexTask.chunk_id==lease.chunk_id,KnowledgeIndexTask.generation==lease.generation,
          KnowledgeIndexTask.lease_epoch==lease.lease_epoch,KnowledgeIndexTask.lease_owner==lease.lease_owner,
          KnowledgeIndexTask.status=='leased',KnowledgeIndexTask.lease_until>datetime.now(UTC)).values(status='complete',lease_owner=None,lease_until=None,error_code=None)).rowcount
        if changed!=1:
            savepoint.rollback(); return False
        chunk_changed=session.execute(update(KnowledgeChunk).where(KnowledgeChunk.id==lease.chunk_id,KnowledgeChunk.generation==lease.generation,
          KnowledgeChunk.status=='indexing',KnowledgeChunk.source_hash==lease.source_hash,KnowledgeChunk.content_hash==lease.content_hash,
          KnowledgeChunk.scope.in_(['public','private']),KnowledgeChunk.visibility.in_(['public','private'])).values(status='ready')).rowcount
        if chunk_changed!=1:
            savepoint.rollback(); return False
        doc_id=session.scalar(select(KnowledgeChunk.document_id).where(KnowledgeChunk.id==lease.chunk_id))
        pending=session.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id==doc_id,KnowledgeChunk.generation==lease.generation,KnowledgeChunk.status.not_in(['ready','needs_review','superseded','withdrawn'])))
        doc=session.get(KnowledgeDocument,doc_id)
        if pending==0 and doc and doc.active and doc.status!='withdrawn': session.execute(update(KnowledgeDocument).where(KnowledgeDocument.id==doc_id,KnowledgeDocument.active.is_(True),KnowledgeDocument.status!='withdrawn').values(status='active'))
        savepoint.commit()
    return True

def reconcile(session: Session, vector_store, generation: str, *, repair: bool=False) -> dict:
    chunks=session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.generation==generation,KnowledgeChunk.status=='ready')).all()
    ids=[str(c.id) for c in chunks]
    receipts=vector_store.get(ids) if ids else {}
    missing=[]; mismatched=[]
    for chunk in chunks:
        got=receipts.get(str(chunk.id))
        if got is None: missing.append(str(chunk.id))
        elif got.get('source_hash')!=chunk.source_hash or got.get('content_hash')!=chunk.content_hash or got.get('generation')!=generation: mismatched.append(str(chunk.id))
    if repair:
        for value in missing+mismatched:
            chunk=session.get(KnowledgeChunk,uuid.UUID(value))
            if not chunk:continue
            chunk.status='pending'
            task=session.scalar(select(KnowledgeIndexTask).where(KnowledgeIndexTask.chunk_id==chunk.id,KnowledgeIndexTask.generation==generation))
            if task: task.status='pending';task.lease_owner=None;task.lease_until=None;task.error_code='vector_reconcile_required'
            else:session.add(KnowledgeIndexTask(chunk_id=chunk.id,generation=generation,status='pending'))
    return {'ready':len(chunks),'missing':missing,'mismatched':mismatched,'ok':not missing and not mismatched,'requeued':len(missing)+len(mismatched) if repair else 0}
