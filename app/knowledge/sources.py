from __future__ import annotations
import hashlib
import json
from datetime import date
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

def _utc(value: datetime | None) -> datetime | None:
    if value is None: return None
    if value.tzinfo is None: return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

@dataclass(frozen=True)
class SourceDocument:
    source_table: str
    source_id: str
    source_version: str
    source_hash: str
    source_type: str
    symbol: str | None
    scope: str
    visibility: str
    owner_hash: str | None
    title: str
    source_url: str | None
    published_at: datetime | None
    observed_at: datetime | None
    content_observed_at: datetime | None
    truncated: bool
    parser: str
    text: str
    category: str | None = None
    questions: str | None = None
    @property
    def available_at(self) -> datetime | None:
        values=[_utc(x) for x in (self.published_at,self.observed_at,self.content_observed_at) if x is not None]
        return max(values) if values else None

@dataclass(frozen=True)
class ChunkDraft:
    id: str
    ordinal: int
    category: str
    questions: str
    answer: str
    embedding_text: str
    content_hash: str
    start_offset: int
    end_offset: int
    needs_review: bool = False
    content_type: str = "text"
    section_path: str = ""

def _hash(text: str) -> str: return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _snapshot_hash(**fields) -> str:
    value={key:(item.isoformat() if isinstance(item,(datetime,date)) else item) for key,item in fields.items()}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def adapt_uploaded(row: Any, *, owner_hash: str | None, allowlist: set[str]) -> SourceDocument | None:
    source_id=str(row['id'])
    if owner_hash is not None and (len(owner_hash)!=64 or any(c not in '0123456789abcdef' for c in owner_hash)): return None
    if owner_hash is None and source_id not in allowlist: return None
    text=str(row.get('content_text') or '')
    if not text.strip(): return None
    scope='private' if owner_hash else 'public'
    source_hash=_snapshot_hash(text=text,title=str(row['title']),url=row.get('source_url'),symbol=str(row['symbol']),raw_content_sha256=row.get('content_sha256'),
      published_at=_utc(row.get('published_at')),observed_at=_utc(row.get('observed_at')),scope=scope,owner_hash=owner_hash)
    return SourceDocument('uploaded_evidence',source_id,source_hash,source_hash,'uploaded_media',str(row['symbol']),scope,scope,owner_hash,
      str(row['title']),row.get('source_url'),_utc(row.get('published_at')),_utc(row.get('observed_at')),_utc(row.get('observed_at')),False,'plain_text',text)

def adapt_sec(row: Any) -> SourceDocument | None:
    if row.get('content_status') not in (None,'fetched'): return None
    text=str(row.get('content_excerpt') or '')
    if not text.strip() or not row.get('content_excerpt_sha256'): return None
    if _hash(text)!=str(row['content_excerpt_sha256']):return None
    url=row.get('content_source_url') or row.get('source_url')
    name=row.get('content_document_name') or row.get('primary_document') or 'SEC filing excerpt'
    source_hash=_snapshot_hash(text=text,title=str(name),url=url,symbol=str(row['symbol']),filed_at=row.get('filed_at'),
      content_observed_at=_utc(row.get('content_observed_at')),observed_at=_utc(row.get('observed_at')),truncated=bool(row.get('content_truncated')),
      content_status=row.get('content_status'),
      kind=str(row.get('content_kind') or 'sec_excerpt'))
    filed=row.get('filed_at')
    published=datetime.combine(filed,datetime.min.time(),tzinfo=UTC) if filed and not isinstance(filed,datetime) else _utc(filed)
    return SourceDocument('sec_filing_inventory',str(row['id']),source_hash,str(row['content_excerpt_sha256']),'official_filing',str(row['symbol']),'public','public',None,
      str(name),url,published,_utc(row.get('observed_at')),_utc(row.get('content_observed_at')),bool(row.get('content_truncated')),
      str(row.get('content_kind') or 'sec_excerpt'),text,category=str(row.get('form') or 'SEC filing'))

def adapt_faq(row: Any) -> SourceDocument:
    category=str(row['category']); question=str(row['question']); answer=str(row['answer'])
    source_hash=_hash('\n'.join((category,question,answer))); source_id=str(row['id'])
    return SourceDocument('agent_faq',source_id,source_hash,source_hash,'research_faq',None,'public','public',None,
      question,None,None,None,None,False,'faq',answer,category,question)
