"""Rebuild the knowledge index from the authoritative source tables.

The routine sync only advances five sources per 30-second tick and indexes a
single chunk per tick, so a deliberate rebuild of roughly 100 filings would take
days. This script drives the same code paths (``sync_source_batch`` and
``index_pending``) at full speed and reports what it did.

Requires ``RAG_ENABLED=true`` and the pinned BGE-M3 model files.
"""

from __future__ import annotations

import argparse
import time

from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.knowledge.indexing import index_pending
from app.knowledge.models import KnowledgeExtractionRun
from app.knowledge.runtime import get_tokenizer, start_runtime
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.source_sync import sync_source_batch

SOURCE_TYPES = ("faq", "sec", "upload", "analysis", "web")


def _reset_cursors() -> None:
    """Drop keyset cursors so every source row is reconsidered from the start."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(KnowledgeExtractionRun).where(
                KnowledgeExtractionRun.scope_key.like("material_sync.%")
            )
        ).all()
        for row in rows:
            session.delete(row)
        session.commit()
        print(f"cleared {len(rows)} material sync cursors")


def _sync_all(session_factory, tokenizer, generation: str, batch_size: int, source_types) -> None:
    for kind in source_types:
        scanned = accepted = chunks = 0
        while True:
            stats = sync_source_batch(
                session_factory, tokenizer, generation, source_type=kind, batch_size=batch_size
            )
            scanned += stats.get("scanned", 0)
            accepted += stats.get("accepted", 0)
            chunks += stats.get("chunks", 0)
            if not stats.get("scanned"):
                break
            print(
                f"  {kind}: scanned {scanned} accepted {accepted} chunks {chunks}",
                flush=True,
            )
        print(f"{kind}: scanned {scanned} accepted {accepted} chunks {chunks}", flush=True)


def _index_all(session_factory, runtime, generation: str, *, batch: int) -> None:
    total = 0
    started = time.monotonic()
    while True:
        counts = index_pending(
            session_factory,
            runtime.embedder,
            runtime.index,
            generation,
            max_tasks=batch,
            worker_id="rebuild",
        )
        total += counts.get("indexed", 0)
        if counts.get("failed"):
            print(f"  failures in this batch: {counts['failed']}", flush=True)
        if not counts.get("indexed"):
            print(f"indexer idle after {total} chunks", flush=True)
            return
        elapsed = time.monotonic() - started
        rate = total / elapsed if elapsed else 0
        print(f"indexed {total} chunks ({rate:.1f}/s)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=5, help="documents per sync page")
    parser.add_argument("--index-batch", type=int, default=25, help="chunks per index call")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--only", nargs="+", choices=SOURCE_TYPES, default=list(SOURCE_TYPES))
    args = parser.parse_args()

    settings = KnowledgeSettings.from_env(validate_model=True)
    if not settings.enabled:
        raise SystemExit("RAG_ENABLED must be true to rebuild the knowledge index")
    runtime = start_runtime(settings)
    if runtime is None:
        raise SystemExit("knowledge runtime did not start")
    tokenizer = get_tokenizer()
    print(f"generation {settings.generation}")

    if not args.skip_sync:
        _reset_cursors()
        _sync_all(SessionLocal, tokenizer, settings.generation, args.batch, tuple(args.only))

    with engine.connect() as connection:
        pending = connection.execute(
            text("select count(*) from knowledge_index_tasks where status = 'pending'")
        ).scalar()
        chunks = connection.execute(
            text("select count(*) from knowledge_chunks where generation = :g"),
            {"g": settings.generation},
        ).scalar()
    print(f"chunks for this generation: {chunks}; pending tasks: {pending}")

    if not args.skip_index:
        _index_all(SessionLocal, runtime, settings.generation, batch=args.index_batch)

    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
