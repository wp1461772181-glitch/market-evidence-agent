from __future__ import annotations
import hashlib,json
from datetime import UTC,datetime
from uuid import UUID
from sqlalchemy import and_,or_,select
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import SecFilingInventory,UploadedEvidence
from ..material_analysis_models import MaterialAnalysisVersion
from ..agent_chat_models import AgentFAQ
from .models import KnowledgeCandidate,KnowledgeChunk,KnowledgeDocument,KnowledgeUploadOwner
from .sources import adapt_sec,adapt_uploaded
from .runtime import current_research_context,get_runtime
from .repository import document_integrity

class KnowledgeUnavailable(RuntimeError):
    def __init__(self,message='dense knowledge runtime is unavailable',code='knowledge_unavailable'):
        super().__init__(message);self.code=code

def _json_bytes(value): return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
def _head_tail_order(items):
    """Place relevance ranks 1, 2, ... at opposite ends of the final sequence."""
    result=[None]*len(items);left=0;right=len(items)-1
    for rank,item in enumerate(items):
        position=left if rank%2==0 else right
        result[position]=item
        if rank%2==0:left+=1
        else:right-=1
    return result
def _fit_tool_budget(result:dict,budget:int=8000):
    had_candidates=bool(result.get('data'))
    while _json_bytes(result)>budget and result.get('data'):
        result['data'].pop()
    result['as_of']=max((row['observed_at'] for row in result.get('data',[]) if row.get('observed_at')),default=None)
    if _json_bytes(result)>budget or (had_candidates and not result.get('data')):
        return {'ok':False,'data':None,'error_code':'insufficient_result_budget','source':result.get('source'),'as_of':None}
    return result
def _q(value: str) -> str: return value.replace('\\','\\\\').replace('"','\\"')
def _source_hash(session:Session,doc:KnowledgeDocument,*,historical:bool=False):
    try: identity=UUID(doc.source_id)
    except (ValueError,TypeError): identity=None
    if doc.source_table=='material_analysis_versions':
        try: analysis_id=UUID(doc.source_version)
        except (ValueError,TypeError): return None
        from .analysis_sources import load_analysis_document
        current=load_analysis_document(session,analysis_id,require_latest=True)
        if not current or (current.source_id,current.source_type,current.symbol,current.scope,current.visibility,current.owner_hash)!=(doc.source_id,doc.source_type,doc.symbol,doc.scope,doc.visibility,doc.owner_hash):return None
        return doc.source_hash if historical and current.source_hash==doc.source_hash else current.source_hash
    if doc.source_table=='uploaded_evidence' and identity:
        row=session.get(UploadedEvidence,identity)
        if not row:return None
        mapping=session.scalar(select(KnowledgeUploadOwner).where(KnowledgeUploadOwner.upload_id==identity))
        owner=mapping.owner_hash if mapping else None
        current=adapt_uploaded({'id':row.id,'symbol':row.symbol,'title':row.title,'source_url':row.source_url,'published_at':row.published_at,
          'observed_at':row.observed_at,'content_sha256':row.content_sha256,'content_text':row.content_text},owner_hash=owner,
          allowlist={str(row.id)} if owner is None else set())
        if not current or (current.scope,current.visibility,current.owner_hash)!=(doc.scope,doc.visibility,doc.owner_hash):return None
        if historical:return doc.source_hash
        return current.source_hash
    if doc.source_table=='sec_filing_inventory' and identity:
        row=session.get(SecFilingInventory,identity)
        if not row:return None
        current=adapt_sec({c.key:getattr(row,c.key) for c in row.__table__.columns})
        if not current or (current.scope,current.visibility,current.owner_hash)!=(doc.scope,doc.visibility,doc.owner_hash):return None
        if historical:
            if row.content_status!='fetched':return None
            return doc.source_hash
        # SEC citations freeze the complete provenance snapshot, not only the
        # body digest (which remains stable when an exhibit URL is corrected).
        if current.source_version!=doc.source_version:return None
        return current.source_hash
    if doc.source_table=='knowledge_candidates' and identity:
        row=session.get(KnowledgeCandidate,identity)
        if not row or row.status not in {'approved_public','approved_private'}:return None
        if not row.reviewer or not row.reviewed_at:return None
        approved_scope='public' if row.status=='approved_public' else 'private'
        approved_owner=None if approved_scope=='public' else row.owner_hash
        if (approved_scope,approved_owner)!=(doc.scope,doc.owner_hash):return None
        if not row.reviewed_at or row.reviewed_at>datetime.now(UTC) or row.reviewed_at!=doc.observed_at:return None
        return hashlib.sha256('\n'.join((row.category,row.question,row.answer,row.reviewed_at.isoformat())).encode()).hexdigest()
    if doc.source_table=='agent_faq':
        if (doc.scope,doc.visibility,doc.owner_hash)!=('public','public',None):return None
        try: key=int(doc.source_id)
        except ValueError:return None
        row=session.get(AgentFAQ,key)
        if not row:return None
        return hashlib.sha256('\n'.join((row.category,row.question,row.answer)).encode()).hexdigest()
    return None

def _current_rows(ids:list[str],generation:str,scope_context, symbol:str|None, faq:bool, *, historical:bool=False, analysis:bool=False):
    try: uuids=[UUID(value) for value in ids]
    except ValueError:return []
    now=datetime.now(UTC); cutoff=scope_context.cutoff if scope_context and scope_context.cutoff else now
    with SessionLocal() as session:
        pairs=session.execute(select(KnowledgeChunk,KnowledgeDocument).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id)
          .where(KnowledgeChunk.id.in_(uuids),KnowledgeChunk.generation==generation,KnowledgeChunk.status=='ready',
            ((KnowledgeDocument.active.is_(True)&(KnowledgeDocument.status=='active')) if not historical else
             (KnowledgeDocument.status.in_(['active','superseded']) & (KnowledgeDocument.created_at<=cutoff) & (KnowledgeChunk.created_at<=cutoff))),KnowledgeChunk.needs_review.is_(False),
            KnowledgeChunk.visibility.in_(['public','private']),(KnowledgeChunk.available_at.is_(None) | (KnowledgeChunk.available_at<=cutoff)),
            (KnowledgeChunk.source_type=='research_faq') if faq else (KnowledgeChunk.source_type=='ai_analysis' if analysis else KnowledgeChunk.source_type.in_(['uploaded_media','official_filing'])),
            KnowledgeChunk.symbol.is_(None) if faq else KnowledgeChunk.symbol==symbol)
          .order_by(KnowledgeChunk.available_at.desc().nullslast())).all()
        found=[]
        # In historical mode, choose the latest eligible snapshot in PostgreSQL
        # for each source, independently of which snapshot ANN happened to rank.
        if historical:
            source_keys={(doc.source_table,doc.source_id) for _chunk,doc in pairs}
            source_predicates=[and_(KnowledgeDocument.source_table==table,KnowledgeDocument.source_id==source_id)
                               for table,source_id in source_keys]
            eligible_docs=session.scalars(select(KnowledgeDocument).where(or_(*source_predicates),
              KnowledgeDocument.status.in_(['active','superseded']),KnowledgeDocument.created_at<=cutoff,
              KnowledgeDocument.available_at.is_not(None),KnowledgeDocument.available_at<=cutoff)).all() if source_predicates else []
            latest={}
            for doc in eligible_docs:
                if doc.observed_at is None and doc.content_observed_at is None:continue
                key=(doc.source_table,doc.source_id)
                observed=max(x for x in (doc.observed_at,doc.content_observed_at,doc.available_at,doc.created_at) if x is not None)
                if key not in latest or observed>latest[key][0]:latest[key]=(observed,doc.id)
            pairs=[(chunk,doc) for chunk,doc in pairs if chunk.available_at is not None and chunk.available_at<=cutoff
                   and latest.get((doc.source_table,doc.source_id),(None,None))[1]==doc.id]
        for chunk,doc in pairs:
            observed_values=(doc.observed_at,doc.content_observed_at,doc.available_at,doc.created_at)
            if (doc.source_type!='research_faq' and (doc.observed_at is None or (doc.source_type=='official_filing' and doc.content_observed_at is None))) or any(value is not None and value>now for value in observed_values):continue
            if not doc.snapshot_integrity or document_integrity(doc)!=doc.snapshot_integrity:continue
            if chunk.scope=='private' and (scope_context is None or chunk.visibility!='private' or chunk.owner_hash!=scope_context.owner_hash or doc.scope!='private' or doc.visibility!='private' or doc.owner_hash!=scope_context.owner_hash): continue
            if chunk.scope=='public' and (chunk.visibility!='public' or doc.scope!='public' or doc.visibility!='public' or doc.owner_hash is not None):continue
            if chunk.scope not in {'public','private'}:continue
            if _source_hash(session,doc,historical=historical)!=doc.source_hash or chunk.source_hash!=doc.source_hash:continue
            if hashlib.sha256(chunk.answer.encode()).hexdigest()!=chunk.content_hash:continue
            found.append((chunk,doc))
        return found

def _eligible_ann_ids(generation:str,scope_context,symbol:str|None,faq:bool,historical:bool,cutoff,analysis:bool=False,source_id:str|None=None):
    with SessionLocal() as session:
        source_types=['research_faq'] if faq else (['ai_analysis'] if analysis else ['uploaded_media','official_filing'])
        predicates=[KnowledgeChunk.generation==generation,KnowledgeChunk.status=='ready',KnowledgeChunk.needs_review.is_(False),
          KnowledgeChunk.source_type.in_(source_types),KnowledgeChunk.available_at.is_not(None),KnowledgeChunk.available_at<=cutoff]
        if not faq: predicates.append(KnowledgeChunk.symbol==symbol)
        if source_id is not None: predicates.append(KnowledgeDocument.source_id==source_id)
        if scope_context:
            predicates.append(or_(and_(KnowledgeChunk.scope=='public',KnowledgeChunk.visibility=='public',KnowledgeChunk.owner_hash.is_(None)),
              and_(KnowledgeChunk.scope=='private',KnowledgeChunk.visibility=='private',KnowledgeChunk.owner_hash==scope_context.owner_hash)))
        else: predicates.extend((KnowledgeChunk.scope=='public',KnowledgeChunk.visibility=='public',KnowledgeChunk.owner_hash.is_(None)))
        if historical:
            predicates.extend((KnowledgeChunk.created_at<=cutoff,KnowledgeDocument.status.in_(['active','superseded']),KnowledgeDocument.created_at<=cutoff))
        else: predicates.extend((KnowledgeDocument.active.is_(True),KnowledgeDocument.status=='active'))
        ids=[str(value) for value in session.scalars(select(KnowledgeChunk.id).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id).where(*predicates)).all()]
    if len(ids)>5000: raise KnowledgeUnavailable('eligible knowledge set exceeds bounded ANN filter capacity','knowledge_candidate_set_too_large')
    rows=_current_rows(ids,generation,scope_context,symbol,faq,historical=historical,analysis=analysis) if ids else []
    return {str(chunk.id):(chunk,doc) for chunk,doc in rows}

async def _retrieve(query:str, *, faq:bool, symbol:str|None=None, limit:int=5, analysis:bool=False, source_id:str|None=None):
    research_cap=getattr(current_research_context(),'max_citations_per_observation',None)
    if research_cap is not None:
        limit=min(limit,research_cap)
    runtime=get_runtime()
    if runtime is None: raise KnowledgeUnavailable('dense knowledge runtime is not enabled or ready')
    context=current_research_context()
    now=datetime.now(UTC); cutoff=context.cutoff if context and context.cutoff else now
    if context and context.cutoff:
        if not isinstance(cutoff,datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise KnowledgeUnavailable('historical cutoff must be timezone-aware','invalid_historical_cutoff')
        cutoff=cutoff.astimezone(UTC)
        if cutoff>now:raise KnowledgeUnavailable('historical cutoff cannot be in the future','invalid_historical_cutoff')
    historical=bool(context and context.cutoff and cutoff<now)
    access='(scope == "public" and visibility == "public")'
    if context: access+=' or (scope == "private" and visibility == "private" and owner_hash == "'+_q(context.owner_hash)+'")'
    try: eligible=_eligible_ann_ids(runtime.settings.generation,context,symbol,faq,historical,cutoff,analysis,source_id)
    except KnowledgeUnavailable:raise
    except Exception as exc:raise KnowledgeUnavailable('knowledge authority database is unavailable') from exc
    if not eligible:return {'ok':True,'data':[],'source':'knowledge_chunks:research_faq' if faq else ('knowledge_documents:material_analysis_versions' if analysis else 'knowledge_documents:uploaded_evidence + sec_filing_inventory'),'as_of':None}
    conditions=['('+access+')']
    conditions.append('source_type == "research_faq"' if faq else ('source_type == "ai_analysis"' if analysis else 'source_type in ["uploaded_media", "official_filing"]'))
    if not faq: conditions.append('symbol == "'+_q(symbol or '')+'"')
    conditions.append(f'available_at <= {int(cutoff.timestamp()*1000)}')
    conditions.append('id in ['+','.join('"'+value+'"' for value in eligible)+']')
    filter_expr=' and '.join(conditions)
    try:
        from .query_understanding import retrieval_query
        strategy=getattr(runtime.settings,'strategy','dense')
        search_query=retrieval_query(query)
        if hasattr(runtime,'search_strategy'):hits=await runtime.search_strategy(strategy,search_query,filter_expr,50)
        else:
            vector=await runtime.encode(search_query);hits=await runtime.search(vector,filter_expr,50)
    except Exception as exc:
        raise KnowledgeUnavailable('knowledge retrieval backend is unavailable') from exc
    # Cosine calibration is meaningful only for dense retrieval. BM25 and RRF
    # use ranking order; a dense cutoff must never reject their candidates.
    ranked=[hit for hit in hits if strategy!='dense' or float(hit.get('distance',-1))>=runtime.threshold]
    by_id=eligible
    ordered=[by_id[str(hit['id'])] for hit in ranked if str(hit['id']) in by_id]
    if strategy=='hybrid_rerank' and ordered:
        try:
            pairs=await runtime.rerank(query,[chunk.answer for chunk,_doc in ordered])
            ordered=[ordered[index] for index,_score in pairs[:10]]
        except Exception as exc: raise KnowledgeUnavailable('reranker backend is unavailable','knowledge_reranker_unavailable') from exc
    # Number citations against the shared turn manifest before rendering evidence.
    # Existing chunks retain their original number when retrieved again.
    context=current_research_context()
    existing=context.citations if context is not None and context.citations is not None else []
    def citation_key(chunk_id, source_hash, content_hash, generation):
        return (str(chunk_id), str(source_hash or ""), str(content_hash or ""), str(generation or ""))
    ordinal_by_chunk={citation_key(item['chunk_id'],item.get('source_hash'),item.get('content_hash'),item.get('generation')):item['ordinal']
                      for item in existing if item.get('chunk_id') and item.get('ordinal')}
    next_ordinal=max((int(item.get('ordinal',0)) for item in existing),default=0)+1
    out=[]; by_source={}; chunk_counts={};emitted=[];documents={}
    for chunk,doc in ordered:
        if len(emitted)>=(research_cap or 10):break
        source_key=doc.source_id
        existing_row=by_source.get(source_key)
        if existing_row is None and len(out)>=limit:continue
        if existing_row is not None:
            field='answer' if faq else 'excerpt'
            if chunk_counts[source_key]>=3 or chunk.answer in existing_row[field]:continue
        chunk_id=str(chunk.id)
        source_snapshot_key=citation_key(chunk_id,doc.source_hash,chunk.content_hash,chunk.generation)
        citation_number=ordinal_by_chunk.get(source_snapshot_key)
        if citation_number is None:
            if next_ordinal>10:break
            citation_number=next_ordinal;ordinal_by_chunk[source_snapshot_key]=citation_number;next_ordinal+=1
        if source_key not in by_source:
            if faq:
                row={'question':doc.title,'answer':f'[{citation_number}] {chunk.answer}','category':chunk.category}
            else:
                observed=doc.content_observed_at if doc.source_type=='official_filing' else doc.observed_at
                excerpt=chunk.answer
                if doc.source_type=='ai_analysis':
                    excerpt=f'[派生 AI 分析；模型/版本见标题与章节；原文引文如下]\n{excerpt}'
                if doc.source_type=='official_filing':
                    coverage='saved excerpt; partial/truncated coverage' if doc.truncated else 'saved excerpt; coverage limited to the stored content'
                    excerpt=f'[{coverage}; content source: {doc.source_url or "URL unavailable"}; content observed: {observed.isoformat() if observed else "unknown"}]\n{excerpt}'
                excerpt=f'[{citation_number}] {excerpt}'
                row={'id':doc.source_id,'title':doc.title,'source_url':getattr(doc,'source_url',None),'published_at':doc.published_at.isoformat() if doc.published_at else None,
                  'observed_at':observed.isoformat() if observed else None,'excerpt':excerpt,'material_type':doc.source_type, **({'analysis_version':doc.source_version,'derived':True} if analysis else {})}
            by_source[source_key]=row;documents[source_key]=doc;emitted.append((chunk,doc,citation_number));out.append(row);chunk_counts[source_key]=1
        else:
            row=by_source[source_key]
            field='answer' if faq else 'excerpt'
            row[field]+='\n\n['+str(citation_number)+'] '+chunk.answer
            candidate={'ok':True,'data':out,'source':'knowledge','as_of':None}
            if _json_bytes(candidate)>8000:
                row[field]=row[field].rsplit('\n\n['+str(citation_number)+'] ',1)[0]
                continue
            emitted.append((chunk,doc,citation_number));chunk_counts[source_key]+=1
    source='knowledge_chunks:research_faq' if faq else ('knowledge_documents:material_analysis_versions' if analysis else 'knowledge_documents:uploaded_evidence + sec_filing_inventory')
    result=_fit_tool_budget({'ok':True,'data':out,'source':source,'as_of':None})
    if result.get('ok'):
        result['data']=_head_tail_order(result['data'])
    if context is not None and context.citations is not None and result.get('ok'):
        retained_sources={row.get('id') for row in result.get('data',[])} if not faq else {
            emitted_item[1].source_id for emitted_item in emitted
            if any(f'[{emitted_item[2]}]' in row.get('answer','') for row in result.get('data',[]))
        }
        seen={citation_key(item['chunk_id'],item.get('source_hash'),item.get('content_hash'),item.get('generation'))
              for item in context.citations if item.get('chunk_id')}
        for chunk,doc,ordinal in emitted:
            if doc.source_id not in retained_sources:continue
            chunk_id=str(chunk.id)
            snapshot_key=citation_key(chunk_id,doc.source_hash,chunk.content_hash,chunk.generation)
            if snapshot_key in seen:continue
            context.citations.append({'ordinal':ordinal,'chunk_id':chunk_id,'document_id':str(doc.id),'source_hash':doc.source_hash,'content_hash':chunk.content_hash,'generation':chunk.generation,'title':doc.title[:180],
                'section':chunk.section_path[:180],'source_url':getattr(doc,'source_url',None) if doc.source_type=='ai_analysis' else None,**({'analysis_version':doc.source_version,'derived':True} if doc.source_type=='ai_analysis' else {}),'observed_at':(doc.content_observed_at or doc.observed_at or doc.available_at).isoformat() if (doc.content_observed_at or doc.observed_at or doc.available_at) else None,
                'source_type':doc.source_type,'truncated':bool(doc.truncated)})
            seen.add(snapshot_key)
    return result

async def retrieve_faq(query:str): return await _retrieve(query,faq=True,limit=5)
async def retrieve_materials(symbol:str,query:str,limit:int=5,source_id:str|None=None): return await _retrieve(query,faq=False,symbol=symbol,limit=limit,source_id=source_id)

async def retrieve_analysis(symbol:str,query:str,limit:int=5,source_id:str|None=None): return await _retrieve(query,faq=False,symbol=symbol,limit=limit,analysis=True,source_id=source_id)
