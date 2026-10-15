"""The research queue: the owner's copy lives in the database, a guest's stays in the browser, and a
company that is not in the user's queue gets a card (add it, or only look this once).

The queue card is the user's own click, so it also grounds a company the planner knew and the
words did not carry.
"""
import asyncio

import pytest
from fastapi import HTTPException

from app.agent_chat_store import MAX_RESEARCH_QUEUE, clean_queue
from app.agent_workflow.graph import build_workflow
from test_agent_semantic_regressions import (_answer, _new, _no_variants, _plan, _purposes, _runtime, _task, _turn,
                                             _web_rows)

OWNER = "owner-credential-" + "q" * 40
OWNER_API = "owner-credential-" + "z" * 40   # another one: the database is shared by the tests
SOMEONE = "guest-credential-" + "g" * 40


# --- the list ------------------------------------------------------------------------------------------

def test_the_queue_is_cleaned_the_way_the_workspace_writes_tickers():
    assert clean_queue(["amd", "AAPL", "brk-b", "AMD", "not a ticker", "", 7, None]) == ["AMD", "AAPL", "BRK.B"]
    assert clean_queue("AMD") == [] and clean_queue(None) == []
    many = [f"A{chr(65 + n // 26)}{chr(65 + n % 26)}" for n in range(60)]
    assert len(clean_queue(many)) == MAX_RESEARCH_QUEUE


def test_the_stream_request_carries_the_queue_cleaned_or_not_at_all():
    from pydantic import ValidationError
    from app.agent_chat_stream import StreamRequest
    base = {"provider": "deepseek", "model": "m", "api_key": "sk-test-secret-key-0123456789", "message": "hi"}
    assert StreamRequest(**base).watchlist is None                      # an older client: no card is ever asked
    assert StreamRequest(**base, watchlist=["amd", "AMD", "x y"]).watchlist == ["AMD"]
    assert StreamRequest(**base, watchlist=[]).watchlist == []          # an emptied queue is a queue
    with pytest.raises(ValidationError):
        StreamRequest(**base, watchlist=["AAPL"] * 31)


# --- the owner's copy ----------------------------------------------------------------------------------

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


# --- the card ------------------------------------------------------------------------------------------

def _news_runtime(queue, calls, symbols=("MU",), query="美光 新闻 MU"):
    async def execute(name, args):
        calls.append((name, args))
        return _web_rows(symbols[0])

    planner = _plan(_task("news", "web_research", "web_research", list(symbols), query))
    return _runtime(resolver=_new, intent_recognizer=_purposes("web_research"), planner=planner,
                    query_expander=_no_variants, executor=execute, answerer=_answer("美光的新闻 [1]。"),
                    watchlist=queue)


def _card(final):
    pending = final.get("pending_request") or {}
    assert pending.get("reason") == "company_not_in_queue", pending
    return pending


def test_a_company_outside_the_queue_asks_add_or_only_this_once():
    calls = []
    runtime = _news_runtime(("AAPL", "AMD"), calls)
    final = _turn(build_workflow(), runtime, "帮我搜一下美光的新闻", "turn")
    pending = _card(final)
    assert calls == []                                                   # nothing was searched yet
    assert final["terminal_status"] == "waiting_for_input"
    assert [choice["value"] for choice in pending["choices"]] == ["queue_add:MU", "queue_view:MU"]
    assert "MU" in final["answer"] and "研究队列" in final["answer"]
    assert pending["question"] == "帮我搜一下美光的新闻"                   # the user's own words are what continues


@pytest.mark.parametrize("button", ["queue_view:MU", "queue_add:MU"])
def test_either_button_continues_the_question_and_is_not_asked_again(button):
    calls = []
    runtime = _news_runtime(("AAPL",), calls)
    graph = build_workflow()
    shown = _turn(graph, runtime, "帮我搜一下美光的新闻", "one")
    assert _card(shown)
    final = _turn(graph, runtime, button, "two", shown)
    assert calls and calls[0][0] == "web_research"
    assert final["terminal_status"] == "succeeded"
    assert final.get("queue_decided") == ["MU"]
    again = _turn(graph, runtime, "再搜一下美光的新闻", "three", final)  # the same conversation: no second card
    assert (again.get("pending_request") or {}).get("reason") != "company_not_in_queue"


def test_a_ticker_typed_instead_of_a_button_answers_the_card_too():
    calls = []
    runtime = _news_runtime(("AAPL",), calls)
    graph = build_workflow()
    shown = _turn(graph, runtime, "帮我搜一下美光的新闻", "one")
    assert _card(shown)
    final = _turn(graph, runtime, "MU", "two", shown)
    assert calls and final["terminal_status"] == "succeeded"


def test_a_company_in_the_queue_or_a_client_without_one_is_not_asked():
    for queue in (("AAPL", "MU"), None):
        calls = []
        final = _turn(build_workflow(), _news_runtime(queue, calls), "帮我搜一下美光的新闻", "turn")
        assert calls == [("web_research", {"query_or_url": "美光 新闻 MU"})], queue
        assert final["terminal_status"] == "succeeded"


def test_a_ticker_the_words_do_not_carry_is_confirmed_by_the_card_not_guessed():
    calls = []
    runtime = _news_runtime(("AAPL",), calls, symbols=("FBRX",), query="FBRX news")
    graph = build_workflow()
    shown = _turn(graph, runtime, "帮我搜一下 Foobar Robotics 的新闻", "one")
    pending = _card(shown)
    assert calls == [] and [c["value"] for c in pending["choices"]] == ["queue_add:FBRX", "queue_view:FBRX"]
    final = _turn(graph, runtime, "queue_view:FBRX", "two", shown)
    assert final["terminal_status"] == "succeeded" and final.get("queue_decided") == ["FBRX"]


def test_a_write_keeps_its_own_card_and_is_not_asked_about_the_queue():
    async def intent(_payload):
        return {"purposes": ["system_action"], "action": "fetch_sec"}

    runtime = _runtime(resolver=_new, intent_recognizer=intent,
                       planner=_plan(_task("act", "system_action", "system_action", ["AMD"], "fetch AMD filings")),
                       query_expander=_no_variants, answerer=_answer("unused"), watchlist=("AAPL",))
    final = _turn(build_workflow(), runtime, "抓取 AMD 的 SEC 原文", "turn")
    assert (final.get("pending_request") or {}).get("reason") != "company_not_in_queue"
