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


def find_revision_candidates(symbol: str, version_id_str: str | None, db):
    from ..symbols import normalize_market_symbol
    from ..models import UploadedEvidence, SecFilingInventory
    from ..forecast_v2_models import ForecastVersionV2
    from datetime import UTC, timedelta
    try:
        norm_symbol = normalize_market_symbol(symbol)
    except Exception:
        return {'symbol': symbol, 'status': 'invalid_symbol', 'candidates': []}

    version = None
    if version_id_str:
        try:
            version = db.get(ForecastVersionV2, UUID(version_id_str))
        except Exception:
            pass
    if not version:
        root_candidates = db.scalars(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.symbol == norm_symbol, ForecastVersionV2.root_id == ForecastVersionV2.id)
            .order_by(ForecastVersionV2.created_at.desc(), ForecastVersionV2.id.desc())
        ).all()
        root = next((c for c in root_candidates if getattr(c, 'time_mode', None) != 'historical_research'), None)
        if root:
            version = db.scalars(
                select(ForecastVersionV2)
                .where(ForecastVersionV2.root_id == root.id)
                .order_by(ForecastVersionV2.version_no.desc(), ForecastVersionV2.created_at.desc())
            ).first()

    if not version:
        return {
            'symbol': norm_symbol,
            'status': 'no_forecast_version',
            'message': f'系统内未找到 {norm_symbol} 的有效预测版本。',
            'candidates': []
        }

    decision_at = version.decision_at
    if not decision_at.tzinfo:
        decision_at = decision_at.replace(tzinfo=UTC)

    start_past = decision_at - timedelta(days=30)
    end_future = decision_at + timedelta(days=3)

    candidates = []
    uploads = db.scalars(
        select(UploadedEvidence)
        .where(UploadedEvidence.symbol == norm_symbol)
        .order_by(UploadedEvidence.published_at.desc())
    ).all()
    for u in uploads:
        pub = u.published_at
        if pub and not pub.tzinfo: pub = pub.replace(tzinfo=UTC)
        obs = u.observed_at
        if obs and not obs.tzinfo: obs = obs.replace(tzinfo=UTC)
        if not pub: continue
        if decision_at <= pub <= end_future:
            candidates.append({
                'source_id': str(u.id),
                'source_type': 'uploaded_media',
                'title': u.title,
                'published_at': pub.isoformat(),
                'observed_at': obs.isoformat() if obs else None,
                'category': 'new_material',
                'badge': '新发布材料',
                'credibility_stars': u.credibility_stars,
            })
        elif obs and obs > decision_at and start_past <= pub < decision_at:
            candidates.append({
                'source_id': str(u.id),
                'source_type': 'uploaded_media',
                'title': u.title,
                'published_at': pub.isoformat(),
                'observed_at': obs.isoformat() if obs else None,
                'category': 'past_supplementary',
                'badge': '过往补充材料',
                'credibility_stars': u.credibility_stars,
            })

    filings = db.scalars(
        select(SecFilingInventory)
        .where(SecFilingInventory.symbol == norm_symbol, SecFilingInventory.content_status == 'fetched')
        .order_by(SecFilingInventory.filed_at.desc())
    ).all()
    for f in filings:
        pub = f.filed_at
        if pub and not pub.tzinfo: pub = pub.replace(tzinfo=UTC)
        obs = f.observed_at
        if obs and not obs.tzinfo: obs = obs.replace(tzinfo=UTC)
        if not pub: continue
        if decision_at <= pub <= end_future:
            candidates.append({
                'source_id': str(f.id),
                'source_type': 'official_filing',
                'title': f.content_document_name or f.primary_document or f"{f.form} 申报",
                'published_at': pub.isoformat(),
                'observed_at': obs.isoformat() if obs else None,
                'category': 'new_material',
                'badge': '新发布材料',
                'credibility_stars': 5,
            })
        elif obs and obs > decision_at and start_past <= pub < decision_at:
            candidates.append({
                'source_id': str(f.id),
                'source_type': 'official_filing',
                'title': f.content_document_name or f.primary_document or f"{f.form} 申报",
                'published_at': pub.isoformat(),
                'observed_at': obs.isoformat() if obs else None,
                'category': 'past_supplementary',
                'badge': '过往补充材料',
                'credibility_stars': 5,
            })

    candidates.sort(key=lambda x: x['published_at'], reverse=True)
    return {
        'symbol': norm_symbol,
        'version_id': str(version.id),
        'version_no': version.version_no,
        'decision_at': decision_at.isoformat(),
        'status': 'success',
        'count': len(candidates),
        'candidates': candidates[:15],
    }


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
            value=find_revision_candidates(symbol,identity,db)
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
            for ref in args['source_refs']:
                if not private_source(db,ref.get('source_type'),ref['source_id'],context):raise ValueError('source_not_owned')
            value=forecasts.create_v2_manual_revision_job(UUID(args['version_id']),forecasts.ManualRevisionJobRequest(source_refs=args['source_refs']),key,db)
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
        return {'ok':True,'data':safe(value)}

