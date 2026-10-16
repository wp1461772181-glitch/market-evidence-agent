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
