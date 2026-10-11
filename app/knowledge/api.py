from __future__ import annotations
import hmac, os
from datetime import UTC, datetime
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..agent_chat_store import owner_hash
from .models import KnowledgeCandidate, KnowledgeChunk, KnowledgeDocument, KnowledgeIndexTask
from .repository import document_integrity, ingest_documents
from .sources import adapt_faq, adapt_sec, adapt_uploaded

router=APIRouter(prefix='/knowledge',tags=['knowledge'])

def _scope(session:Session,identity:str):
    return (KnowledgeDocument.scope=='public') | ((KnowledgeDocument.scope=='private') & (KnowledgeDocument.owner_hash==owner_hash(identity)))

def _chunk_valid(session,doc,chunk):
    import hashlib
    from .settings import KnowledgeSettings
    from .retrieval import _source_hash
    now=datetime.now(UTC)
    return (doc.active and doc.status=='active' and doc.snapshot_integrity and document_integrity(doc)==doc.snapshot_integrity
        and _source_hash(session,doc)==doc.source_hash and chunk.status=='ready' and not chunk.needs_review and chunk.available_at is not None
        and chunk.available_at<=now and chunk.generation==KnowledgeSettings.from_env().generation
        and chunk.source_hash==doc.source_hash and hashlib.sha256(chunk.answer.encode()).hexdigest()==chunk.content_hash
        and (chunk.scope,chunk.visibility,chunk.owner_hash)==(doc.scope,doc.visibility,doc.owner_hash))

def _identity(value):
    if not value or not 32<=len(value)<=256:raise HTTPException(401,'chat_session_required')
    return value

def _write_access(key:str|None,identity:str|None):
    _identity(identity)
    expected=os.getenv('APP_API_ACCESS_KEY','')
    from ..agent_chat_identity import owner_credential_matches
    legacy=bool(expected and key and hmac.compare_digest(key.encode('utf-8'),expected.encode('utf-8')))
    if not owner_credential_matches(identity) and not legacy:raise HTTPException(401,'owner_login_required')

class ReviewRequest(BaseModel):
    approved:bool
    reviewer:str=Field(min_length=1,max_length=120)

class ImportRequest(BaseModel):
    source_type:str=Field(pattern='^(faq|upload|sec)$')
    source_ids:list[str]=Field(min_length=1,max_length=20)

class IndexRequest(BaseModel):
    max_tasks:int=Field(default=100,ge=1,le=200)

class RetrievalTestRequest(BaseModel):
    kind:str=Field(pattern='^(faq|material)$')
    keyword:str=Field(min_length=1,max_length=800)
    symbol:str|None=Field(default=None,min_length=1,max_length=16)

@router.post('/retrieval-test')
async def retrieval_test(payload:RetrievalTestRequest,x_chat_session:str|None=Header(default=None)):
    identity=_identity(x_chat_session)
    from .runtime import get_runtime,research_context
    from .retrieval import KnowledgeUnavailable,retrieve_faq,retrieve_materials
    if get_runtime() is None:raise HTTPException(503,'knowledge_unavailable')
    if payload.kind=='material' and not payload.symbol:raise HTTPException(422,'symbol_required_for_material_retrieval')
    try:
        with research_context(owner_hash(identity)) as context:
            result=await retrieve_faq(payload.keyword) if payload.kind=='faq' else await retrieve_materials(payload.symbol.upper(),payload.keyword)
        return {'result':result,'citations':context.citations or []}
    except KnowledgeUnavailable as exc:
        raise HTTPException(503,exc.code) from exc

@router.get('/importable/{source_type}')
def importable(source_type:str,symbol:str|None=Query(default=None,max_length=16),x_chat_session:str|None=Header(default=None)):
    identity=_identity(x_chat_session)
    from ..agent_chat_models import AgentFAQ
    from ..models import SecFilingInventory,UploadedEvidence
    with SessionLocal() as session:
        if source_type=='faq': rows=session.scalars(select(AgentFAQ).order_by(AgentFAQ.category,AgentFAQ.id).limit(200)).all();items=[{'id':str(r.id),'title':r.question,'symbol':None,'source_type':'research_faq'} for r in rows]
        elif source_type=='sec':
            query=select(SecFilingInventory).where(SecFilingInventory.review_status=='accepted',SecFilingInventory.content_status=='fetched')
            if symbol:query=query.where(SecFilingInventory.symbol==symbol.upper())
            rows=session.scalars(query.order_by(SecFilingInventory.observed_at.desc()).limit(300)).all();items=[{'id':str(r.id),'title':r.content_document_name or r.primary_document or 'SEC filing','symbol':r.symbol,'source_type':'official_filing'} for r in rows if r.content_excerpt and r.content_excerpt_sha256]
        elif source_type=='upload':
            from .models import KnowledgeUploadOwner
            query=select(UploadedEvidence).join(KnowledgeUploadOwner,KnowledgeUploadOwner.upload_id==UploadedEvidence.id).where(KnowledgeUploadOwner.owner_hash==owner_hash(identity))
            if symbol:query=query.where(UploadedEvidence.symbol==symbol.upper())
            rows=session.scalars(query.order_by(UploadedEvidence.observed_at.desc()).limit(300)).all();items=[{'id':str(r.id),'title':r.title,'symbol':r.symbol,'source_type':'uploaded_media'} for r in rows]
        else:raise HTTPException(404,'knowledge_source_type_not_found')
        return {'items':items}

@router.get('/documents')
def list_documents(x_chat_session:str|None=Header(default=None),limit:int=Query(100,ge=1,le=500)):
    identity=_identity(x_chat_session)
    with SessionLocal() as session:
        rows=session.scalars(select(KnowledgeDocument).where(_scope(session,identity)).order_by(KnowledgeDocument.created_at.desc()).limit(limit)).all()
        return {'items':[{'id':str(d.id),'title':d.title,'symbol':d.symbol,'source_type':d.source_type,'observed_at':d.observed_at.isoformat() if d.observed_at else None,'available_at':d.available_at.isoformat() if d.available_at else None,'status':d.status,'active':d.active,'owner_scope':d.scope,'chunks':session.scalar(select(__import__('sqlalchemy').func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id==d.id))} for d in rows]}

@router.post('/import')
def import_existing(payload:ImportRequest,x_chat_session:str|None=Header(default=None),x_app_access_key:str|None=Header(default=None)):
    _write_access(x_app_access_key,x_chat_session)
    from ..agent_chat_models import AgentFAQ
    from ..models import SecFilingInventory, UploadedEvidence
    from .runtime import get_tokenizer
    from .settings import KnowledgeSettings
    identity_hash=owner_hash(x_chat_session);docs=[]
    with SessionLocal() as session:
        for source_id in payload.source_ids:
            if payload.source_type=='faq':
                try:row=session.get(AgentFAQ,int(source_id))
                except ValueError:row=None
                if row:docs.append(adapt_faq({'id':row.id,'question':row.question,'answer':row.answer,'category':row.category}))
            elif payload.source_type=='upload':
                try:row=session.get(UploadedEvidence,UUID(source_id))
                except ValueError:row=None
                if row:
                    from .models import KnowledgeUploadOwner
                    ownership=session.scalar(select(KnowledgeUploadOwner).where(KnowledgeUploadOwner.upload_id==row.id))
                    if ownership and ownership.owner_hash==identity_hash:
                        doc=adapt_uploaded({'id':row.id,'symbol':row.symbol,'title':row.title,'source_url':row.source_url,'published_at':row.published_at,'observed_at':row.observed_at,'content_sha256':row.content_sha256,'content_text':row.content_text},owner_hash=identity_hash,allowlist=set())
                        if doc:docs.append(doc)
            else:
                try:row=session.get(SecFilingInventory,UUID(source_id))
                except ValueError:row=None
                if row:
                    doc=adapt_sec({c.key:getattr(row,c.key) for c in row.__table__.columns})
                    if doc:docs.append(doc)
        settings=KnowledgeSettings.from_env(validate_model=True)
        counts=ingest_documents(session,docs,get_tokenizer(),settings.generation)
        session.commit()
    return {'ok':True,'requested':len(payload.source_ids),'accepted_sources':len(docs),**counts}

@router.post('/index')
def run_index(payload:IndexRequest,x_chat_session:str|None=Header(default=None),x_app_access_key:str|None=Header(default=None)):
    _write_access(x_app_access_key,x_chat_session)
    if os.getenv('RAG_INDEXING_ENABLED','false').lower() not in {'1','true','yes'}:raise HTTPException(409,'knowledge_indexing_disabled')
    from ..database import SessionLocal as session_factory
    from .embedding import DenseEmbedder
    from .indexing import index_pending
    from .runtime import get_runtime
    from .settings import KnowledgeSettings
    from .vector_store import MilvusIndex
    settings=KnowledgeSettings.from_env(validate_model=True);runtime=get_runtime()
    embedder=runtime.embedder if runtime else DenseEmbedder(settings.model_path,cpu_threads=settings.cpu_threads)
    index=MilvusIndex(settings.milvus_uri,settings.generation)
    try:return {'ok':True,**index_pending(session_factory,embedder,index,settings.generation,max_tasks=payload.max_tasks,worker_id='knowledge-admin')}
    finally:
        index.close()

@router.get('/documents/{document_id}/chunks')
def document_chunks(document_id:str,x_chat_session:str|None=Header(default=None),x_app_access_key:str|None=Header(default=None)):
    identity=_identity(x_chat_session)
    # Administrative inspection may reveal pending/failed source text. Require
    # the operator key, while still enforcing the document scope below.
    _write_access(x_app_access_key,identity)
    try:key=UUID(document_id)
    except ValueError:raise HTTPException(404,'knowledge_document_not_found')
    with SessionLocal() as session:
        doc=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==key,_scope(session,identity)))
        if not doc:raise HTTPException(404,'knowledge_document_not_found')
        if not doc.active or doc.status=='withdrawn' or not doc.snapshot_integrity or document_integrity(doc)!=doc.snapshot_integrity:
            raise HTTPException(404,'knowledge_document_not_found')
        # This endpoint is a privileged view of the immutable stored snapshot.
        # Retrieval/citation continues to require current-source validation.
        chunks=session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id==key).order_by(KnowledgeChunk.ordinal)).all()
        if not chunks or any(chunk.status=='withdrawn' for chunk in chunks):raise HTTPException(404,'knowledge_document_not_found')
        if doc.status=='active' and any(not _chunk_valid(session,doc,chunk) for chunk in chunks):
            raise HTTPException(404,'knowledge_document_not_found')
        return {'document':{'id':str(doc.id),'title':doc.title,'source_text':doc.source_text,'source_url':doc.source_url,'status':doc.status},'chunks':[{'id':str(c.id),'ordinal':c.ordinal,'section':c.section_path,'answer':c.answer,'status':c.status,'needs_review':c.needs_review} for c in chunks]}

@router.get('/citations/{chunk_id}')
def read_citation(chunk_id:str,x_chat_session:str|None=Header(default=None)):
    identity=_identity(x_chat_session)
    try:key=UUID(chunk_id)
    except ValueError:raise HTTPException(404,'citation_unavailable')
    from .repository import document_integrity
    from .retrieval import _source_hash
    now=datetime.now(UTC)
    with SessionLocal() as session:
        pair=session.execute(select(KnowledgeChunk,KnowledgeDocument).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id).where(KnowledgeChunk.id==key)).first()
        if not pair:raise HTTPException(404,'citation_unavailable')
        chunk,doc=pair
        permitted=(chunk.scope=='public' and doc.scope=='public' and chunk.visibility=='public' and doc.visibility=='public' and doc.owner_hash is None) or (chunk.scope=='private' and doc.scope=='private' and chunk.visibility=='private' and doc.visibility=='private' and chunk.owner_hash==owner_hash(identity) and doc.owner_hash==owner_hash(identity))
        valid=permitted and _chunk_valid(session,doc,chunk)
        if not valid:raise HTTPException(404,'citation_unavailable')
        return {'title':doc.title,'section':chunk.section_path,'excerpt':chunk.answer,'source_url':doc.source_url,'observed_at':(doc.content_observed_at or doc.observed_at or doc.available_at).isoformat() if (doc.content_observed_at or doc.observed_at or doc.available_at) else None,'coverage':'excerpt_only' if doc.truncated or doc.source_type=='official_filing' else 'stored_source','start_offset':chunk.start_offset,'end_offset':chunk.end_offset}

@router.post('/documents/{document_id}/withdraw')
def withdraw_document(document_id:str,x_chat_session:str|None=Header(default=None),x_app_access_key:str|None=Header(default=None)):
    _write_access(x_app_access_key,x_chat_session)
    try:key=UUID(document_id)
    except ValueError:raise HTTPException(404,'knowledge_document_not_found')
    with SessionLocal() as session,session.begin():
        doc=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==key,_scope(session,x_chat_session)))
        if not doc:raise HTTPException(404,'knowledge_document_not_found')
        doc.active=False;doc.status='withdrawn'
        session.execute(KnowledgeChunk.__table__.update().where(KnowledgeChunk.document_id==key).values(status='withdrawn'))
    return {'ok':True,'status':'withdrawn'}

@router.post('/documents/{document_id}/retry')
def retry_document(document_id:str,x_chat_session:str|None=Header(default=None),x_app_access_key:str|None=Header(default=None)):
    _write_access(x_app_access_key,x_chat_session)
    try:key=UUID(document_id)
    except ValueError:raise HTTPException(404,'knowledge_document_not_found')
    with SessionLocal() as session,session.begin():
        doc=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==key,_scope(session,x_chat_session)))
        if not doc or not doc.active:raise HTTPException(404,'knowledge_document_not_found')
        chunks=session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id==key,KnowledgeChunk.status.in_(['failed','pending']))).all()
        for chunk in chunks:
            chunk.status='pending'
            task=session.scalar(select(KnowledgeIndexTask).where(KnowledgeIndexTask.chunk_id==chunk.id,KnowledgeIndexTask.generation==chunk.generation))
            if task:task.status='pending';task.error_code=None;task.next_attempt_at=None
            else:session.add(KnowledgeIndexTask(chunk_id=chunk.id,generation=chunk.generation,status='pending'))
        doc.status='pending' if chunks else doc.status
    return {'ok':True,'queued_chunks':len(chunks)}

@router.get('/candidates')
def list_candidates(x_chat_session:str|None=Header(default=None),x_app_access_key:str|None=Header(default=None)):
    identity=_identity(x_chat_session)
    from ..agent_chat_identity import owner_credential_matches
    is_operator=owner_credential_matches(x_chat_session)
    with SessionLocal() as session:
        scope=(KnowledgeCandidate.status=='pending_review') if is_operator else ((KnowledgeCandidate.scope=='private')&(KnowledgeCandidate.owner_hash==owner_hash(identity))&(KnowledgeCandidate.status=='pending_review'))
        rows=session.scalars(select(KnowledgeCandidate).where(scope).order_by(KnowledgeCandidate.created_at.desc()).limit(100)).all()
        return {'items':[{'id':str(x.id),'question':x.question,'answer':x.answer,'category':x.category,'evidence_quote':x.evidence_quote} for x in rows]}

@router.post('/candidates/{candidate_id}/review')
def review_candidate(candidate_id:str,payload:ReviewRequest,x_chat_session:str|None=Header(default=None),x_app_access_key:str|None=Header(default=None)):
    _write_access(x_app_access_key,x_chat_session)
    try:key=UUID(candidate_id)
    except ValueError:raise HTTPException(404,'knowledge_candidate_not_found')
    with SessionLocal() as session,session.begin():
        row=session.scalar(select(KnowledgeCandidate).where(KnowledgeCandidate.id==key,KnowledgeCandidate.status=='pending_review',((KnowledgeCandidate.scope=='public')|((KnowledgeCandidate.scope=='private')&(KnowledgeCandidate.owner_hash==owner_hash(x_chat_session))))).with_for_update())
        if not row:raise HTTPException(404,'knowledge_candidate_not_found')
        from .extraction import review_candidate as apply_review
        try:
            if payload.approved:
                if row.candidate_kind!='product_faq':raise HTTPException(422,'only_product_faq_candidates_can_be_published')
                from .runtime import get_tokenizer
                from .settings import KnowledgeSettings
                settings=KnowledgeSettings.from_env(validate_model=True)
                tokenizer=get_tokenizer()
            else:
                settings=None;tokenizer=None
            status=apply_review(session,row.id,'approve' if payload.approved else 'reject',payload.reviewer,row.scope,tokenizer,settings.generation if settings else '')
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
    return {'ok':True,'status':status,'indexing':'queued' if status.startswith('approved_') else None}
