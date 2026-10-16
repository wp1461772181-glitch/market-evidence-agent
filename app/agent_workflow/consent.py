"""Server-minted capabilities. Models never receive or supply write authority."""
from __future__ import annotations
import hashlib,json,secrets,uuid
from datetime import UTC,datetime,timedelta
from sqlalchemy import select,text
from ..agent_chat_identity import owner_credential_matches
from ..agent_chat_store import owner_hash
from ..database import SessionLocal,engine
from .models import AgentConsent

class ConsentError(ValueError):pass

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def propose(credential,conversation,operations,display,*,now=None):
    if not owner_credential_matches(credential):raise ConsentError('owner_login_required')
    if not operations or len(operations)>8:raise ConsentError('invalid_write_targets')
    token=secrets.token_urlsafe(32);expires=(now or datetime.now(UTC))+timedelta(minutes=10)
    with SessionLocal() as db:
        row=AgentConsent(owner_hash=owner_hash(credential),conversation_id=conversation,capability_hash=digest(token),payload_digest=digest(operations),operations=operations,display=display,expires_at=expires,status='proposed')
        db.add(row);db.commit();identity=str(row.id)
    return {'id':identity,'reason':'write_confirmation','display':display,'expires_at':expires.isoformat(),
        'choices':[{'label':'同意','value':'confirm:'+token},{'label':'换个操作','value':'change:'+identity},{'label':'取消','value':'cancel:'+identity}]}

def lookup(credential,conversation,request_id):
    if not owner_credential_matches(credential):raise ConsentError('owner_login_required')
    try:identity=uuid.UUID(request_id)
    except (TypeError,ValueError):raise ConsentError('invalid_confirmation')
    with SessionLocal() as db:
        row=db.get(AgentConsent,identity)
        if not row or row.owner_hash!=owner_hash(credential) or row.conversation_id!=conversation:raise ConsentError('confirmation_scope_mismatch')
        return {'id':str(row.id),'operations':row.operations,'display':row.display,'status':row.status}

def cancel(credential,conversation,request_id):
    lookup(credential,conversation,request_id)
    with SessionLocal() as db:
        row=db.scalar(select(AgentConsent).where(AgentConsent.id==uuid.UUID(request_id)).with_for_update())
        if row.status=='proposed':row.status='cancelled'
        elif row.status=='executing':row.status='cancelling'
        status=row.status;db.commit()
    return {'ok':True,'status':status}

def execute(credential,conversation,token,operations,handler,*,now=None):
    if not owner_credential_matches(credential):raise ConsentError('owner_login_required')
    if not isinstance(token,str) or not 32<=len(token)<=100:raise ConsentError('confirmation_required')
    capability=digest(token)
    # The dedicated session lock covers the durable state transition and external service.
    # After a crash an executing operation is never silently retried; legacy handlers also
    # retain their per-operation durable idempotency ledger.
    with engine.connect() as connection:
        connection.execute(text('SELECT pg_advisory_lock(hashtext(:key))'),{'key':'consent:'+capability});connection.commit()
        try:
            with SessionLocal(bind=connection) as db:
                row=db.scalar(select(AgentConsent).where(AgentConsent.capability_hash==capability).with_for_update())
                if not row or row.owner_hash!=owner_hash(credential) or row.conversation_id!=conversation:raise ConsentError('confirmation_scope_mismatch')
                if row.payload_digest!=digest(operations):raise ConsentError('confirmation_parameter_mismatch')
                if row.status=='complete':return row.result
                if row.status!='proposed':raise ConsentError('confirmation_consumed')
                if row.expires_at<=(now or datetime.now(UTC)):raise ConsentError('confirmation_expired')
                identity=str(row.id);frozen=json.loads(json.dumps(row.operations));row.status='executing';db.commit()
                outcomes=[];items=[];stopped=False
                for index,operation in enumerate(frozen):
                    # Cancellation and the next durable claim linearize on this
                    # short row lock. Never reuse a pre-cancel identity-map value.
                    row=db.scalar(select(AgentConsent).where(AgentConsent.id==uuid.UUID(identity)).with_for_update().execution_options(populate_existing=True))
                    if row.status=='cancelling':stopped=True
                    dependencies=operation.get('depends_on',[])
                    if stopped or not owner_credential_matches(credential):
                        outcome={'ok':False,'error_code':'batch_stopped'};stopped=True
                    elif dependencies and any(type(i) is not int or i<0 or i>=index or not outcomes[i].get('ok',True) for i in dependencies):
                        outcome={'ok':False,'error_code':'dependency_failed'}
                    else:
                        # Commit BEFORE each side effect. A process crash preserves
                        # completed items and the unknown in-flight item; never replay.
                        row.result={'items':[*items,{'index':index,'status':'executing'}],'data':outcomes}
                        db.commit()
                        try:
                            outcome=handler(operation,identity+'.'+str(index))
                            if not isinstance(outcome,dict):outcome={'ok':False,'error_code':'write_service_failed'}
                        except ConsentError:
                            outcome={'ok':False,'error_code':'permission_revoked'}
                        except Exception:
                            outcome={'ok':False,'error_code':'write_service_failed'}
                        if outcome.get('error_code') in {'permission_revoked','owner_login_required','access_denied','cancelled'}:stopped=True
                    outcomes.append(outcome);items.append({'index':index,'status':'complete','outcome':outcome})
                    row=db.get(AgentConsent,uuid.UUID(identity));row.result={'items':items,'data':outcomes};db.commit()
                result={'ok':all(x.get('ok',True) for x in outcomes),'data':outcomes,'status':'complete'}
                row=db.get(AgentConsent,uuid.UUID(identity));row.status='complete';row.result=result;db.commit()
                return result
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(hashtext(:key))'),{'key':'consent:'+capability});connection.commit()
