"""Server-minted capabilities. Models never receive or supply write authority."""
from __future__ import annotations
import hashlib,json,logging,secrets,uuid
from datetime import UTC,datetime,timedelta
from sqlalchemy import select,text
from ..agent_chat_identity import owner_credential_matches
from ..agent_chat_store import owner_hash
from ..database import SessionLocal,engine
from .models import AgentConsent

logger=logging.getLogger(__name__)

class ConsentError(ValueError):pass

# What the user reads when a confirmed handler refuses the write.  The exception text itself stays in
# the log: a database error or a scanned page can quote a filename, a URL query or worse.
FAILURE_REASONS={
 'page_fetch_blocked':'网页被站点拒绝，或返回的是验证页、登录页、错误页，没有可保存的正文。',
 'page_without_text':'这个网页只抓到很少的文字（大多是图片或链接），没有可保存的正文。',
 'invalid_published_at':'发布日期无法解析，或晚于当前时间。',
 'content_too_large':'内容超过单次保存的大小上限。',
 'content_empty':'要保存的内容是空的。',
 'content_unreadable':'文件无法读取为文本，可能是损坏的 PDF 或非 UTF-8 编码。',
 'duplicate_source':'同样的内容已经在资料库里，没有重复保存。',
 'invalid_source_url':'来源链接必须是去掉空格的 HTTPS 地址，且不能带账号密码。',
 'invalid_title':'标题不符合要求，可能为空或过长。',
 'invalid_symbol':'股票代码不是有效的美股代码。',
 'invalid_material':'材料没有通过校验，请检查标题、来源链接和正文。',
 'source_changed':'网页内容在你确认后发生了变化，已停止保存以免保存错内容。',
 'entity_mismatch':'网页正文没有出现这只股票或这家公司，不能作为它的资料保存。',
 'owner_login_required':'当前登录状态已失效，请由所有者重新登录后再试。',
 'permission_revoked':'当前登录状态已失效，请由所有者重新登录后再试。',
 'unsupported_operation':'这项操作不受支持。',
 'batch_stopped':'前一项没有完成，这项没有执行。',
 'dependency_failed':'这项依赖的前一项没有完成，所以没有执行。',
 'write_service_failed':'执行时服务返回了未预期的错误，操作没有完成。',
}

def failure_reason(code):
    return FAILURE_REASONS.get(str(code),FAILURE_REASONS['write_service_failed'])

def failure_outcome(exc):
    """Map a handler's exception to a code the client may show and a sentence the user can act on.

    Only the code survives into the result; the handler's own message is never copied there.  A
    validation error is distinguishable from an outage, which is the difference between retrying and
    fixing the source.
    """
    from sqlalchemy.exc import IntegrityError
    from ..manual_evidence import ManualEvidenceError
    if isinstance(exc,IntegrityError):
        original=getattr(exc,'orig',None)
        # Only a unique violation means "already saved"; a not-null or check violation is an outage
        # of the write path, not a duplicate the user can ignore.
        if getattr(original,'sqlstate',None)=='23505' or getattr(original,'pgcode',None)=='23505' or type(original).__name__=='UniqueViolation':
            return {'ok':False,'error_code':'duplicate_source','reason':failure_reason('duplicate_source')}
        return {'ok':False,'error_code':'write_service_failed','reason':failure_reason('write_service_failed')}
    if isinstance(exc,ManualEvidenceError):
        message=str(exc)
        if 'published_at' in message:
            reason='发布日期晚于当前时间，可能抓错了日期。' if 'later than' in message else failure_reason('invalid_published_at')
            return {'ok':False,'error_code':'invalid_published_at','reason':reason}
        if 'exceeds' in message:
            return {'ok':False,'error_code':'content_too_large','reason':failure_reason('content_too_large')}
        if 'must not be empty' in message and 'file' in message:
            return {'ok':False,'error_code':'content_empty','reason':failure_reason('content_empty')}
        if 'source_url' in message:
            return {'ok':False,'error_code':'invalid_source_url','reason':failure_reason('invalid_source_url')}
        if 'symbol' in message:
            return {'ok':False,'error_code':'invalid_symbol','reason':failure_reason('invalid_symbol')}
        if message.startswith('title '):
            return {'ok':False,'error_code':'invalid_title','reason':failure_reason('invalid_title')}
        if 'PDF could not be read' in message or 'UTF-8' in message:
            return {'ok':False,'error_code':'content_unreadable','reason':failure_reason('content_unreadable')}
        if 'credibility_stars' in message:reason='可信度星级必须是 1 到 5 的整数。'
        elif 'credibility_reason' in message:reason='可信度理由不能为空，且不能超过 700 字。'
        elif 'impact_severity' in message:reason='影响程度必须是 low、medium 或 high。'
        elif 'filename' in message or 'TXT, Markdown' in message:reason='文件名无效，或文件类型不是 TXT、Markdown、PDF。'
        else:reason=failure_reason('invalid_material')
        return {'ok':False,'error_code':'invalid_material','reason':reason}
    code=str(exc)
    known={
     'web_body_unavailable':'page_fetch_blocked','challenge_page':'page_fetch_blocked',
     'boilerplate_page':'page_without_text','page_without_text':'page_without_text',
     'unsupported_content_type':'page_fetch_blocked',
     'web_snapshot_unavailable':'page_fetch_blocked','invalid_source_body':'page_fetch_blocked','zero_evidence':'page_fetch_blocked',
     'confirmation_snapshot_missing':'source_changed','confirmation_snapshot_mismatch':'source_changed',
     'requested_entity_missing':'entity_mismatch','owner_login_required':'owner_login_required',
     'unknown_tool':'unsupported_operation','invalid_published_at':'invalid_published_at',
    }
    if code in known:
        return {'ok':False,'error_code':known[code],'reason':failure_reason(known[code])}
    return {'ok':False,'error_code':'write_service_failed','reason':failure_reason('write_service_failed')}

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def propose(credential,conversation,operations,display,*,question='',now=None):
    """Mint one card and keep the request that produced it.

    A press on the card sends only the button's value, so the turn that executes the write has no
    other way to learn what the user asked: the request travels with the card instead.
    """
    if not owner_credential_matches(credential):raise ConsentError('owner_login_required')
    if not operations or len(operations)>8:raise ConsentError('invalid_write_targets')
    token=secrets.token_urlsafe(32);expires=(now or datetime.now(UTC))+timedelta(minutes=10)
    with SessionLocal() as db:
        row=AgentConsent(owner_hash=owner_hash(credential),conversation_id=conversation,capability_hash=digest(token),payload_digest=digest(operations),operations=operations,display=display,expires_at=expires,status='proposed')
        db.add(row);db.commit();identity=str(row.id)
    return {'id':identity,'reason':'write_confirmation','display':display,'original_question':question,
        'expires_at':expires.isoformat(),
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
                            if not isinstance(outcome,dict):outcome={'ok':False,'error_code':'write_service_failed','reason':failure_reason('write_service_failed')}
                        except ConsentError:
                            outcome={'ok':False,'error_code':'permission_revoked','reason':failure_reason('permission_revoked')}
                        except Exception as exc:
                            # The traceback is the only place the cause exists; the result keeps a
                            # code and a human reason, never the exception text.
                            logger.exception('confirmed write failed operation=%s request=%s',operation.get('name'),identity)
                            outcome=failure_outcome(exc)
                        if outcome.get('error_code') in {'permission_revoked','owner_login_required','access_denied','cancelled'}:stopped=True
                    outcomes.append(outcome);items.append({'index':index,'status':'complete','outcome':outcome})
                    row=db.get(AgentConsent,uuid.UUID(identity));row.result={'items':items,'data':outcomes};db.commit()
                result={'ok':all(x.get('ok',True) for x in outcomes),'data':outcomes,'status':'complete'}
                row=db.get(AgentConsent,uuid.UUID(identity));row.status='complete';row.result=result;db.commit()
                return result
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(hashtext(:key))'),{'key':'consent:'+capability});connection.commit()
