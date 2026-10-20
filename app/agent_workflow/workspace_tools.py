"""Adapters for existing workspace APIs. No new forecasting or retrieval algorithms."""
import json
from uuid import UUID
from sqlalchemy import select
from ..database import SessionLocal
from ..agent_chat_identity import owner_credential_matches
from .tools import result,error,safe


def private_source(db,source_type,identity,context):
    if source_type!='uploaded_media':return True
    from ..knowledge.models import KnowledgeUploadOwner
    owner=db.scalar(select(KnowledgeUploadOwner.owner_hash).where(KnowledgeUploadOwner.upload_id==UUID(identity)))
    return owner==context.owner_hash


# The V2 forecast API reports a refused revision as FastAPI's HTTPException; its English detail is
# the only classification it exposes.  Translate it at the handler boundary so a confirmed write
# says which version or source is wrong instead of collapsing into write_service_failed.
REVISION_REJECTIONS={
 'forecast version was not found':'forecast_version_not_found',
 'forecast version has an incompatible root':'forecast_version_not_revisable',
 'forecast target has expired; create an evaluation instead':'forecast_version_not_revisable',
 'forecast version has an invalid target contract':'forecast_version_not_revisable',
 'source_refs must not repeat a source':'revision_rejected',
 'official filing source was not found':'revision_source_not_found',
 'uploaded media source was not found':'revision_source_not_found',
 'source does not belong to the forecast symbol':'revision_source_symbol_mismatch',
 'source is not available at this decision time':'revision_source_unavailable',
 'manual revision with this unchanged source snapshot is already queued; wait for it to finish':'revision_already_queued',
 'manual revision source is unchanged from the selected forecast':'revision_source_unchanged',
 'manual revision source is unchanged from the selected forecast and has no new successful analysis':'revision_source_unchanged',
 'manual revision source is outside the revision window of the selected forecast':'revision_source_outside_window',
 'manual revision source snapshot and analysis are unchanged from the previous result':'revision_source_unchanged',
 'official filing requires an exact timezone-aware SEC acceptance timestamp':'revision_rejected',
}
def revision_rejection(exc):
    detail=str(getattr(exc,'detail',''))
    if detail.startswith('revision source snapshot is invalid:'):return 'revision_rejected'
    return REVISION_REJECTIONS.get(detail)


def usable_for_revision(db,source_type,identity,context):
    """An upload someone else privately owns stays theirs; the owner's own and unowned library uploads
    (the workspace lists those to everyone) may revise the owner's forecast, as the workspace allows."""
    if source_type!='uploaded_media':return True
    from ..knowledge.models import KnowledgeUploadOwner
    owner=db.scalar(select(KnowledgeUploadOwner.owner_hash).where(KnowledgeUploadOwner.upload_id==UUID(identity)))
    return owner is None or owner==context.owner_hash


def current_forecast(db,symbol):
    """The newest version of the symbol's newest forward-looking forecast; a historical replay is a
    retrospective study, never the forecast a user means by "the forecast"."""
    from ..forecast_v2_models import ForecastJobV2,ForecastVersionV2
    roots=db.scalars(select(ForecastVersionV2).where(ForecastVersionV2.symbol==symbol,ForecastVersionV2.root_id==ForecastVersionV2.id)
                     .order_by(ForecastVersionV2.created_at.desc(),ForecastVersionV2.id.desc())).all()
    for root in roots:
        job=db.get(ForecastJobV2,root.job_id)
        if job is not None and job.time_mode=='historical_research':continue
        return db.scalars(select(ForecastVersionV2).where(ForecastVersionV2.root_id==root.id)
                          .order_by(ForecastVersionV2.version_no.desc(),ForecastVersionV2.created_at.desc())).first()
    return None


def revision_plan(symbol,version_id=None,context=None,mentioned=()):
    """What revising a forecast can use right now: the version, its window and the allowed materials.

    ``status`` is ``ready`` with candidates, or says why there is nothing to offer: ``no_forecast``,
    ``version_not_found``, ``target_expired``, ``version_not_revisable`` or ``no_candidates``.  With
    nothing to offer, ``excluded`` holds the newest materials and why each does not count; ``mentioned``
    says the same of the materials the user named.
    """
    from datetime import UTC,date,datetime
    from ..forecast_revision_candidates import REVISION_LOOKAHEAD,REVISION_LOOKBACK,assess_revision_sources,unknown_source
    from ..forecast_v2_models import ForecastVersionV2
    from ..market_time import xnys_session_close_at
    from ..symbols import normalize_market_symbol
    try:symbol=normalize_market_symbol(symbol)
    except ValueError:return {'status':'no_forecast','symbol':str(symbol)}
    with SessionLocal() as db:
        if version_id:
            version=db.get(ForecastVersionV2,UUID(version_id))
            if version is None or version.symbol!=symbol:return {'status':'version_not_found','symbol':symbol}
        else:
            version=current_forecast(db,symbol)
            if version is None:return {'status':'no_forecast','symbol':symbol}
        decided=version.decision_at if version.decision_at.tzinfo else version.decision_at.replace(tzinfo=UTC)
        root=db.get(ForecastVersionV2,version.root_id) or version
        target=root.target_contract.get('target_end_date') if isinstance(root.target_contract,dict) else None
        plan={'symbol':symbol,'version_id':str(version.id),'version_no':version.version_no,'decision_at':decided.isoformat(),
              'target_end_date':target,'window_start':(decided-REVISION_LOOKBACK).isoformat(),
              'window_end':(decided+REVISION_LOOKAHEAD).isoformat(),'candidates':[]}
        try:expired=datetime.now(UTC)>=xnys_session_close_at(date.fromisoformat(str(target)))
        except (TypeError,ValueError):return {**plan,'status':'version_not_revisable'}
        if expired:return {**plan,'status':'target_expired'}
        assessed=assess_revision_sources(db,version)
        for row in assessed:
            if row['category'] and context is not None and not usable_for_revision(db,row['source_type'],row['source_id'],context):
                row.update(category=None,reason='not_found',reason_text='资料库里没有找到这份材料')
        candidates=[{k:v for k,v in row.items() if k not in ('reason','reason_text')} for row in assessed if row['category']]
        by_key={(row['source_type'],row['source_id']):row for row in assessed}
        named=[by_key.get((ref.get('source_type'),str(ref.get('source_id')))) or unknown_source(db,symbol,ref) for ref in mentioned]
        excluded=[row for row in assessed if row['reason'] and row['reason']!='not_published_yet'][:3]
        return {**plan,'status':'ready' if candidates else 'no_candidates','candidates':candidates,
                'excluded':[] if candidates else excluded,'mentioned':named}


def read_workspace(args,context):
    kind=args['kind'];identity=args.get('record_id');symbol=args.get('symbol');source_type=args['source_type']
    from .. import forecast_v2_api as forecasts
    from .. import knowledge
    from ..knowledge import api as library
    owner=owner_credential_matches(context.identity_credential)
    with SessionLocal() as db:
        if kind in {'forecast','timeline','analysis_history','original','knowledge_chunks'} and not identity:return error('record_id_required')
        if kind in {'analysis_history','original'} and not private_source(db,source_type,identity,context):return error('source_not_owned')
        if kind=='forecast':value=forecasts.get_v2_forecast_version(UUID(identity),db)
        elif kind=='timeline':value=forecasts.get_v2_forecast_timeline(UUID(identity),db)
        elif kind=='roots':
            if not symbol:return error('symbol_required')
            value=forecasts.get_v2_stock_workspace(symbol,db)
        elif kind=='revision_candidates':
            if not symbol:return error('symbol_required')
            value=revision_plan(symbol,identity,context)
        elif kind=='learning':
            from ..jev_learning import jev_learning_status
            value=jev_learning_status(db=db,symbol=symbol)
        elif kind in {'analysis_history','original'}:
            from ..material_analysis_api import material_analysis_history,material_original
            value=(material_analysis_history if kind=='analysis_history' else material_original)(source_type,UUID(identity),db)
        elif kind=='knowledge_chunks':value=library.document_chunks(identity,x_chat_session=context.identity_credential,x_app_access_key=None)
        elif kind=='knowledge_candidates':
            if not owner:return error('owner_login_required')
            value=library.list_candidates(x_chat_session=context.identity_credential,x_app_access_key=None)
        elif kind=='importable':value=library.importable(source_type,symbol=symbol,x_chat_session=context.identity_credential)
        else:return error('unknown_workspace_read')
        actual_symbol=value.get('symbol') if isinstance(value,dict) else None
        if symbol and actual_symbol and symbol!=actual_symbol:return error('requested_entity_missing')
        return result([{'id':identity,'symbol':actual_symbol or symbol,'source_type':'saved_workspace','title':'Workspace '+kind,'excerpt':json.dumps(safe(value),ensure_ascii=False,sort_keys=True,separators=(',',':'))[:5000],'tool_arguments':args}],'workspace '+kind)


def write_workspace(name,args,key,context):
    if not owner_credential_matches(context.identity_credential):raise ValueError('owner_login_required')
    from .. import forecast_v2_api as forecasts
    from ..knowledge import api as library
    with SessionLocal() as db:
        if name=='start_historical_replay':value=forecasts.create_v2_historical_replay(forecasts.HistoricalReplayRequest.model_validate(args),key,db)
        elif name=='revise_forecast':
            from fastapi import HTTPException
            for ref in args['source_refs']:
                if not usable_for_revision(db,ref.get('source_type'),ref['source_id'],context):raise ValueError('source_not_owned')
            try:
                value=forecasts.create_v2_manual_revision_job(UUID(args['version_id']),forecasts.ManualRevisionJobRequest(source_refs=args['source_refs']),key,db)
            except HTTPException as exc:
                code=revision_rejection(exc)
                if not code:raise
                raise ValueError(code) from exc
        elif name=='retry_document_index':value=library.retry_document(args['document_id'],x_chat_session=context.identity_credential,x_app_access_key=None)
        elif name=='withdraw_knowledge_document':value=library.withdraw_document(args['document_id'],x_chat_session=context.identity_credential,x_app_access_key=None)
        elif name=='translate_content':
            from ..localization_api import material_analysis_localization,forecast_brief_localization
            if args['kind']=='analysis':
                from ..material_analysis_models import MaterialAnalysisVersion
                analysis=db.get(MaterialAnalysisVersion,UUID(args['record_id']))
                if not analysis or not private_source(db,analysis.source_type,str(analysis.source_id),context):raise ValueError('source_not_owned')
            value=(material_analysis_localization if args['kind']=='analysis' else forecast_brief_localization)(UUID(args['record_id']),db)
        elif name=='review_knowledge_candidate':value=library.review_candidate(args['candidate_id'],library.ReviewRequest(approved=args['approved'],reviewer=args['reviewer']),x_chat_session=context.identity_credential,x_app_access_key=None)
        elif name=='import_sources':value=library.import_existing(library.ImportRequest.model_validate(args),x_chat_session=context.identity_credential,x_app_access_key=None)
        else:raise ValueError('unknown_tool')
        if name in {'revise_forecast','start_historical_replay'} and isinstance(value,dict) and value.get('id'):
            # A forecast job is queued, not done: the report reads job_id and status from a flat row.
            return {'ok':True,'data':[{'job_id':str(value['id']),'status':value.get('status'),'symbol':value.get('symbol'),'action':name}]}
        return {'ok':True,'data':safe(value)}

