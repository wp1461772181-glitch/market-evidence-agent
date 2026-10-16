"""Bounded web-news snapshots with durable provenance and private ownership."""
from __future__ import annotations
import hashlib
from datetime import UTC,datetime
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from sqlalchemy import select,or_,text
from ..agent_web_research import validate_public_url,_get_public,extract_page,MAX_PAGE_BYTES,is_challenge_page,is_boilerplate_page
from ..knowledge.models import KnowledgeDocument
from ..knowledge.repository import document_integrity
from ..knowledge.sources import SourceDocument,_snapshot_hash
from .models import AgentWebSource


def canonical_url(url):
    parts=urlsplit(url)
    validate_public_url(urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,'')))
    return urlunsplit(('https',parts.netloc.lower(),parts.path or '/',urlencode(sorted(parse_qsl(parts.query,keep_blank_values=True))),''))


def timestamp(value):
    if not value:return None
    if isinstance(value,datetime):return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:return None


def as_document(row):
    fingerprint=_snapshot_hash(text=row.excerpt,url=row.source_url,title=row.title,published_at=row.published_at,
        fetched_at=row.fetched_at,owner_hash=row.owner_hash,symbol=row.symbol,source_type='web_news')
    return SourceDocument('agent_web_sources',str(row.id),fingerprint,fingerprint,'web_news',row.symbol,
        'private','private',row.owner_hash,row.title,row.source_url,row.published_at,row.fetched_at,row.fetched_at,
        True,row.fetch_method,row.excerpt)


def save_snapshot(db,identity,page,*,symbol=None,company=None):
    url=canonical_url(page['url']);body=str(page.get('excerpt') or '')[:5000].strip()
    if not body or is_boilerplate_page(body):raise ValueError('web_body_unavailable')
    content_hash=hashlib.sha256(body.encode()).hexdigest();url_hash=hashlib.sha256(url.encode()).hexdigest()
    # Serializing per owner also covers simultaneous different URLs with identical bodies.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'web-ingest:'+identity})
    row=db.scalar(select(AgentWebSource).where(AgentWebSource.owner_hash==identity,or_(AgentWebSource.url_hash==url_hash,AgentWebSource.content_hash==content_hash)))
    exists=row is not None
    if row is None:
        published=timestamp(page.get('published_at'));fetched=timestamp(page.get('fetched_at')) or datetime.now(UTC)
        if published and published>fetched:published=None
        row=AgentWebSource(owner_hash=identity,symbol=symbol,company=company,source_url=url,url_hash=url_hash,
            content_hash=content_hash,title=str(page.get('title') or url)[:512],excerpt=body,
            published_at=published,fetched_at=fetched,fetch_method=str(page.get('body_provenance') or 'public_page_fetch')[:64],source_type='web_news')
        db.add(row);db.flush()
    source=as_document(row)
    doc=db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.source_table=='agent_web_sources',KnowledgeDocument.source_id==str(row.id),KnowledgeDocument.source_hash==source.source_hash))
    if doc is None:
        doc=KnowledgeDocument(source_table=source.source_table,source_id=source.source_id,source_version=source.source_version,
            source_hash=source.source_hash,source_type='web_news',symbol=source.symbol,scope=source.scope,visibility=source.visibility,
            owner_hash=identity,title=source.title,source_url=source.source_url,published_at=source.published_at,
            observed_at=source.observed_at,content_observed_at=source.content_observed_at,available_at=source.available_at,
            raw_length=len(source.text),truncated=True,parser=source.parser,source_text=source.text,active=True,status='pending')
        db.add(doc);db.flush();doc.snapshot_integrity=document_integrity(doc)
    return {'ok':True,'status':'already_exists' if exists else 'created','source_id':str(row.id),'document_id':str(doc.id),
        'source_type':'web_news','source_url':row.source_url,'published_at':row.published_at.isoformat() if row.published_at else None,
        'fetched_at':row.fetched_at.isoformat(),'content_hash':row.content_hash,'index_status':doc.status}


async def capture(url,*,approved_publication=None):
    final,content_type,raw=await _get_public(canonical_url(url),limit=MAX_PAGE_BYTES)
    if 'html' not in content_type:raise ValueError('unsupported_content_type')
    html=raw.decode('utf-8','replace')
    if is_challenge_page(html):raise ValueError('challenge_page')
    page=extract_page(html,final,datetime.now(UTC));page['body_provenance']='public_page_fetch'
    page['published_at']=page.get('published_at') or approved_publication
    return page


def freeze_snapshot(page):
    """Freeze exactly the bounded source the user reviewed, before minting consent."""
    import json
    snapshot={key:page.get(key) for key in ('url','title','excerpt','published_at','fetched_at','body_provenance','truncated')}
    snapshot['url']=canonical_url(snapshot['url'])
    snapshot['excerpt']=str(snapshot.get('excerpt') or '')[:5000].strip()
    if not snapshot['excerpt'] or not timestamp(snapshot.get('fetched_at')):raise ValueError('web_snapshot_unavailable')
    snapshot['content_hash']=hashlib.sha256(snapshot['excerpt'].encode()).hexdigest()
    return json.loads(json.dumps(snapshot,default=str,ensure_ascii=False))
