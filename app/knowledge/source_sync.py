"""Bounded keyset sync from the authoritative MaterialLibrary tables."""
from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, select

from .models import KnowledgeDocument, KnowledgeExtractionRun, KnowledgeUploadOwner
from .repository import ingest_documents
from .sources import adapt_faq, adapt_sec, adapt_uploaded
from .analysis_sources import load_analysis_document

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None


def sync_source_batch(session_factory, tokenizer, generation: str, *, source_type: str, batch_size: int = 5) -> dict:
    """Process one durable keyset page; unmapped uploads are never selected."""
    from ..agent_chat_models import AgentFAQ
    from ..models import SecFilingInventory, UploadedEvidence
    from ..material_analysis_models import MaterialAnalysisVersion

    if source_type not in {"sec", "faq", "upload", "analysis", "web"}:
        raise ValueError("unsupported source type")
    batch_size = max(1, min(10, int(batch_size)))
    scope_key = f"material_sync.{source_type}"
    with session_factory() as session:
        state = session.scalar(select(KnowledgeExtractionRun).where(KnowledgeExtractionRun.scope_key == scope_key).with_for_update())
        if state is None:
            state = KnowledgeExtractionRun(scope_key=scope_key, batch_status="running", processed_count=0)
            session.add(state); session.flush()
        cursor = state.cursor
        if source_type == "web":
            from ..agent_workflow.models import AgentWebSource
            from ..agent_workflow.web_sources import as_document
            last=UUID(cursor) if cursor else UUID(int=0)
            from .models import KnowledgeChunk
            chunked=select(KnowledgeChunk.id).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id).where(
                KnowledgeDocument.source_table=='agent_web_sources',KnowledgeDocument.source_id==__import__('sqlalchemy').cast(AgentWebSource.id,__import__('sqlalchemy').String),KnowledgeChunk.generation==generation).correlate(AgentWebSource).exists()
            rows=session.scalars(select(AgentWebSource).where(~chunked).order_by(AgentWebSource.fetched_at).limit(batch_size)).all()
            docs=[as_document(row) for row in rows]
            next_cursor=str(rows[-1].id) if rows else None
        elif source_type == "faq":
            last = int(cursor or 0)
            rows = session.scalars(select(AgentFAQ).where(AgentFAQ.id > last).order_by(AgentFAQ.id).limit(batch_size)).all()
            docs = [adapt_faq({"id": r.id, "question": r.question, "answer": r.answer, "category": r.category}) for r in rows]
            next_cursor = str(rows[-1].id) if rows else None
        elif source_type == "sec":
            last = UUID(cursor) if cursor else UUID(int=0)
            already_bootstrapped = session.scalar(select(exists().where(and_(
                KnowledgeDocument.source_table == "sec_filing_inventory",
                KnowledgeDocument.source_id == "9af2ef08-e7e6-4e2d-bf47-68d1a163957c"))))
            if cursor is None and not already_bootstrapped:
                # Controlled first bootstrap includes the accepted AMD quality target first.
                target = session.get(SecFilingInventory, UUID("9af2ef08-e7e6-4e2d-bf47-68d1a163957c"))
                rows = [target] if target and target.content_status == "fetched" and target.content_excerpt else []
            else:
                rows = []
            if not rows:
                rows = session.scalars(select(SecFilingInventory).where(
                    SecFilingInventory.id > last, SecFilingInventory.content_status == "fetched",
                    SecFilingInventory.content_excerpt.is_not(None)).order_by(SecFilingInventory.id).limit(batch_size)).all()
            docs = [doc for r in rows if (doc := adapt_sec({c.key: getattr(r, c.key) for c in r.__table__.columns}))]
            next_cursor = str(rows[-1].id) if rows else None
        elif source_type == "analysis":
            last = UUID(cursor) if cursor else UUID(int=0)
            rows = session.scalars(__import__("sqlalchemy").text("""
              SELECT v.id FROM material_analysis_versions v
              WHERE v.id > CAST(:cursor AS uuid) AND NOT EXISTS (
                SELECT 1 FROM material_analysis_versions n WHERE n.source_type=v.source_type AND n.source_id=v.source_id
                  AND (n.created_at,n.version_no,n.id)>(v.created_at,v.version_no,v.id))
              ORDER BY v.id LIMIT :limit
            """), {"cursor": str(last), "limit": batch_size}).all()
            rows = [session.get(MaterialAnalysisVersion, value) for value in rows]
            docs = [doc for r in rows if (doc := load_analysis_document(session, r.id))]
            next_cursor = str(rows[-1].id) if rows else None
        else:
            last = UUID(cursor) if cursor else UUID(int=0)
            rows = session.execute(select(UploadedEvidence, KnowledgeUploadOwner.owner_hash)
                .join(KnowledgeUploadOwner, KnowledgeUploadOwner.upload_id == UploadedEvidence.id)
                .where(UploadedEvidence.id > last).order_by(UploadedEvidence.id).limit(batch_size)).all()
            docs = [doc for row, owner in rows if (doc := adapt_uploaded({
                "id": row.id, "symbol": row.symbol, "title": row.title, "source_url": row.source_url,
                "published_at": row.published_at, "observed_at": row.observed_at,
                "content_sha256": row.content_sha256, "content_text": row.content_text,
            }, owner_hash=owner, allowlist=set()))]
            next_cursor = str(rows[-1][0].id) if rows else None
        counts = ingest_documents(session, docs, tokenizer, generation)
        if next_cursor:
            state.cursor = next_cursor; state.processed_count += len(rows); state.batch_status = "running"
        else:
            # A fresh keyset cycle permits future inserts without rescanning an initial prefix forever.
            state.cursor = None; state.processed_count = 0; state.batch_status = "idle"
        state.completed_at = datetime.now(UTC)
        session.commit()
        return {"source_type": source_type, "scanned": len(rows), "accepted": len(docs), "cursor": next_cursor, **counts}


def sync_sources_once(session_factory, runtime, tokenizer) -> None:
    """Advance each source cursor, then optionally index one leased task."""
    settings = runtime.settings
    for kind in ("faq", "sec", "upload", "analysis", "web"):
        sync_source_batch(session_factory, tokenizer, settings.generation, source_type=kind, batch_size=5)
    if os.getenv("RAG_INDEXING_ENABLED", "false").strip().lower() in {"1", "true", "yes"}:
        from .indexing import index_pending
        runtime.executor.run_background_sync(lambda: index_pending(
            session_factory, runtime.embedder, runtime.index,
            settings.generation, max_tasks=1, worker_id="material-sync"))


def start_source_sync(session_factory, interval_seconds: float = 30.0) -> None:
    global _thread
    if _thread and _thread.is_alive(): return
    _stop.clear()

    def run():
        while not _stop.is_set():
            try:
                from .runtime import get_runtime
                runtime = get_runtime()
                if runtime is None:
                    _stop.wait(max(5.0, interval_seconds))
                    continue
                from .runtime import get_tokenizer
                tokenizer = get_tokenizer()
                sync_sources_once(session_factory, runtime, tokenizer)
            except Exception as exc:
                log.warning("material source sync batch failed (%s)", type(exc).__name__)
            _stop.wait(max(5.0, interval_seconds))
    _thread = threading.Thread(target=run, name="material-library-sync", daemon=True)
    _thread.start()


def stop_source_sync() -> None:
    _stop.set()
