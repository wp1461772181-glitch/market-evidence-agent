"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
from fastapi import HTTPException
from app.agent_chat_store import MAX_RESEARCH_QUEUE
import asyncio
from app.agent_chat_store import clean_queue
import pytest

OWNER = "owner-credential-" + "q" * 40


OWNER_API = "owner-credential-" + "z" * 40   # another one: the database is shared by the tests


SOMEONE = "guest-credential-" + "g" * 40


def test_the_queue_is_cleaned_the_way_the_workspace_writes_tickers():
    assert clean_queue(["amd", "AAPL", "brk-b", "AMD", "not a ticker", "", 7, None]) == ["AMD", "AAPL", "BRK.B"]
    assert clean_queue("AMD") == [] and clean_queue(None) == []
    many = [f"A{chr(65 + n // 26)}{chr(65 + n % 26)}" for n in range(60)]
    assert len(clean_queue(many)) == MAX_RESEARCH_QUEUE


def test_legacy_watchlist_payload_is_ignored_without_error():
    from app.agent_chat_stream import StreamRequest
    value=StreamRequest(provider='deepseek',model='test',api_key='abcdefgh',message='新闻',watchlist=['MU','INVALID/OLD'])
    assert 'watchlist' not in value.model_dump()


def test_the_owners_queue_is_stored_apart_and_an_emptied_queue_differs_from_none(disposable_database):
    from app import agent_chat_store as store
    from app.agent_chat_models import AgentResearchQueue
    from app.database import Base, engine
    Base.metadata.create_all(engine, tables=[AgentResearchQueue.__table__])

    async def exercise():
        try:
            first = await store.get_research_queue(OWNER)
            saved = await store.put_research_queue(OWNER, ["amd", "AAPL", "junk!", "AMD"])
            again = await store.get_research_queue(OWNER)
            other = await store.get_research_queue(SOMEONE)
            await store.put_research_queue(OWNER, [])
            emptied = await store.get_research_queue(OWNER)
            return first, saved, again, other, emptied
        finally:
            await store.engine.dispose()

    first, saved, again, other, emptied = asyncio.run(exercise())
    assert first is None
    assert saved == again == ["AMD", "AAPL"]
    assert other is None                      # one owner's list is not another credential's
    assert emptied == []


def test_only_the_owner_reads_or_writes_the_queue(monkeypatch, disposable_database):
    from app import agent_chat_store as store
    from app.agent_chat_models import AgentResearchQueue
    from app.agent_chat_stream import (ResearchQueueBody, get_research_queue_endpoint, put_research_queue_endpoint)
    from app.database import Base, engine
    Base.metadata.create_all(engine, tables=[AgentResearchQueue.__table__])
    monkeypatch.setattr("app.agent_chat_identity.owner_credential_matches", lambda value: value == OWNER_API)

    async def exercise():
        try:
            for who in (None, SOMEONE):
                with pytest.raises(HTTPException) as caught:
                    await get_research_queue_endpoint(x_chat_session=who)
                assert caught.value.status_code == 403
                with pytest.raises(HTTPException):
                    await put_research_queue_endpoint(ResearchQueueBody(symbols=["AMD"]), x_chat_session=who)
            before = await get_research_queue_endpoint(x_chat_session=OWNER_API)
            put = await put_research_queue_endpoint(ResearchQueueBody(symbols=["amd", "MU"]), x_chat_session=OWNER_API)
            after = await get_research_queue_endpoint(x_chat_session=OWNER_API)
            return before, put, after
        finally:
            await store.engine.dispose()

    before, put, after = asyncio.run(exercise())
    assert before == {"symbols": None}
    assert put == after == {"symbols": ["AMD", "MU"]}
