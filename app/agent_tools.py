from __future__ import annotations
import os
import re
import hashlib
from datetime import UTC, date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from langchain_core.tools import tool
from sqlalchemy import text
from .config import DATABASE_URL
from .symbols import normalize_market_symbol
from .knowledge.retrieval import KnowledgeUnavailable, retrieve_faq, retrieve_materials, retrieve_analysis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

def _rag_enabled() -> bool:
    return os.getenv("RAG_ENABLED", "false").strip().lower() in {"1", "true", "yes"}

async def _legacy_materials(symbol: str, keyword: str, limit: int) -> dict:
    from .knowledge.runtime import current_research_context
    context = current_research_context()
    owner = context.owner_hash if context else None
    # The stream sets this only after checking the configured owner credential.
    allow_unmapped = bool(context and getattr(context, "legacy_unmapped_owner", False))
    now = datetime.now(UTC)
    cutoff = context.cutoff if context and context.cutoff else now
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("legacy materials cutoff must be timezone-aware")
    cutoff = cutoff.astimezone(UTC)
    params = {"symbol": symbol, "pattern": f"%{_like(keyword)}%", "limit": limit,
              "owner": owner, "allow_unmapped": allow_unmapped, "cutoff": cutoff, "now": now}
    owner_table = await _query("SELECT to_regclass('public.knowledge_upload_owners') IS NOT NULL AS present", {})
    has_owner_table = bool(owner_table and owner_table[0]["present"])
    owner_join = "LEFT JOIN knowledge_upload_owners o ON o.upload_id=u.id" if has_owner_table else ""
    owner_filter = (
      "((o.owner_hash IS NOT NULL AND o.owner_hash=:owner) OR (o.owner_hash IS NULL AND :allow_unmapped))"
      if has_owner_table else "(:allow_unmapped AND CAST(:owner AS text) IS NOT NULL)"
    )
    uploaded = await _query(f"""SELECT u.id, u.title, u.source_url, u.published_at, u.observed_at,
      left(u.content_text,1200) AS excerpt, length(u.content_text) AS body_length, false AS source_truncated,
      'uploaded_media' AS material_type
      FROM uploaded_evidence u {owner_join}
      WHERE u.symbol=:symbol AND (u.title ILIKE :pattern ESCAPE E'\\\\' OR u.content_text ILIKE :pattern ESCAPE E'\\\\')
      AND u.observed_at<=:cutoff AND (u.published_at IS NULL OR u.published_at<=:cutoff)
      AND {owner_filter}
      ORDER BY u.published_at DESC NULLS LAST LIMIT :limit""", params)
    filings = await _query("""SELECT id, primary_document AS title, source_url, filed_at AS published_at,
      observed_at, content_observed_at, content_excerpt AS excerpt, length(content_excerpt) AS body_length,
      content_truncated AS source_truncated, content_excerpt_sha256, 'official_filing' AS material_type
      FROM sec_filing_inventory WHERE symbol=:symbol AND content_status='fetched'
      AND content_excerpt IS NOT NULL AND content_observed_at IS NOT NULL AND content_observed_at<=:cutoff
      AND content_observed_at<=:now AND observed_at<=:cutoff AND observed_at<=:now
      AND (filed_at IS NULL OR filed_at<=CAST(:cutoff AS date))
      AND (primary_document ILIKE :pattern ESCAPE E'\\\\' OR COALESCE(content_excerpt,'') ILIKE :pattern ESCAPE E'\\\\')
      ORDER BY filed_at DESC LIMIT :limit""", params)
    import hashlib
    filings = [row for row in filings if row.get("excerpt") and row.get("content_excerpt_sha256")
               and hashlib.sha256(row["excerpt"].encode()).hexdigest()==row["content_excerpt_sha256"]]
    rows = sorted(uploaded + filings, key=lambda row: str(row.get("published_at") or ""), reverse=True)[:limit]
    for row in rows:
        if row.get("material_type") == "official_filing" and row.get("content_observed_at"):
            row["observed_at"] = max(row["observed_at"], row["content_observed_at"])
        row.pop("content_observed_at", None)
        row.pop("content_excerpt_sha256", None)
        source_truncated=row.pop("source_truncated",False);body_length=row.pop("body_length",0)
        row["excerpt_coverage"]="partial_excerpt" if source_truncated or body_length>1200 else "complete_saved_body"
        row["excerpt"] = row["excerpt"][:1200]
    return {"ok": True, "data": [{k:_json(v) for k,v in row.items()} for row in rows],
            "source": "uploaded_evidence + sec_filing_inventory (saved records)",
            "as_of": _json(max((r["observed_at"] for r in rows if r.get("observed_at")), default=None)),
            "_retrieval_mode": "legacy"}

async def _legacy_faq(keyword: str) -> dict:
    pattern=f"%{_like(keyword)}%"
    rows=await _query("SELECT question, answer, category FROM agent_faq WHERE question ILIKE :pattern ESCAPE E'\\\\' OR category ILIKE :pattern ESCAPE E'\\\\' ORDER BY id LIMIT 5", {"pattern":pattern})
    return {"ok":True,"data":rows,"source":"agent_faq (saved FAQ)","as_of":None,"_retrieval_mode":"legacy"}

ASYNC_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg://")
_async_engine = create_async_engine(ASYNC_URL, pool_pre_ping=True, connect_args={"connect_timeout": 3})
_async_session = async_sessionmaker(_async_engine, expire_on_commit=False)

def normalize_agent_symbol(value: str) -> str:
    return normalize_market_symbol(value)

def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def _json(value):
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, (dict, list, int, float, bool)): return value
    return str(value) if value is not None else None

class SymbolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=12)
    @field_validator("symbol")
    @classmethod
    def valid(cls, value): return normalize_agent_symbol(value)
class PriceArgs(SymbolArgs):
    limit: int = Field(default=5, ge=1, le=30)
class MaterialArgs(SymbolArgs):
    keyword: str = Field(min_length=1, max_length=80)
    source_id: str | None = Field(default=None, max_length=36)
    limit: int = Field(default=5, ge=1, le=10)
class FilingArgs(SymbolArgs):
    form: Literal["10-K", "10-Q", "8-K"] | None = None
    accession_number: str | None = Field(default=None, pattern=r"^\d{10}-\d{2}-\d{6}$")
    source_id: str | None = Field(default=None, max_length=36)
    filed_year: int | None = Field(default=None, ge=1990, le=2100)
    financial_only: bool = False
    limit: int = Field(default=5, ge=1, le=10)
class FAQArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: str = Field(min_length=1, max_length=80)

async def _query(sql, params):
    async with _async_session() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL statement_timeout = '4000ms'"))
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

async def _index_state(source_id: str) -> str:
    present=await _query("SELECT to_regclass('public.knowledge_documents') IS NOT NULL AS present",{})
    if not present or not present[0]["present"]: return "not_imported"
    rows=await _query("""SELECT d.status, count(c.id) FILTER (WHERE c.status='ready') AS ready_chunks
      FROM knowledge_documents d LEFT JOIN knowledge_chunks c ON c.document_id=d.id
      WHERE d.source_id=:id GROUP BY d.id,d.status ORDER BY d.created_at DESC LIMIT 1""",{"id":source_id})
    if not rows:return "not_imported"
    return "ready" if rows[0]["ready_chunks"] else "index_pending"

async def _expanded_retrieval(function, symbol: str, keyword: str, limit: int, source_id: str | None):
    from .knowledge.query_understanding import expanded_queries
    queries = [keyword] if source_id else expanded_queries(keyword)
    rows=[];seen=set();first=None
    for query in queries[:3]:
        found = await function(symbol, query, limit, source_id=source_id) if source_id else await function(symbol, query, limit)
        if first is None:first=found
        for row in found.get("data") or []:
            key=(row.get("id"),row.get("analysis_version"),row.get("material_type")) if isinstance(row,dict) else None
            if key not in seen:
                seen.add(key);rows.append(row)
            if len(rows)>=limit:break
        if len(rows)>=limit:break
    return {**first,"data":rows[:limit],"as_of":max((str(row.get("observed_at")) for row in rows if isinstance(row,dict) and row.get("observed_at")),default=first.get("as_of"))}

@tool(args_schema=PriceArgs)
async def query_stock_prices(symbol: str, limit: int = 5) -> dict:
    """Read saved daily stock prices; this is not a live quote."""
    rows = await _query("SELECT symbol, trading_date, open, high, low, close, volume, source, fetched_at FROM market_prices WHERE symbol=:symbol ORDER BY trading_date DESC LIMIT :limit", {"symbol":symbol,"limit":limit})
    return {"ok":True,"data":[{k:_json(v) for k,v in r.items()} for r in rows],"source":"market_prices","as_of":_json(rows[0]["fetched_at"]) if rows else None}

@tool(args_schema=SymbolArgs)
async def query_latest_forecast(symbol: str) -> dict:
    """Read the latest saved research forecast snapshot."""
    try:
        rows = await _query("SELECT id, root_id, version_no, decision_at, model_status, decision_probabilities, joint_probabilities, baseline_probabilities, research_brief, created_at FROM forecast_versions_v2 WHERE symbol=:symbol AND root_id IN (SELECT id FROM forecast_versions_v2 WHERE symbol=:symbol AND root_id=id ORDER BY created_at DESC, id DESC LIMIT 1) ORDER BY version_no DESC, created_at DESC, id DESC LIMIT 1", {"symbol":symbol})
        if rows:
            row=rows[0]
            brief=row.get("research_brief")
            probabilities=next((row.get(k) for k in ("decision_probabilities","joint_probabilities","baseline_probabilities") if row.get(k) not in (None, "null")),None)
            data={k:_json(v) for k,v in row.items() if k not in ("research_brief","decision_probabilities","joint_probabilities","baseline_probabilities")}
            data["probabilities"] = probabilities
            data["research_brief_summary"]=(str(brief)[:1200] if brief is not None else None)
            return {"ok":True,"data":[data],"source":"forecast_versions_v2 workspace current root","as_of":_json(row["created_at"])}
        return {"ok":True,"data":[],"source":"forecast_versions_v2 workspace current root","as_of":None}
    except Exception:
        raise

@tool(args_schema=MaterialArgs)
async def query_materials(symbol: str, keyword: str, source_id: str | None = None, limit: int = 5) -> dict:
    """Find saved uploaded and official research material using a natural language question."""
    if source_id and not _rag_enabled():
        direct = await query_sec_filings.coroutine(symbol=symbol, source_id=source_id, limit=1)
        if direct.get("data"):
            return {**direct, "_retrieval_mode": "direct_original_index_pending"}
    if not _rag_enabled() and source_id is None:
        return await _legacy_materials(symbol, keyword, limit)
    try:
        found = await _expanded_retrieval(retrieve_materials, symbol, keyword, limit, source_id)
        if found.get("data") or not source_id: return found
    except KnowledgeUnavailable as exc:
        if not source_id:
            return {"ok":False,"data":None,"error_code":exc.code,"source":"knowledge_chunks","as_of":None}
    direct = await query_sec_filings.coroutine(symbol=symbol, source_id=source_id, limit=1)
    return {**direct, "_retrieval_mode": "direct_original_index_pending"} if direct.get("data") else {
        "ok": False, "data": None, "error_code": "no_source", "source": "knowledge_chunks", "as_of": None}


@tool(args_schema=MaterialArgs)
async def search_material_analysis(symbol: str, keyword: str, source_id: str | None = None, limit: int = 5) -> dict:
    """Search saved AI analysis as derived material; verify claims against original sources when asked."""
    if source_id and not _rag_enabled():
        direct = await query_material_analysis.coroutine(symbol=symbol, source_id=source_id)
        return {**direct, "_retrieval_mode": "direct_derived_index_pending"} if direct.get("data") else {
            "ok": False, "data": None, "error_code": "no_analysis", "source": "material_analysis_versions", "as_of": None}
    if not _rag_enabled():
        return {"ok":False,"data":None,"error_code":"knowledge_unavailable","source":"material_analysis_versions","as_of":None}
    try:
        found = await _expanded_retrieval(retrieve_analysis, symbol, keyword, limit, source_id)
        if found.get("data") or not source_id: return found
    except KnowledgeUnavailable as exc:
        if not source_id: return {"ok":False,"data":None,"error_code":exc.code,"source":"material_analysis_versions","as_of":None}
    direct = await query_material_analysis.coroutine(symbol=symbol, source_id=source_id)
    return {**direct, "_retrieval_mode": "direct_derived_index_pending"} if direct.get("data") else {
        "ok":False,"data":None,"error_code":"no_analysis","source":"material_analysis_versions","as_of":None}

@tool(args_schema=FilingArgs)
async def query_sec_filings(symbol: str, form: str | None = None, accession_number: str | None = None,
                            source_id: str | None = None, filed_year: int | None = None,
                            financial_only: bool = False, limit: int = 5) -> dict:
    """Read saved SEC filing inventory without contacting SEC."""
    from .knowledge.runtime import current_research_context
    context=current_research_context()
    cutoff=context.cutoff if context and context.cutoff else datetime.now(UTC)
    filters=""
    params={"symbol":symbol,"limit":min(50,max(20,limit*5)),"cutoff":cutoff}
    if form is not None: filters+=" AND form=:form";params["form"]=form
    if accession_number is not None: filters+=" AND accession_number=:accession";params["accession"]=accession_number
    if source_id is not None:
        try: source_id=str(__import__('uuid').UUID(source_id))
        except ValueError:return {"ok":False,"data":None,"error_code":"invalid_source_id","source":"sec_filing_inventory","as_of":None}
        filters+=" AND id=CAST(:source_id AS uuid)";params["source_id"]=source_id
    if filed_year is not None: filters+=" AND EXTRACT(YEAR FROM filed_at)=:year";params["year"]=filed_year
    if financial_only: filters+=" AND form IN ('10-K','10-Q')"
    rows=await _query("SELECT id, accession_number, form, filed_at, source_url, content_source_url, content_status, content_truncated, content_excerpt_sha256, content_excerpt, observed_at FROM sec_filing_inventory WHERE symbol=:symbol AND content_status='fetched' AND observed_at<=:cutoff AND content_observed_at<=:cutoff AND filed_at<=CAST(:cutoff AS date)"+filters+" ORDER BY filed_at DESC, accession_number DESC LIMIT :limit",params)
    import hashlib
    for row in rows:
        body=row.get("content_excerpt") or ""
        digest=row.get("content_excerpt_sha256")
        row["body_hash_valid"]=bool(body and digest and hashlib.sha256(body.encode()).hexdigest()==digest)
        row["excerpt"]=_substantive_excerpt(body)
        row["excerpt_coverage"]="partial_excerpt" if row.get("content_truncated") or len(row["excerpt"])<len(body) else "complete_saved_body"
        row.pop("content_excerpt",None);row.pop("content_excerpt_sha256",None)
    rows=[row for row in rows if row.get('body_hash_valid')]
    if limit == 1 and rows:
        from .agent_workflow.source_selection import choose_filing
        selected = choose_filing(rows, accession_number=accession_number, form=form,
                                 year=filed_year, financial_only=financial_only)
        rows = [selected] if selected else []
    rows = rows[:limit]
    for row in rows: row["index_status"] = await _index_state(str(row["id"]))
    return {"ok":True,"data":[{k:_json(v) for k,v in r.items()} for r in rows],"source":"sec_filing_inventory (saved inventory)","as_of":_json(rows[0]["observed_at"]) if rows else None,
            "error_code":"no_source" if not rows else None}

@tool(args_schema=FAQArgs)
async def query_research_faq(keyword: str) -> dict:
    """Answer a natural language question from the approved research FAQ."""
    if not _rag_enabled():
        return await _legacy_faq(keyword)
    try:
        return await retrieve_faq(keyword)
    except KnowledgeUnavailable as exc:
        return {"ok":False,"data":None,"error_code":exc.code,"source":"knowledge_chunks:research_faq","as_of":None}

def _substantive_excerpt(body: str, limit: int=3000) -> str:
    """Select substantive filing text while reporting that it is only an excerpt."""
    text=body or ""
    # SEC HTML extraction can start with thousands of XBRL contexts. Prefer an
    # actual statement heading to a cover or a table-of-contents Item entry.
    statement=re.search(r"(?im)^[ \t]*(?:[A-Z][A-Z &'(),.-]+ )?(?:CONDENSED |CONSOLIDATED |COMBINED )*(?:BALANCE SHEETS|STATEMENTS OF (?:OPERATIONS|INCOME|CASH FLOWS|COMPREHENSIVE INCOME|SHAREHOLDERS.? EQUITY))\b[^\n]*",text)
    item=re.search(r"(?im)^[ \t]*item\s+\d+(?:\.\d+)?\b[^\n]*",text)
    start=statement.start() if statement else item.start() if item else 0
    passage=text[start:]
    # The limit applies to the UTF-8 tool payload, including multibyte text.
    clipped=passage.encode('utf-8')[:limit].decode('utf-8','ignore')
    if len(clipped)<len(passage):
        boundary=clipped.rfind('\n')
        if boundary>=limit//2: clipped=clipped[:boundary]
    return clipped

def _safe_source_url(value: str | None) -> str | None:
    if not value:return None
    from urllib.parse import parse_qsl,urlsplit,urlunsplit,urlencode
    try:parts=urlsplit(value)
    except ValueError:return None
    if parts.scheme not in {'https','http'} or not parts.hostname:return None
    try:host=parts.hostname.encode('idna').decode('ascii')
    except UnicodeError:return None
    try:port=parts.port
    except ValueError:return None
    if port is not None:
        if not 1<=port<=65535:return None
        host=f'{host}:{port}'
    query=[(key,val) for key,val in parse_qsl(parts.query,keep_blank_values=True)
      if not re.search(r'(?:password|secret|token|credential|api.?key)',key,re.I)]
    return urlunsplit((parts.scheme,host,parts.path,urlencode(query,doseq=True),''))

class AnalysisArgs(SymbolArgs):
    accession_number: str | None = Field(default=None,min_length=10,max_length=24)
    source_id: str | None = Field(default=None,min_length=1,max_length=36)
    form: str | None = Field(default=None,pattern=r"^(10-Q|10-K|8-K)$")
    financial_only: bool = False

@tool(args_schema=AnalysisArgs)
async def query_material_analysis(symbol: str, accession_number: str | None = None, source_id: str | None = None,
                                  form: str | None = None, financial_only: bool = False) -> dict:
    """Read the latest saved AI analysis of a material; this is an AI summary, not original filing text or an official fact record."""
    from .knowledge.runtime import current_research_context
    context=current_research_context()
    if source_id:
        try: source_id=str(__import__('uuid').UUID(source_id))
        except ValueError:return {"ok":False,"data":None,"error_code":"invalid_source_id","source":"material_analysis_versions","as_of":None}
    now=datetime.now(UTC)
    cutoff=context.cutoff if context and context.cutoff else now
    if not isinstance(cutoff,datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        return {"ok":False,"data":None,"error_code":"invalid_material_analysis_cutoff","source":"material_analysis_versions","as_of":None}
    cutoff=cutoff.astimezone(UTC)
    params={"symbol":symbol,"accession":accession_number,"source_id":source_id,"form":form,
            "financial_only":financial_only,"cutoff":cutoff,"now":now}
    rows=await _query("""SELECT v.id,v.evidence_version_id,v.source_type,v.source_id,v.version_no,v.created_at,v.actual_model,v.payload,v.source_manifest,
      e.content_sha256,e.source_snapshot,e.citations,e.symbol,e.published_at,e.observed_at,
      s.accession_number,s.filed_at AS filing_filed_at,s.source_url AS filing_url,s.content_source_url,s.content_excerpt_sha256 AS filing_body_hash,s.content_excerpt AS filing_text,s.content_observed_at AS filing_content_observed_at,s.observed_at AS filing_observed_at,
      u.title AS upload_title,u.source_url AS upload_url,u.content_text AS upload_text,u.content_sha256 AS upload_body_hash,u.published_at AS upload_published_at,u.observed_at AS upload_observed_at
      FROM material_analysis_versions v JOIN evidence_event_versions_v2 e ON e.id=v.evidence_version_id
      LEFT JOIN sec_filing_inventory s ON v.source_type='official_filing' AND s.id=v.source_id AND s.symbol=e.symbol AND s.content_status='fetched'
      LEFT JOIN uploaded_evidence u ON v.source_type='uploaded_media' AND u.id=v.source_id AND u.symbol=e.symbol
      WHERE e.symbol=:symbol AND (CAST(:accession AS text) IS NULL OR s.accession_number=CAST(:accession AS text))
      AND (CAST(:source_id AS text) IS NULL OR CAST(v.source_id AS text)=CAST(:source_id AS text))
      AND (CAST(:form AS text) IS NULL OR s.form=CAST(:form AS text))
      AND (:financial_only=false OR s.form IN ('10-Q','10-K'))
      AND e.published_at<=:cutoff AND e.observed_at<=:cutoff AND e.published_at<=:now AND e.observed_at<=:now
      AND v.created_at<=:cutoff AND v.created_at<=:now
      ORDER BY COALESCE(s.filed_at,CAST(u.published_at AS date)) DESC NULLS LAST,
        v.version_no DESC,v.created_at DESC,v.id DESC LIMIT 100""",params)
    if source_id: rows=[row for row in rows if str(row.get('source_id'))==source_id]
    verified=[]
    for row in rows:
        if row['source_type']=='official_filing':
            original=_safe_source_url(row.get('content_source_url') or row.get('filing_url'))
            manifest=row.get('source_manifest') or {}; frozen=row.get('source_snapshot') or {}
            analysis_text=frozen.get('analysis_text')
            if (not row.get('accession_number') or not original or not row.get('filing_text') or not analysis_text
                or row.get('content_sha256')!=row.get('filing_body_hash')
                or hashlib.sha256(row['filing_text'].encode()).hexdigest()!=row.get('filing_body_hash')
                or manifest.get('source_id')!=str(row.get('source_id'))
                or manifest.get('source_type')!='official_filing'
                or manifest.get('content_sha256')!=row.get('filing_body_hash')
                or manifest.get('evidence_version_id')!=str(row.get('evidence_version_id'))
                or frozen.get('source_id')!=str(row.get('source_id'))
                or frozen.get('content_sha256')!=row.get('filing_body_hash')): continue
            if hashlib.sha256(analysis_text.encode()).hexdigest()!=manifest.get('analysis_text_sha256'):continue
            if any(not isinstance(row.get(key),datetime) or row[key].astimezone(UTC)>min(now,cutoff) for key in ('filing_content_observed_at','filing_observed_at')):continue
            if not isinstance(row.get('filing_filed_at'),date) or row['filing_filed_at']>min(now.date(),cutoff.date()):continue
            source_text=analysis_text
        else:
            owner=await _query("SELECT owner_hash FROM knowledge_upload_owners WHERE upload_id=CAST(:id AS uuid)",{"id":str(row['source_id'])})
            mapped=owner[0]['owner_hash'] if owner else None
            allowed=bool(context and mapped and context.owner_hash==mapped)
            if mapped is None: allowed=bool(context and getattr(context,'legacy_unmapped_owner',False))
            if not allowed or not row.get('upload_text'): continue
            manifest=row.get('source_manifest') or {}; frozen=row.get('source_snapshot') or {}
            analysis_text=frozen.get('analysis_text')
            if (not analysis_text or row.get('content_sha256')!=row.get('upload_body_hash')
                or manifest.get('source_id')!=str(row.get('source_id'))
                or manifest.get('source_type')!='uploaded_media'
                or manifest.get('content_sha256')!=row.get('upload_body_hash')
                or manifest.get('evidence_version_id')!=str(row.get('evidence_version_id'))
                or frozen.get('source_id')!=str(row.get('source_id'))
                or frozen.get('content_sha256')!=row.get('upload_body_hash')
                or hashlib.sha256(analysis_text.encode()).hexdigest()!=manifest.get('analysis_text_sha256')): continue
            if any(not isinstance(row.get(key),datetime) or row[key].astimezone(UTC)>min(now,cutoff) for key in ('upload_published_at','upload_observed_at')):continue
            original=_safe_source_url(row.get('upload_url'))
            if not original:continue
            source_text=analysis_text
        summary=(row.get('payload') or {}).get('summary')
        if not isinstance(summary,str) or not summary.strip():continue
        quotes=[]
        for group in ('facts','supporting','counter','uncertainties','key_numbers'):
            for item in (row.get('payload') or {}).get(group,[]) or []:
                for citation in item.get('citations',[]) if isinstance(item,dict) else []:
                    quote=citation.get('quote') if isinstance(citation,dict) else None
                    if quote and quote in source_text and quote not in quotes: quotes.append(str(quote)[:500])
        manifest=row.get('source_manifest') or {}
        safe_manifest={key:manifest[key] for key in ('source_type','source_id','source_hash','content_sha256','truncated','coverage_incomplete') if key in manifest}
        verified.append({"source_type":row['source_type'],"source_id":str(row['source_id']),"accession_number":row.get('accession_number'),
          "source_url":original,"analysis_id":str(row['id']),"version_no":row['version_no'],"created_at":_json(row['created_at']),
          "published_at":_json(row['published_at']),"observed_at":_json(row['observed_at']),"model":row['actual_model'],
          "summary":summary[:900],"evidence_quotes":quotes[:3],"source_manifest":safe_manifest,
          "classification":"saved_ai_analysis_not_official_source_text"})
    latest=[];seen=set()
    for row in verified:
        key=row['source_id']
        if key not in seen:latest.append(row);seen.add(key)
    for row in latest[:1]: row["index_status"] = await _index_state(row["source_id"])
    # One requested analysis keeps the complete tool response safely below the 8KB tool budget.
    return {"ok":True,"data":latest[:1],"source":"material_analysis_versions joined to frozen evidence and current source","as_of":latest[0]['created_at'] if latest else None,
            "error_code":"no_analysis" if not latest else None}

from .agent_web_research import web_research

TOOL_REGISTRY={item.name:item for item in [query_stock_prices,query_latest_forecast,query_materials,query_sec_filings,query_research_faq,query_material_analysis,search_material_analysis,web_research]}
def tool_schemas():
    return [{"type":"function","function":{"name":name,"description":item.description,"parameters":item.args_schema.model_json_schema()}} for name,item in TOOL_REGISTRY.items()]
