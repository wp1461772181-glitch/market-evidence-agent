"""Acceptance probe for the full-text filing index.

Checks the three properties this refactor exists for:

1. every stored filing keeps its complete text, not only the frozen preview;
2. chunking reaches past the old 80,000-character boundary;
3. natural questions about the financial statements retrieve the right text.

Run with ``RAG_ENABLED=true`` after ``scripts/rebuild_knowledge_index.py``.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.database import engine
from app.knowledge.retrieval import retrieve_materials
from app.knowledge.runtime import start_runtime
from app.knowledge.settings import KnowledgeSettings

PREVIEW_CHARACTERS = 80_000
DEFAULT_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")
QUESTIONS = {
    "balance_sheet": "consolidated balance sheet total assets and total liabilities",
    "segment_revenue": "segment revenue by reportable segment for the quarter",
    "cash_flow": "net cash provided by operating activities",
    "risk": "principal risks that could affect our business",
}


def _coverage() -> list[tuple]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "select symbol, count(*), count(content_text), "
                    "max(content_length), "
                    "count(*) filter (where content_length > :preview) "
                    "from sec_filing_inventory group by 1 order by 1"
                ),
                {"preview": PREVIEW_CHARACTERS},
            )
        )


def _chunk_depth(generation: str) -> list[tuple]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "select d.symbol, count(*), max(c.end_offset), "
                    "count(*) filter (where c.end_offset > :preview) "
                    "from knowledge_chunks c join knowledge_documents d on d.id = c.document_id "
                    "where c.generation = :generation and c.status = 'ready' "
                    "and c.source_type = 'official_filing' "
                    "group by 1 order by 1"
                ),
                {"generation": generation, "preview": PREVIEW_CHARACTERS},
            )
        )


def _junk(generation: str) -> tuple[int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "select count(*) filter (where c.answer like '%us-gaap:%' or c.answer like '%ix:nonfraction%'), "
                "count(*) from knowledge_chunks c "
                "where c.generation = :generation and c.source_type = 'official_filing'"
            ),
            {"generation": generation},
        ).one()
        return int(row[0]), int(row[1])


async def _probe(symbol: str, generation: str) -> list[tuple[str, str]]:
    results = []
    for label, question in QUESTIONS.items():
        payload = await retrieve_materials(symbol, question, limit=4)
        rows = payload.get("data") or []
        top = rows[0] if rows else {}
        excerpt = (top.get("excerpt") or "").replace("\n", " ")[:120]
        results.append((f"{symbol}/{label}", excerpt or "(no hit)"))
    return results


async def _main(only: tuple[str, ...]) -> int:
    settings = KnowledgeSettings.from_env(validate_model=True)
    if not settings.enabled:
        raise SystemExit("RAG_ENABLED must be true")
    runtime = start_runtime(settings)
    if runtime is None:
        raise SystemExit("knowledge runtime did not start")
    try:
        print("stored filings (symbol, rows, with full text, longest, over preview):")
        for row in _coverage():
            print("  ", row)
        print("ready filing chunks (symbol, chunks, deepest offset, past preview):")
        for row in _chunk_depth(settings.generation):
            print("  ", row)
        polluted, total = _junk(settings.generation)
        print(f"chunks containing inline-XBRL labels: {polluted} of {total}")
        print("retrieval probes:")
        for symbol in only:
            for label, excerpt in await _probe(symbol, settings.generation):
                print(f"   {label}: {excerpt}")
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(tuple(args.symbols))))
