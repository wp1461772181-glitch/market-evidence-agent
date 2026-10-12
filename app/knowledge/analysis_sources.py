"""Fail-closed adapter for immutable saved material analyses."""
from __future__ import annotations
import hashlib
import json
from datetime import UTC, datetime, time
from uuid import UUID
from urllib.parse import urlsplit
from sqlalchemy import text
from sqlalchemy.orm import Session
from .sources import SourceDocument


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x)).encode()).hexdigest()

def _material_analysis_digest(value) -> str:
    # Match app.material_analysis._digest exactly (json.dumps defaults to ensure_ascii=True).
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _utc(value):
    if value is None: return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def load_analysis_document(session: Session, analysis_id, *, cutoff=None, require_latest: bool = True) -> SourceDocument | None:
    """Build one validated analysis document; unknown upload ownership is never public."""
    try: aid = str(UUID(str(analysis_id)))
    except (ValueError, TypeError, AttributeError): return None
    now = datetime.now(UTC); cutoff = _utc(cutoff) if cutoff else now
    if cutoff is None or cutoff > now: return None
    row = session.execute(text("""
      SELECT v.id,v.source_type,v.source_id,v.version_no,v.created_at,v.actual_model,v.requested_model,v.schema_version,v.prompt_version,
        v.payload,v.source_manifest,v.input_fingerprint,v.evidence_version_id,e.symbol,e.published_at,e.observed_at,e.content_sha256,e.source_type AS evidence_source_type,e.source_id AS evidence_source_id,
        e.source_snapshot,s.accession_number,s.form,s.filed_at,s.source_url AS sec_url,s.content_source_url,s.content_document_name,
        s.content_excerpt AS sec_text,s.content_excerpt_sha256,s.content_observed_at,s.observed_at AS sec_observed,s.content_status,s.content_truncated,
        u.title AS upload_title,u.source_url AS upload_url,u.content_text AS upload_text,u.raw_content AS upload_raw_content,u.content_sha256 AS upload_hash,
        u.published_at AS upload_published,u.observed_at AS upload_observed,u.status AS upload_status,o.owner_hash
      FROM material_analysis_versions v
      JOIN evidence_event_versions_v2 e ON e.id=v.evidence_version_id
      LEFT JOIN sec_filing_inventory s ON v.source_type='official_filing' AND s.id=v.source_id AND s.symbol=e.symbol
      LEFT JOIN uploaded_evidence u ON v.source_type='uploaded_media' AND u.id=v.source_id AND u.symbol=e.symbol
      LEFT JOIN knowledge_upload_owners o ON o.upload_id=u.id
      WHERE v.id=CAST(:id AS uuid) AND e.published_at<=:cutoff AND e.observed_at<=:cutoff AND v.created_at<=:cutoff
        AND e.published_at<=:now AND e.observed_at<=:now AND v.created_at<=:now
    """), {"id": aid, "cutoff": cutoff, "now": now}).mappings().first()
    if not row: return None
    row = dict(row)
    if any(_utc(row.get(key)) is None or _utc(row.get(key)) > min(now, cutoff) for key in ("created_at", "published_at", "observed_at")): return None
    manifest = row.get("source_manifest") or {}; frozen = row.get("source_snapshot") or {}; payload = row.get("payload") or {}
    source_type = row["source_type"]
    if source_type == "official_filing":
        body = row.get("sec_text"); body_hash = row.get("content_excerpt_sha256")
        title = row.get("content_document_name") or row.get("form") or "SEC filing"
        url = row.get("content_source_url") or row.get("sec_url"); source_observed = row.get("sec_observed"); content_observed = row.get("content_observed_at")
        scope, visibility, owner = "public", "public", None
        if row.get("content_status") != "fetched" or not row.get("accession_number") or not row.get("filed_at"): return None
        if (_utc(source_observed) is None or _utc(content_observed) is None or row["filed_at"] > cutoff.date()
            or _utc(source_observed)>min(now,cutoff) or _utc(content_observed)>min(now,cutoff)): return None
    elif source_type == "uploaded_media":
        body = row.get("upload_text"); body_hash = row.get("upload_hash"); title = row.get("upload_title") or "uploaded material"
        url = row.get("upload_url"); source_observed = row.get("upload_observed"); content_observed = source_observed
        owner = row.get("owner_hash")
        if not owner or len(owner) != 64: return None
        scope, visibility = "private", "private"
        if (row.get("upload_status")!="unconfirmed" or _utc(row.get("upload_published")) is None or _utc(source_observed) is None
            or _utc(row.get("upload_published"))>min(now,cutoff) or _utc(source_observed)>min(now,cutoff)): return None
    else: return None
    if not body or not body_hash or row["content_sha256"] != body_hash: return None
    # The current source publication is the effective publication boundary.
    # SEC filing dates are calendar dates; normalize their effective time to UTC.
    current_published = (
        datetime.combine(row["filed_at"], time.min, UTC)
        if source_type == "official_filing" and row.get("filed_at")
        else _utc(row.get("upload_published"))
    )
    if current_published is None or current_published > min(now, cutoff): return None
    if source_type == "official_filing":
        if hashlib.sha256(body.encode()).hexdigest() != body_hash: return None
    else:
        raw=row.get("upload_raw_content")
        if not raw or hashlib.sha256(bytes(raw)).hexdigest()!=body_hash or frozen.get("content_text")!=body: return None
    analysis_text = frozen.get("analysis_text")
    if (row.get("evidence_source_type")!=source_type or str(row.get("evidence_source_id"))!=str(row.get("source_id"))
        or frozen.get("source_type")!=source_type or frozen.get("symbol")!=row.get("symbol")
        or not analysis_text or manifest.get("source_type") != source_type or manifest.get("source_id") != str(row["source_id"])
        or manifest.get("content_sha256") != body_hash or manifest.get("evidence_version_id") != str(row["evidence_version_id"])
        or frozen.get("source_id") != str(row["source_id"]) or frozen.get("content_sha256") != body_hash
        or hashlib.sha256(analysis_text.encode()).hexdigest() != manifest.get("analysis_text_sha256")):
        return None
    expected_model_fingerprint=_material_analysis_digest({"model":row["requested_model"],"schema":row["schema_version"],"prompt":row["prompt_version"]})
    expected_input_fingerprint=_material_analysis_digest({"source_type":source_type,"source_id":str(row["source_id"]),
      "text_sha256":hashlib.sha256(analysis_text.encode("utf-8")).hexdigest(),"schema":row["schema_version"],
      "prompt":row["prompt_version"],"model_fingerprint":expected_model_fingerprint})
    if row.get("input_fingerprint")!=expected_input_fingerprint: return None
    if not isinstance(url, str) or not url.startswith("https://"): return None
    try:
        parsed_url = urlsplit(url)
        if parsed_url.username is not None or parsed_url.password is not None: return None
        from ..agent_tools import _safe_source_url
        url=_safe_source_url(url)
    except Exception: return None
    if not url: return None
    try:
        from ..material_analysis_schema import validate_material_analysis
        parsed = validate_material_analysis(payload, analysis_text)
    except (ValueError, TypeError): return None
    latest = session.execute(text("""SELECT id FROM material_analysis_versions WHERE source_type=:type AND source_id=CAST(:sid AS uuid)
      AND (created_at,version_no,id) >= (CAST(:created AS timestamptz),:version,CAST(:id AS uuid))
      ORDER BY created_at DESC,version_no DESC,id DESC LIMIT 1"""), {"type":source_type,"sid":str(row["source_id"]),"created":row["created_at"],"version":row["version_no"],"id":aid}).scalar()
    if require_latest and str(latest) != aid: return None
    if latest is None: return None
    if parsed.schema_version != row["schema_version"] or parsed.prompt_version != row["prompt_version"] or not row.get("actual_model") or not row.get("requested_model"): return None
    evidence = []
    def citation_lines(items):
        lines=[]
        for item in items:
            for citation in item.citations:
                lines.append(f'> 原文引文：{citation.quote}')
        return lines
    sections = [("摘要（AI 分析）", [parsed.summary])]
    sections += [("事实（AI 提取，未独立核验）", [f"{x.statement}\n"+"\n".join(citation_lines([x])) for x in parsed.facts])]
    sections += [("支持推论（AI 推论）", [f"{x.statement}\n理由：{x.rationale}\n依据事实：{', '.join(x.fact_ids)}\n"+"\n".join(citation_lines([x])) for x in parsed.supporting])]
    sections += [("反对推论（AI 推论）", [f"{x.statement}\n理由：{x.rationale}\n依据事实：{', '.join(x.fact_ids)}\n"+"\n".join(citation_lines([x])) for x in parsed.counter])]
    sections += [("关键数字（AI 提取，保留原期间）", [f"{x.name}：{x.value_text}"+(f"（期间：{x.period}）" if x.period else "")+"\n"+"\n".join(citation_lines([x])) for x in parsed.key_numbers])]
    sections += [("不确定项（限已分析材料覆盖范围）", [f"{x.statement}\n原因：{x.reason}\n"+"\n".join(citation_lines([x])) for x in parsed.uncertainties])]
    text_parts=[f"# {heading}\n\n"+"\n\n".join(f"## 条目 {i+1}\n{item}" for i,item in enumerate(items)) for heading,items in sections if items]
    original_type="official_filing" if source_type=="official_filing" else "uploaded_media"
    logical_id=f"{original_type}:{row['source_id']}"
    period=f"{row.get('form') or ''} filed {row.get('filed_at') or ''}".strip() if source_type=="official_filing" else "not specified in saved source"
    text_body=(f"派生 AI 分析；模型：{row['actual_model']}；分析版本：v{row['version_no']}；分析 UUID：{aid}；分析时间：{_utc(row['created_at']).isoformat()}\n"
      f"原始来源类型：{original_type}；原始来源 UUID：{row['source_id']}；期间：{period}；原始来源 URL：{url}\n\n")+"\n\n".join(text_parts)
    source_truncated=bool(row.get("content_truncated") or manifest.get("truncated") or manifest.get("coverage_incomplete") or frozen.get("analysis_text_truncated") or frozen.get("coverage_incomplete"))
    provenance={"payload":payload,"manifest":manifest,"analysis_id":aid,"version_no":row["version_no"],"model":row["actual_model"],"requested_model":row["requested_model"],"schema":row["schema_version"],"prompt":row["prompt_version"],"input":row["input_fingerprint"],"evidence":str(row["evidence_version_id"]),"source_body_hash":body_hash,"owner":owner,"symbol":row["symbol"],"url":url,"title":title,"analysis_text":analysis_text,"created":row["created_at"],"published":current_published,"evidence_published":row["published_at"],"source_observed":source_observed,"content_observed":content_observed,"filed_at":row.get("filed_at"),"accession":row.get("accession_number"),"form":row.get("form"),"source_status":row.get("content_status") if source_type=="official_filing" else row.get("upload_status"),"source_truncated":source_truncated,"coverage":manifest.get("coverage"),"coverage_incomplete":bool(manifest.get("coverage_incomplete"))}
    source_hash=_digest(provenance)
    generated=_utc(row["created_at"]); evidence_published=_utc(row.get("published_at")); observed=_utc(source_observed); content_seen=_utc(content_observed)
    available=max(x for x in (generated,current_published,evidence_published,observed,content_seen) if x is not None)
    return SourceDocument("material_analysis_versions",logical_id,aid,source_hash,"ai_analysis",row["symbol"],scope,visibility,owner,
      f"已保存 AI 分析 · {row['symbol']} · {title} · v{row['version_no']}",url,current_published,observed,available,source_truncated,"saved_analysis_v1",text_body,category=f"AI analysis · {original_type}")
