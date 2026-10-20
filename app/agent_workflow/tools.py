"""Real service adapters; routing and natural-language argument inference do not live here."""
from __future__ import annotations
import asyncio,json,uuid
from datetime import UTC,datetime,timedelta
from sqlalchemy import select,text,or_
from ..database import SessionLocal
from ..agent_chat_identity import owner_credential_matches
from ..agent_chat_store import owner_hash
from ..agent_tool_catalog import definitions,validate
from ..knowledge.runtime import research_context
from .models import AgentWebSource


def safe(value):return json.loads(json.dumps(value,default=str,ensure_ascii=False))
def result(rows,source):return {'ok':True,'data':safe(rows),'source':source,'as_of':datetime.now(UTC).isoformat()}
def publication_time(value):
    """A date-only value is UTC midnight: the source gave a calendar day, not a local clock time."""
    if isinstance(value,datetime):
        parsed=value
    else:
        try:parsed=datetime.fromisoformat(str(value or '').strip().replace('Z','+00:00'))
        except ValueError:raise ValueError('invalid_published_at')
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
def error(code):return {'ok':False,'data':[],'error_code':code,'hint':'用 request_action 请求用户批准' if code=='write_tool_not_granted' else '请修正参数或选择已返回的来源；没有资料时可先联网查找。'}


async def search_knowledge(args,context):
    """A's mandatory saved-data prelude; explicit structured selectors only."""
    from ..agent_tools import query_materials,query_sec_filings,query_research_faq
    rows=[];citations=[];read_error=None;symbol=args.get('symbol');query=args['query'];limit=args.get('limit',5)
    with research_context(context.owner_hash,citations=citations):
        if symbol:
            if args.get('latest') or args.get('form'):
                filing=await query_sec_filings.ainvoke({'symbol':symbol,'limit':1 if args.get('latest') else limit,
                    'form':args.get('form'),'financial_only':args.get('financial_only',False),'source_id':args.get('source_id')})
                rows.extend({**r,'symbol':symbol,'source_type':'official_filing'} for r in filing.get('data') or [])
                if not filing.get('ok'):read_error=filing.get('error_code') or 'knowledge_unavailable'
            else:
                found=await query_materials.ainvoke({'symbol':symbol,'keyword':query[:80],'source_id':args.get('source_id'),'limit':limit})
                rows.extend(found.get('data') or [])
                if not found.get('ok'):read_error=found.get('error_code') or 'knowledge_unavailable'
        elif not args.get('company'):
            found=await query_research_faq.ainvoke({'keyword':query[:80]})
            rows.extend({**r,'source_type':'research_faq'} for r in found.get('data') or [])
            if not found.get('ok'):read_error=found.get('error_code') or 'knowledge_unavailable'
        # Captured web sources are available in lexical saved-data reads even while
        # embeddings warm up. Dense retrieval uses the same snapshots and owner gate.
        from ..knowledge.models import KnowledgeDocument
        with SessionLocal() as db:
            terms=[AgentWebSource.owner_hash==context.owner_hash]
            if symbol:terms.append(AgentWebSource.symbol==symbol)
            elif args.get('company'):terms.append(AgentWebSource.company.ilike(args['company']))
            elif not args.get('source_id'):terms.append(AgentWebSource.excerpt.contains(query,autoescape=True))
            if args.get('source_id'):terms.append(AgentWebSource.id==uuid.UUID(args['source_id']))
            saved=db.scalars(select(AgentWebSource).where(*terms).order_by(AgentWebSource.published_at.desc().nullslast()).limit(limit)).all()
            rows.extend({'id':str(r.id),'symbol':r.symbol,'title':r.title,'excerpt':r.excerpt,'source_type':'web_news',
                'source_url':r.source_url,'published_at':r.published_at.isoformat() if r.published_at else None,
                'fetched_at':r.fetched_at.isoformat(),'content_hash':r.content_hash} for r in saved)
    if not rows and read_error:return error(read_error)
    return {**result(rows[:limit],'saved knowledge'),'_citations':citations}


async def invoke(name,args,context,*,emit=None):
    try:args=validate(name,args)
    except Exception:return error('invalid_arguments')
    policy=definitions()[name];is_owner=owner_credential_matches(context.identity_credential)
    if not is_owner and not policy.guest:return error('owner_login_required')
    if policy.level=='costly' or (policy.level=='reversible' and is_owner):return error('confirmation_required')
    return await _dispatch(name,args,context,is_owner,emit)


async def execute_reversible(operation,key,context,*,emit=None):
    """Require the exact durable operation currently executing under consent's lock."""
    from . import consent
    try:
        request_id,index=key.rsplit('.',1)
        saved=consent.lookup(context.identity_credential,context.conversation_id,request_id)
        if saved['status']!='executing' or saved['operations'][int(index)]!=operation:raise ValueError('confirmation_parameter_mismatch')
        name=operation['name'];args=validate(name,operation['arguments'])
        if definitions()[name].level!='reversible':raise ValueError('invalid_action_tool')
    except (ValueError,IndexError,KeyError,TypeError):return error('confirmation_required')
    return await _dispatch(name,args,context,True,emit)


async def _dispatch(name,args,context,is_owner,emit):
    if context.executor:
        value=context.executor(name,args)
        return await value if hasattr(value,'__await__') else value
    from ..agent_tools import TOOL_REGISTRY
    if name in TOOL_REGISTRY:
        citations=[]
        with research_context(context.owner_hash,citations=citations):value=await TOOL_REGISTRY[name].ainvoke(args)
        return {**value,'_citations':citations}
    if name=='search_knowledge':return await search_knowledge(args,context)
    if name=='test_retrieval_diagnostics':
        from ..knowledge.retrieval import retrieve_materials,retrieve_analysis,retrieve_faq
        citations=[]
        symbol=(args.get('symbol') or '').upper()
        query=args['query']
        kind=args.get('kind') or ('material' if symbol else 'faq')
        limit=args.get('top_k',5)
        with research_context(context.owner_hash,citations=citations):
            if kind=='analysis' and symbol:
                res=await retrieve_analysis(symbol,query,limit=limit)
            elif kind=='material' and symbol:
                res=await retrieve_materials(symbol,query,limit=limit)
            else:
                res=await retrieve_faq(query)
        return {**res,'_citations':citations}
    if name=='read_workspace':
        from .workspace_tools import read_workspace
        return await asyncio.to_thread(read_workspace,args,context)
    if name=='query_system_status':
        kind=args['kind']
        with SessionLocal() as db:
            if kind=='jobs':
                # The durable action ledger is owner scoped; guests cannot infer owner tasks.
                if not is_owner:return error('owner_login_required')
                filters=' AND (CAST(id AS text)=:job_id OR CAST(job_id AS text)=:job_id)' if args.get('job_id') else ''
                if args.get('symbol'):
                    # Resolve symbol from stored results, underlying jobs or the exact
                    # frozen operation. Filter before LIMIT, never relabel another job.
                    filters+=''' AND (result->>'symbol'=:symbol
                        OR job_id IN (SELECT id FROM forecast_jobs_v2 WHERE symbol=:symbol)
                        OR job_id IN (SELECT j.id FROM material_analysis_jobs j
                            LEFT JOIN sec_filing_inventory f ON j.source_type='official_filing' AND f.id=j.source_id
                            LEFT JOIN uploaded_evidence u ON j.source_type='uploaded_media' AND u.id=j.source_id
                            WHERE f.symbol=:symbol OR u.symbol=:symbol)
                        OR EXISTS (SELECT 1 FROM agent_consents c,
                            jsonb_array_elements(CAST(c.operations AS jsonb)) WITH ORDINALITY AS operation(value,position)
                            WHERE c.owner_hash=:owner
                            AND agent_action_requests.request_key=CAST(c.id AS text)||'.'||CAST(operation.position-1 AS text)
                            AND operation.value->'arguments'->>'symbol'=:symbol))'''
                rows=db.execute(text('SELECT * FROM agent_action_requests WHERE owner_hash=:owner'+filters+' ORDER BY created_at DESC LIMIT :limit'),{**args,'owner':context.owner_hash}).mappings().all()
            elif kind=='knowledge':
                from ..knowledge.models import KnowledgeDocument
                statement=select(KnowledgeDocument).where(or_(KnowledgeDocument.scope=='public',KnowledgeDocument.owner_hash==context.owner_hash))
                if args.get('symbol'):statement=statement.where(KnowledgeDocument.symbol==args['symbol'])
                if args.get('job_id'):statement=statement.where(KnowledgeDocument.id==uuid.UUID(args['job_id']))
                rows=[{'id':str(r.id),'title':r.title,'status':r.status,'source_type':r.source_type} for r in db.scalars(statement.limit(args['limit']))]
            else:
                table={'monitor':'official_monitor_runs_v2','evaluations':'forecast_evaluations_v2','replays':'forecast_jobs_v2','revisions':'forecast_jobs_v2'}[kind]
                from sqlalchemy import inspect
                available={c['name'] for c in inspect(db.bind).get_columns(table)}
                columns=[n for n in ('id','symbol','status','created_at','started_at','completed_at','evaluated_at','target_date') if n in available]
                filters=' WHERE symbol=:symbol' if args.get('symbol') and 'symbol' in available else ''
                if args.get('symbol') and 'symbol' not in available:
                    if kind=='monitor':filters+=(' AND ' if filters else ' WHERE ')+"jsonb_exists(per_symbol_results,:symbol)"
                    else:return error('symbol_filter_unavailable')
                if args.get('job_id'):filters+=(' AND ' if filters else ' WHERE ')+"CAST(id AS text)=:job_id"
                if kind=='replays':filters+=(' AND ' if filters else ' WHERE ')+"time_mode='historical_research'"
                # "Is the revision done?": the manual revisions of a forecast, newest first.
                if kind=='revisions':filters+=(' AND ' if filters else ' WHERE ')+"kind='manual_revision'"
                rows=db.execute(text('SELECT '+','.join(columns)+' FROM '+table+filters+' ORDER BY '+('created_at' if 'created_at' in available else 'id')+' DESC LIMIT :limit'),args).mappings().all()
            if kind=='jobs':
                from .actions import _status
                rows=[{'id':str(r['id']),**_status(db,dict(r))} for r in rows]
            return result([dict(r) for r in rows],kind)
    if name in {'query_research_queue','add_to_research_queue','remove_from_research_queue'}:
        if not is_owner:
            action={'event':'client_action','id':str(uuid.uuid4()),'action':{'query_research_queue':'show_queue','add_to_research_queue':'queue_add','remove_from_research_queue':'queue_remove'}[name],**args}
            if emit:await emit(action)
            return result([{'client_action':action,'status':'client_requested'}],'browser')
        from ..agent_chat_models import AgentResearchQueue
        with SessionLocal() as db:
            # Concurrent add/remove cannot lose one another's queue changes.
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'queue:'+context.owner_hash})
            row=db.get(AgentResearchQueue,context.owner_hash);symbols=list(row.symbols if row else [])
            if name!='query_research_queue':
                symbol=args['symbol']
                if name=='add_to_research_queue' and symbol not in symbols:
                    if len(symbols)>=30:return error('queue_full')
                    symbols.append(symbol)
                if name=='remove_from_research_queue':symbols=[s for s in symbols if s!=symbol]
                if row:row.symbols=symbols
                else:db.add(AgentResearchQueue(owner_hash=context.owner_hash,symbols=symbols))
                db.commit()
            action={'event':'client_action','id':str(uuid.uuid4()),'action':'queue_sync','symbols':symbols}
            if emit:await emit(action)
            return result([{'symbols':symbols}],'owner queue')
    if name in {'select_stock','open_page'}:
        action={'event':'client_action','id':str(uuid.uuid4()),'action':name,**args}
        if emit:await emit(action)
        return result([{'client_action':action}],'browser')
    if name=='review_filing':
        from ..models import SecFilingInventory
        from ..sec_filings import review_inventory_filing
        with SessionLocal() as db:
            row=db.get(SecFilingInventory,uuid.UUID(args['source_id']))
            if not row:return error('source_not_found')
            review_inventory_filing(symbol=row.symbol,accession_number=row.accession_number,decision=args['decision'],note=args['note'],db=db)
            db.commit();return result([{'source_id':args['source_id'],'decision':args['decision']}],'relevance review')
    return error('unknown_tool')


def execute_costly(operation,key,context):
    """Only called inside consent.execute after capability and payload validation."""
    name=operation['name'];args=operation['arguments'];credential=context.identity_credential
    if not owner_credential_matches(credential):raise ValueError('owner_login_required')
    if name in {'fetch_sec','refresh_sec'} and operation.get('discovery'):
        from .sec_sources import register_confirmed
        register_confirmed(operation)
    if name in {'fetch_sec','refresh_sec','analyze','import','index','forecast'}:
        from .actions import execute_action
        payload={k:v for k,v in args.items() if v is not None and k!='form'}
        value=execute_action(credential=credential,request_key=key,action=name,payload=payload)
        return {'ok':value.get('status') not in {'failed','blocked_data'},**value}
    if name in {'start_historical_replay','revise_forecast','retry_document_index','translate_content','review_knowledge_candidate','import_sources','withdraw_knowledge_document'}:
        from .workspace_tools import write_workspace
        return write_workspace(name,args,key,context)
    if name=='save_material_evidence':
        from ..manual_evidence import create_uploaded_evidence
        from ..knowledge.models import KnowledgeUploadOwner
        raw_pub=args['published_at']
        pub_dt=publication_time(raw_pub)
        content_bytes=args['content'].encode('utf-8')
        title=args['title']
        filename=f"{title[:50].replace('/','_')}.md"
        with SessionLocal() as db:
            evidence,created=create_uploaded_evidence(
                symbol=args['symbol'],
                title=title,
                source_url=args['source_url'],
                published_at=pub_dt,
                credibility_stars=args.get('credibility_stars',3),
                credibility_reason=args.get('credibility_reason',''),
                impact_severity=args.get('impact_severity','medium'),
                filename=filename,
                content=content_bytes,
                db=db,
            )
            if created and context.identity_credential:
                db.add(KnowledgeUploadOwner(upload_id=evidence.id,owner_hash=context.owner_hash))
                db.commit()
            return {
                'ok':True,
                'data':[{
                    'id':str(evidence.id),
                    'symbol':evidence.symbol,
                    'title':evidence.title,
                    'created':created,
                    'status':'created' if created else 'already_exists'
                }]
            }
    if name=='ingest_web_source':
        from .web_sources import freeze_snapshot,save_snapshot,canonical_url
        snapshot=operation.get('snapshot')
        if not isinstance(snapshot,dict):raise ValueError('confirmation_snapshot_missing')
        page=freeze_snapshot(snapshot)
        if page['content_hash']!=snapshot.get('content_hash') or canonical_url(args['url'])!=page['url']:raise ValueError('confirmation_snapshot_mismatch')
        from .evidence import gate_row
        aliases = list(operation.get('entity_aliases', []))
        if args.get('company') and args['company'] not in aliases: aliases.append(args['company'])
        if args.get('symbol') and args['symbol'] not in aliases: aliases.append(args['symbol'])
        accepted,gated=gate_row('',{**page,'source_type':'web'},company=args.get('company'),symbol=args.get('symbol'),metrics=[],entity_aliases=aliases)
        if not accepted:raise ValueError(gated)
        if operation.get('condition'):
            from .web_sources import timestamp
            before=timestamp(operation['condition'].get('saved_date'));after=timestamp(page.get('published_at'))
            if not before or not after or after<before:
                return {'ok':True,'status':'completed_without_write','reason':'not_newer'}
        with SessionLocal() as db:
            value=save_snapshot(db,context.owner_hash,page,symbol=args.get('symbol'),company=args.get('company'));db.commit()
            # The report names the material the user reviewed, not just its ticker.
            return {**value,'title':str(page.get('title') or args['url']).strip()[:300] or args['url']}
    if name=='initialize_prices':
        from ..market_data_ingestion import ingest_market_data
        now=datetime.now(UTC);value=ingest_market_data([args['symbol']],(now-timedelta(days=args['days'])).date(),now.date(),now)
        return {'ok':value.status=='completed','run_id':str(value.run_id),'status':value.status,'inserted':value.inserted_count}
    if name=='scan_official':
        from ..official_monitor_v2 import scan_only
        days=args.get('days')
        with SessionLocal() as db:
            # A confirmed scan reports its own SEC outcome inside the chat request, so the
            # scheduled monitor's evaluator and learner are not part of this effect.
            # A backfill reads one SEC submissions page per covered year, so the page
            # budget follows the requested range instead of a fixed three pages.
            pages=max(3,(days+364)//365+1) if days else 3
            value=scan_only(db=db,symbols=[args['symbol']],max_pages=min(12,pages),
                            lookback_days=days);db.commit()
            scanned=[{'symbol':item.symbol,'status':item.status,'discovered':item.discovered_count,
                      'created':item.created_count,'queued':item.queued_count,
                      **({'error':item.error} if item.error else {})} for item in value.symbols]
            return {'ok':value.status=='succeeded','run_id':str(value.run_id),'status':value.status,'scanned':scanned}
    raise ValueError('unknown_tool')
