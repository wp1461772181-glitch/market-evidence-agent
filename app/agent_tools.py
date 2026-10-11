from __future__ import annotations
import os
import re
from datetime import UTC, date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from langchain_core.tools import tool
from sqlalchemy import text
from .config import DATABASE_URL
from .symbols import normalize_market_symbol
from .knowledge.retrieval import KnowledgeUnavailable, retrieve_faq, retrieve_materials
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
      left(u.content_text,1200) AS excerpt, 'uploaded_media' AS material_type
      FROM uploaded_evidence u {owner_join}
      WHERE u.symbol=:symbol AND (u.title ILIKE :pattern ESCAPE E'\\\\' OR u.content_text ILIKE :pattern ESCAPE E'\\\\')
      AND u.observed_at<=:cutoff AND (u.published_at IS NULL OR u.published_at<=:cutoff)
      AND {owner_filter}
      ORDER BY u.published_at DESC NULLS LAST LIMIT :limit""", params)
    filings = await _query("""SELECT id, primary_document AS title, source_url, filed_at AS published_at,
      observed_at, content_observed_at, content_excerpt AS excerpt, content_excerpt_sha256, 'official_filing' AS material_type
      FROM sec_filing_inventory WHERE symbol=:symbol AND review_status='accepted' AND content_status='fetched'
      AND content_excerpt IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_at<=:cutoff
      AND reviewed_at<=:now AND content_observed_at IS NOT NULL AND content_observed_at<=:cutoff
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
    limit: int = Field(default=5, ge=1, le=10)
class FilingArgs(SymbolArgs):
    form: Literal["10-K", "10-Q", "8-K"] | None = None
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

@tool(args_schema=PriceArgs)
async def query_stock_prices(symbol: str, limit: int = 5) -> dict:
    """Read saved daily stock prices; this is not a live quote."""
    rows = await _query("SELECT trading_date, open, high, low, close, volume, source, fetched_at FROM market_prices WHERE symbol=:symbol ORDER BY trading_date DESC LIMIT :limit", {"symbol":symbol,"limit":limit})
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
async def query_materials(symbol: str, keyword: str, limit: int = 5) -> dict:
    """Find saved uploaded and official research material using a natural language question."""
    if not _rag_enabled():
        return await _legacy_materials(symbol, keyword, limit)
    try:
        return await retrieve_materials(symbol, keyword, limit)
    except KnowledgeUnavailable as exc:
        return {"ok":False,"data":None,"error_code":exc.code,"source":"knowledge_chunks","as_of":None}

@tool(args_schema=FilingArgs)
async def query_sec_filings(symbol: str, form: str | None = None, limit: int = 5) -> dict:
    """Read saved SEC filing inventory without contacting SEC."""
    if form is None:
        rows=await _query("SELECT id, form, filed_at, source_url, content_status, left(content_excerpt,1200) AS excerpt, observed_at FROM sec_filing_inventory WHERE symbol=:symbol ORDER BY filed_at DESC LIMIT :limit",{"symbol":symbol,"limit":limit})
    else:
        rows=await _query("SELECT id, form, filed_at, source_url, content_status, left(content_excerpt,1200) AS excerpt, observed_at FROM sec_filing_inventory WHERE symbol=:symbol AND form=:form ORDER BY filed_at DESC LIMIT :limit",{"symbol":symbol,"form":form,"limit":limit})
    return {"ok":True,"data":[{k:_json(v) for k,v in r.items()} for r in rows],"source":"sec_filing_inventory (saved inventory)","as_of":_json(rows[0]["observed_at"]) if rows else None}

@tool(args_schema=FAQArgs)
async def query_research_faq(keyword: str) -> dict:
    """Answer a natural language question from the approved research FAQ."""
    if not _rag_enabled():
        return await _legacy_faq(keyword)
    try:
        return await retrieve_faq(keyword)
    except KnowledgeUnavailable as exc:
        return {"ok":False,"data":None,"error_code":exc.code,"source":"knowledge_chunks:research_faq","as_of":None}

TOOL_REGISTRY={item.name:item for item in [query_stock_prices,query_latest_forecast,query_materials,query_sec_filings,query_research_faq]}
def tool_schemas():
    return [{"type":"function","function":{"name":name,"description":item.description,"parameters":item.args_schema.model_json_schema()}} for name,item in TOOL_REGISTRY.items()]
