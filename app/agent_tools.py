from __future__ import annotations
import re
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from langchain_core.tools import tool
from sqlalchemy import text
from .config import DATABASE_URL
from .symbols import normalize_market_symbol
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    """Search saved uploaded and official research materials by literal keyword."""
    pattern=f"%{_like(keyword)}%"
    params={"symbol":symbol,"pattern":pattern,"limit":limit}
    uploaded=await _query("SELECT id, title, source_url, published_at, observed_at, left(content_text,1200) AS excerpt, 'uploaded_media' AS material_type FROM uploaded_evidence WHERE symbol=:symbol AND (title ILIKE :pattern ESCAPE E'\\\\' OR content_text ILIKE :pattern ESCAPE E'\\\\') ORDER BY published_at DESC LIMIT :limit",params)
    filings=await _query("SELECT id, primary_document AS title, source_url, filed_at AS published_at, observed_at, left(content_excerpt,1200) AS excerpt, 'official_filing' AS material_type FROM sec_filing_inventory WHERE symbol=:symbol AND (primary_document ILIKE :pattern ESCAPE E'\\\\' OR COALESCE(content_excerpt,'') ILIKE :pattern ESCAPE E'\\\\') ORDER BY filed_at DESC LIMIT :limit",params)
    rows=sorted(uploaded+filings,key=lambda r:str(r.get("published_at") or ""),reverse=True)[:limit]
    return {"ok":True,"data":[{k:_json(v) for k,v in r.items()} for r in rows],"source":"uploaded_evidence + sec_filing_inventory","as_of":_json(max((r["observed_at"] for r in rows if r.get("observed_at")),default=None))}

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
    """Search fixed research FAQ questions and categories by literal keyword."""
    pattern=f"%{_like(keyword)}%"
    rows=await _query("SELECT question, answer, category FROM agent_faq WHERE question ILIKE :pattern ESCAPE E'\\\\' OR category ILIKE :pattern ESCAPE E'\\\\' ORDER BY id LIMIT 5",{"pattern":pattern})
    return {"ok":True,"data":rows,"source":"agent_faq","as_of":None}

TOOL_REGISTRY={item.name:item for item in [query_stock_prices,query_latest_forecast,query_materials,query_sec_filings,query_research_faq]}
def tool_schemas():
    return [{"type":"function","function":{"name":name,"description":item.description,"parameters":item.args_schema.model_json_schema()}} for name,item in TOOL_REGISTRY.items()]
