"""Owner-authorized adapters to existing durable business operations."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import text

from ..agent_chat_identity import owner_credential_matches
from ..agent_chat_store import owner_hash
from ..database import SessionLocal, engine
from ..symbols import normalize_market_symbol


class ActionError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _word(token: str) -> str:
    """An ASCII word that is not part of a longer one (CJK neighbours are fine)."""
    return rf"(?<![a-z])(?:{token})(?![a-z])"


_ACCESSION_NO = r"\d{10}-\d{2}-\d{6}"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_FORM = r"(?<![a-z0-9])(?:10-[kq]|8-k)(?![a-z0-9])"
_IMPORT_OBJECT = "|".join(["资料(?!库)", "材料(?!库)", "来源", r"(?<![a-z])(?:source|material|filing|accession)", _ACCESSION_NO, _UUID])
# A pointer at records the user was given earlier ("刚刚那两条", "第二份", "those two"): what a write
# may name instead of a noun, its records then being resolved from the saved result set.
_COUNT = r"[一二两三四五六七八九十\d]{1,3}"
_UNIT = r"(?:条|篇|份|项|个(?![月年周天季度日]))"
_POINTER = "|".join([
    rf"(?:刚刚|刚才|之前|上面|前面|上述|以上|这|那|该|此)\s*(?:{_COUNT}\s*)?{_UNIT}",
    r"(?:刚刚|刚才|之前|上面|前面|上述|以上|这|那)\s*(?:些|批|组)",
    rf"第\s*{_COUNT}\s*{_UNIT}", rf"前\s*{_COUNT}\s*{_UNIT}", rf"最后\s*(?:{_COUNT}\s*)?{_UNIT}",
    r"(?<![其他])它们", r"(?<![a-z])(?:those|these|them|the (?:first|second|third|last|above|previous))(?![a-z])",
])
_ANALYZE_OBJECT = "|".join([r"(?<![a-z])(?:accession|filing|report|source)", _FORM, _ACCESSION_NO, _UUID,
                            "财报", "年报", "季报", "报告", "来源", "资料", "材料", "原文",
                            r"ai\s*分析", r"(?<![a-z])ai\s+analysis", _POINTER])
_INDEX_OBJECT = "|".join(["资料", "材料", r"(?<![a-z])(?:source|material|filing|document)"])
_ANALYZE_PHRASE = re.compile(r"(?:生成|创建|重新|开始|" + _word("(?:request|run)") + r").{0,20}?"
                             r"(?:ai\s*分析|分析|" + _word("analysis") + ")")
_IMPORT_ITEM = rf"(?:{_POINTER}|{_IMPORT_OBJECT}|{_FORM}|财报|年报|季报)"
_IMPORT_VERBS = rf"(?:导入(?!业务|政策|关税)|存入|存到|放入|放进|加入|收录|录入|保存|{_word('import|save|ingest')})"


def _requested_action_words(question: str) -> str | None:
    """The write the words name.  Words are matched as words ('important' is not 'import',
    'Nasdaq-100 index' is not an index job) and an import, an index job or an analysis must name
    what it works on (a noun, or a pointer at records the user was given)."""
    lower = question.lower()
    if re.search(r"^(?:查看|查询|浏览|看下|看看)", lower):
        if re.search(r"(?:查看|查询|" + _word("(?:read|check)") + r").{0,12}(?:任务状态|" + _word("job status") + "|进度)", lower): return "status"
        return None
    if re.search(r"(?:查看|查询|" + _word("(?:read|check)") + r").{0,12}(?:任务状态|" + _word("job status") + "|进度)", lower): return "status"
    if re.search(r"(?:刷新|" + _word("refresh") + r").{0,25}(?:原文|" + _word("(?:sec|filing)") + "|资料)", lower): return "refresh_sec"
    if re.search(r"(?:抓取|获取|拉取|下载|同步|搜寻|" + _word("(?:fetch|download|pull|sync)") + r").{0,25}(?:" + _word("(?:sec|filing)") + "|原文|申报|财报|年报|季报)", lower): return "fetch_sec"
    if (re.search(r"(?:送给|送去|交由|让|进行|做[个一次下]?|生成|开始|帮我|请|帮|来[个]?).{0,15}?(?:ai\s*分析|分析|解读|研报)", lower)
            or re.search(r"(?:分析|解读|研报).{0,20}(?:财报|年报|季报|申报)", lower)
            or re.search(r"(?:这|那|刚刚|刚才|上述|所选|上传).{0,15}?(?:资料(?!库)|材料(?!库)|文章|新闻).{0,15}?(?:ai\s*分析|分析|解读|研报)", lower)):
        return "analyze"
    for phrase in _ANALYZE_PHRASE.finditer(lower):
        # The object lies in the phrase or at most 25 characters before or after it.
        if any(hit.end() >= phrase.start() - 25 and hit.start() <= phrase.end() + 25
               for hit in re.finditer(_ANALYZE_OBJECT, lower)): return "analyze"
    if re.search(r"(?:^|\s)(?:分析|" + _word("analyze") + r")\s+(?:accession|\d{10}-)", lower): return "analyze"
    if (re.search(rf"(?:{_IMPORT_VERBS}.{{0,25}}{_IMPORT_ITEM}|{_IMPORT_ITEM}.{{0,25}}{_IMPORT_VERBS})", lower)
            or re.search(rf"(?:把|将).{{0,20}}{_IMPORT_ITEM}.{{0,15}}(?:{_IMPORT_VERBS}|资料库|材料库)", lower)):
        return "import"
    if (re.search(r"建立索引|重建索引", lower)
            or re.search(_word("(?:re-?)?index(?:ing)?") + r".{0,20}(?:" + _INDEX_OBJECT + ")", lower)): return "index"
    if re.search(r"(?:创建|生成|开始|" + _word("(?:enqueue|create)") + r").{0,20}(?:预测|" + _word("forecast") + ")", lower): return "forecast"
    return None


# A sentence that names a write without asking for it: said in the negative ("别把那份资料导入"),
# or asked about ("导入这些资料需要什么权限？", "What does import those two mean?").  A clause break
# ends a negation's or a question's reach ("别急，把那两条导入" is a request).
_GAP = r"[^，。,.;；!！?？\n]"
_NEGATED_ACTION = re.compile(
    r"(?:不要|别|勿|不用|不必|无需|不需要|不想|不能|不可以|不许|禁止|请勿|切勿|千万别|不准|不得)" + _GAP + r"{0,10}"
    r"(?:导入|分析|创建|生成|开始|刷新|抓取|索引|预测|执行|运行)"
    r"|\b(?:do not|don't|dont|never|no need to|without|not)\s+(?:\w+\s+){0,3}"
    r"(?:import|analy[sz]e|create|fetch|refresh|index|run|forecast)\b", re.I)
_MENTIONED_ACTION = re.compile(
    r"(?:解释|说明|讲讲|讲解|介绍|什么是|何谓|如何理解|怎么理解)(?:一下|下)?" + _GAP + r"{0,12}"
    r"(?:导入|分析|创建|索引|预测|刷新|抓取)"
    r"|(?:导入|分析|创建|索引|预测|刷新|抓取)" + _GAP + r"{0,14}"
    r"(?:是什么意思|什么意思|的含义|的定义|是什么|需要什么|要什么|为什么|有什么(?:区别|作用|用)|怎么回事)"
    r"|\b(?:what (?:does|is|are)|why|how (?:does|do)|explain|define|meaning of)\b[^.?!\n]{0,40}"
    r"\b(?:import|analy[sz]e|forecast|index|refresh)\b"
    r"|\b(?:import|analy[sz]e)\b[^.?!\n]{0,30}\b(?:mean|means|permission|permissions|require|requires|work|works)\b",
    re.I)
POINTER_AT_RECORDS = re.compile(_POINTER, re.I)


def requested_action(question: str) -> str | None:
    """The write the user's words explicitly ask for: the action the words name, unless the
    sentence is negated or only asks about it.  Without a plain request there is no authority,
    whatever the intent model made of the sentence."""
    action = _requested_action_words(question)
    clean_q = re.sub(r'不用客气|别客气|不用担心|别担心|无需确认|不需确认|无需向我确认|不用多说', '', question)
    if action and (_NEGATED_ACTION.search(clean_q) or _MENTIONED_ACTION.search(clean_q)):
        return None
    return action


def _fingerprint(action: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"action": action, "payload": payload}, sort_keys=True,
                                     ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _status(db, row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row.get("result") or {})
    kind, job_id = row.get("job_kind"), row.get("job_id")
    if kind == "analysis" and job_id:
        from ..material_analysis import get_job
        current = get_job(db=db, job_id=job_id)
        if current: result = current
    elif kind == "forecast" and job_id:
        from ..forecast_v2_models import ForecastJobV2
        current = db.get(ForecastJobV2, job_id)
        if current: result = {"job_id": str(current.id), "status": current.status,
                              "result_id": str(current.result_version_id) if current.result_version_id else None}
    status = result.get("status", row["status"])
    if row["status"] == "running" and not job_id:
        status = "running" if _action_lock_held(row["owner_hash"], row["request_key"]) else "interrupted_reconcile_required"
    if status in {"queued", "running", "pending"}: state = "running"
    elif status in {"succeeded", "succeeded_no_change", "complete", "completed"}: state = "succeeded"
    elif status == "partially_succeeded": state = "partially_succeeded"
    elif status in {"failed", "blocked_data", "interrupted_reconcile_required"}: state = "failed"
    else: state = row["status"]
    return {"ok": state != "failed", "status": state, "job_status": status,
            "action": row["action"], "job_id": str(job_id) if job_id else None,
            "error_code": (result.get("error_code") or status) if state == "failed" else None,
            "result": result}


def _action_lock_held(identity: str, request_key: str) -> bool:
    lock = identity + ":" + request_key
    with engine.connect() as connection:
        free = connection.execute(text("SELECT pg_try_advisory_lock(hashtext(:lock))"),
                                  {"lock": lock}).scalar_one()
        if free:
            connection.execute(text("SELECT pg_advisory_unlock(hashtext(:lock))"), {"lock": lock})
            connection.commit()
        return not free


def _underlying_key(identity: str, request_key: str) -> str:
    return f"agent:{identity[:16]}:{request_key}"


def _reconcile_job(db, row: dict[str, Any], identity: str, request_key: str) -> dict[str, Any] | None:
    """Find a committed underlying idempotent job after a ledger-write interruption."""
    key = _underlying_key(identity, request_key)
    if row["action"] == "analyze":
        found = db.execute(text("SELECT id FROM material_analysis_jobs WHERE idempotency_key=:key"),
                           {"key": key}).scalar_one_or_none()
        kind = "analysis"
    elif row["action"] == "forecast":
        found = db.execute(text("SELECT id FROM forecast_jobs_v2 WHERE idempotency_key=:key"),
                           {"key": key}).scalar_one_or_none()
        kind = "forecast"
    else:
        return None
    if found:
        db.execute(text("""UPDATE agent_action_requests SET job_kind=:kind,job_id=:job,
            status='running',result='{}'::jsonb,updated_at=now() WHERE id=:id"""),
                   {"kind": kind, "job": found, "id": row["id"]})
        db.commit()
        return _status(db, {**row, "job_kind": kind, "job_id": found, "status": "running", "result": {}})
    return None


def _index_selected(db, source_id: uuid.UUID, identity: str, max_tasks: int) -> dict[str, Any]:
    """Index only chunks belonging to a selected, authorized saved source."""
    import os
    if os.getenv("RAG_INDEXING_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise ActionError("knowledge_indexing_disabled")
    rows = db.execute(text("""SELECT c.id, d.scope, d.owner_hash FROM knowledge_chunks c
        JOIN knowledge_documents d ON d.id=c.document_id
        WHERE d.source_id=:source_id AND d.active=true AND c.status IN ('pending','failed')"""),
        {"source_id": str(source_id)}).mappings().all()
    if not rows: raise ActionError("no_pending_index_chunks")
    if any(row["scope"] == "private" and row["owner_hash"] != identity for row in rows):
        raise ActionError("source_not_owned")
    from ..knowledge.embedding import DenseEmbedder
    from ..knowledge.indexing import index_pending
    from ..knowledge.runtime import get_runtime
    from ..knowledge.settings import KnowledgeSettings
    from ..knowledge.vector_store import MilvusIndex
    settings = KnowledgeSettings.from_env(validate_model=True)
    runtime = get_runtime()
    embedder = runtime.embedder if runtime else DenseEmbedder(settings.model_path, cpu_threads=settings.cpu_threads)
    index = MilvusIndex(settings.milvus_uri, settings.generation)
    try:
        counts = index_pending(SessionLocal, embedder, index, settings.generation,
                               max_tasks=max_tasks, worker_id="agent-selected-index",
                               chunk_ids={row["id"] for row in rows})
    finally:
        index.close()
    remaining = db.execute(text("""SELECT count(*) FROM knowledge_chunks c JOIN knowledge_documents d
        ON d.id=c.document_id WHERE d.source_id=:source_id AND d.active=true
        AND c.status IN ('pending','failed','indexing')"""), {"source_id": str(source_id)}).scalar_one()
    status = _index_status(counts, remaining)
    return {"status": status, "source_id": str(source_id), "pending_before": len(rows), **counts,
            "remaining": remaining, **({"error_code": "index_operation_failed"} if counts["failed"] and not counts["indexed"] else {})}


def _index_status(counts: dict[str, Any], remaining: int) -> str:
    if counts.get("failed"):
        return "partially_succeeded" if counts.get("indexed") else "failed"
    if remaining:
        return "partially_succeeded" if counts.get("indexed") else "running"
    return "succeeded"


def execute_action(*, credential: str, request_key: str, action: str,
                   payload: dict[str, Any]) -> dict[str, Any]:
    # The caller's owner hash is never accepted as proof of ownership.
    if not owner_credential_matches(credential): raise ActionError("owner_login_required")
    if not 1 <= len(request_key) <= 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", request_key):
        raise ActionError("invalid_action_request_id")
    if action not in {"fetch_sec", "refresh_sec", "analyze", "import", "index", "forecast", "status"}:
        raise ActionError("unsupported_action")
    identity = owner_hash(credential)
    if action == "status":
        with SessionLocal() as db:
            row = db.execute(text("""SELECT * FROM agent_action_requests WHERE owner_hash=:owner
                AND (request_key=:key OR CAST(id AS text)=:key OR CAST(job_id AS text)=:key)
                ORDER BY created_at DESC LIMIT 1"""), {"owner": identity, "key": request_key}).mappings().first()
            if not row: raise ActionError("task_not_found")
            if row["status"] in {"running", "failed"} and not row["job_id"]:
                recovered = _reconcile_job(db, dict(row), identity, row["request_key"])
                if recovered: return recovered
            return _status(db, dict(row))
    source_id = payload.get("source_id")
    if source_id:
        try: source_id = uuid.UUID(str(source_id))
        except ValueError as exc: raise ActionError("invalid_source_id") from exc
    symbol = payload.get("symbol")
    if symbol:
        try: symbol = normalize_market_symbol(symbol)
        except ValueError as exc: raise ActionError("invalid_symbol") from exc
    if not source_id and payload.get("accession_number") and action in {"analyze", "import"}:
        with SessionLocal() as lookup:
            matches = lookup.execute(text("SELECT id,symbol FROM sec_filing_inventory WHERE accession_number=:accession LIMIT 2"),
                                     {"accession": payload["accession_number"]}).mappings().all()
            if len(matches) != 1: raise ActionError("source_not_found_or_ambiguous")
            if symbol and symbol != matches[0]["symbol"]: raise ActionError("source_symbol_mismatch")
            source_id, symbol = matches[0]["id"], matches[0]["symbol"]
    if action in {"fetch_sec", "refresh_sec"} and not (symbol and payload.get("accession_number")):
        raise ActionError("symbol_and_accession_required")
    if action in {"analyze", "import"} and not source_id:
        raise ActionError("source_id_required")
    if action == "forecast" and not symbol:
        raise ActionError("symbol_required")
    if action == "index" and (type(payload.get("max_tasks", 20)) is not int or not 1 <= payload.get("max_tasks", 20) <= 200):
        raise ActionError("invalid_index_limit")
    normalized = {**payload, **({"symbol": symbol} if symbol else {}),
                  **({"source_id": str(source_id)} if source_id else {})}
    digest = _fingerprint(action, normalized)
    # A session-level lock stays on this dedicated connection across ledger
    # commit and the external operation. The transaction lock did not.
    with engine.connect() as connection:
      connection.execute(text("SELECT pg_advisory_lock(hashtext(:lock))"),
                         {"lock": identity + ":" + request_key})
      connection.commit()
      try:
       with SessionLocal(bind=connection) as db:
        # The connection-level lock covers both ledger and business operations;
        # the unique index also protects the owner/key pair across processes.
        existing = db.execute(text("SELECT * FROM agent_action_requests WHERE owner_hash=:owner AND request_key=:key"),
                              {"owner": identity, "key": request_key}).mappings().first()
        if existing:
            if existing["payload_digest"] != digest: raise ActionError("idempotency_conflict")
            if existing["status"] in {"running", "failed"} and not existing["job_id"]:
                reconciled = _reconcile_job(db, dict(existing), identity, request_key)
                if reconciled: return reconciled
                if existing["status"] == "running":
                    return {"ok": False, "status": "failed", "job_status": "interrupted_reconcile_required",
                            "action": action, "job_id": None, "error_code": "interrupted_reconcile_required", "result": {}}
            return _status(db, dict(existing))
        # Validate scope before creating a durable request.
        if source_id and action in {"analyze", "import"}:
            source_type = payload.get("source_type", "official_filing")
            if source_type == "uploaded_media":
                owner = db.execute(text("SELECT owner_hash FROM knowledge_upload_owners WHERE upload_id=:id"),
                                   {"id": source_id}).scalar_one_or_none()
                if owner != identity: raise ActionError("source_not_owned")
            elif source_type == "official_filing":
                found = db.execute(text("SELECT symbol FROM sec_filing_inventory WHERE id=:id"),
                                   {"id": source_id}).scalar_one_or_none()
                if not found: raise ActionError("source_not_found")
                # A source id never widens a request scoped to another stock (the accession path
                # above already enforces this).
                if symbol and found != symbol: raise ActionError("source_symbol_mismatch")
            elif source_type == "ai_analysis" and action == "import":
                from ..knowledge.analysis_sources import load_analysis_document
                document=load_analysis_document(db,source_id)
                if not document or (document.scope=='private' and document.owner_hash!=identity):raise ActionError("source_not_owned")
                if symbol and document.symbol!=symbol:raise ActionError("source_symbol_mismatch")
            else: raise ActionError("unsupported_source_type")
        row_id = uuid.uuid4()
        db.execute(text("""INSERT INTO agent_action_requests
            (id,owner_hash,request_key,action,payload_digest,status,result)
            VALUES (:id,:owner,:key,:action,:digest,'running','{}'::jsonb)"""),
            {"id": row_id, "owner": identity, "key": request_key, "action": action, "digest": digest})
        db.commit()
        try:
            if action in {"fetch_sec", "refresh_sec"}:
                from ..sec_filings import fetch_inventory_content
                filing, cached = fetch_inventory_content(symbol=symbol, accession_number=payload["accession_number"],
                                                           db=db, force_refresh=action == "refresh_sec")
                db.commit()
                result = {"status": "succeeded", "source_id": str(filing.id), "cached": cached}
                job_kind = job_id = None
            elif action == "analyze":
                from ..material_analysis import request_material_analysis
                result = request_material_analysis(db=db, source_type=payload.get("source_type", "official_filing"),
                                                   source_id=source_id, idempotency_key=_underlying_key(identity, request_key))
                job_kind, job_id = "analysis", uuid.UUID(str(result.get("job_id") or result.get("id")))
            elif action == "forecast":
                from ..forecast_v2_api import _validate_source_refs, _supported_symbol
                from ..forecast_jobs import enqueue_job
                from datetime import UTC, datetime
                refs = payload.get("source_refs") or []
                for ref in refs:
                    if ref.get("source_type") == "uploaded_media":
                        upload_owner = db.execute(text("SELECT owner_hash FROM knowledge_upload_owners WHERE upload_id=CAST(:id AS uuid)"),
                                                  {"id": ref.get("source_id")}).scalar_one_or_none()
                        if upload_owner != identity: raise ActionError("source_not_owned")
                _validate_source_refs(db, refs=refs, symbol=_supported_symbol(symbol), decision_at=datetime.now(UTC))
                job = enqueue_job(db=db, symbol=symbol, kind="new",
                                  idempotency_key=_underlying_key(identity, request_key), source_refs=refs)
                result = {"status": job.status, "job_id": str(job.id)}
                job_kind, job_id = "forecast", job.id
            elif action == "import":
                from ..knowledge.api import ImportRequest, import_existing
                source_type = {"official_filing": "sec", "uploaded_media": "upload", "ai_analysis": "analysis"}.get(payload.get("source_type", "official_filing"))
                result = import_existing(ImportRequest(source_type=source_type, source_ids=[str(source_id)]), x_chat_session=credential)
                result["status"] = "succeeded" if result.get("accepted_sources") == 1 else "failed"
                if result["status"] == "failed": result["error_code"] = "source_not_imported"
                job_kind = job_id = None
            elif source_id:
                result = _index_selected(db, source_id, identity, payload.get("max_tasks", 20))
                job_kind = job_id = None
            else:
                from ..knowledge.api import IndexRequest, run_index
                result = run_index(IndexRequest(max_tasks=payload.get("max_tasks", 20)), x_chat_session=credential)
                remaining = result.get("remaining", 0)
                result["remaining"] = remaining
                result["status"] = _index_status(result, remaining)
                job_kind = job_id = None
            db.execute(text("""UPDATE agent_action_requests SET status=:status,job_kind=:kind,job_id=:job,
                result=CAST(:result AS jsonb),updated_at=now() WHERE id=:id"""),
                {"status": result["status"], "kind": job_kind, "job": job_id,
                 "result": json.dumps(result, default=str), "id": row_id})
            db.commit()
        except Exception as exc:
            db.rollback()
            code = exc.code if isinstance(exc, ActionError) else "service_unavailable"
            db.execute(text("""UPDATE agent_action_requests SET status='failed',
                result=CAST(:result AS jsonb),updated_at=now() WHERE id=:id"""),
                       {"id": row_id, "result": json.dumps({"status": "failed", "error_code": code})})
            db.commit()
            if isinstance(exc, ActionError): raise
            raise ActionError("service_unavailable") from exc
        stored = db.execute(text("SELECT * FROM agent_action_requests WHERE id=:id"), {"id": row_id}).mappings().one()
        return _status(db, dict(stored))
      finally:
       connection.execute(text("SELECT pg_advisory_unlock(hashtext(:lock))"),
                          {"lock": identity + ":" + request_key})
       connection.commit()
