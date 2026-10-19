"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import asyncio
import pytest



@pytest.mark.parametrize("code, fragment", [
    ("provider_timeout", "超时"), ("provider_rate_or_quota_error", "额度"), ("provider_invalid_key", "API key"),
    ("provider_network_error", "连接"), ("anything_else", "没有完成"),
])
def test_a_failure_is_told_in_words_without_an_internal_code(code, fragment):
    from fastapi import HTTPException
    from app.agent_chat_stream import _failure_notice
    text = _failure_notice(HTTPException(502, code))
    assert fragment in text and code not in text


def test_the_provider_timeouts_are_longer_and_bounded(monkeypatch):
    import importlib
    import app.agent_chat_provider as provider
    assert provider.PROVIDER_REQUEST_SECONDS >= 30 and provider.PROVIDER_STREAM_SECONDS >= 60
    monkeypatch.setenv("AGENT_PROVIDER_TIMEOUT_SECONDS", "9999")
    monkeypatch.setenv("AGENT_PROVIDER_STREAM_SECONDS", "1")
    reloaded = importlib.reload(provider)
    try:
        assert reloaded.PROVIDER_REQUEST_SECONDS == 90 and reloaded.PROVIDER_STREAM_SECONDS == 30
    finally:
        monkeypatch.undo()
        importlib.reload(provider)


def test_the_system_prompt_says_the_model_cannot_save_anything():
    from app.agent_chat_stream import SYSTEM
    assert "cannot save, import, record or modify anything yourself" in SYSTEM


YAHOO_ERROR = ("Oops, something went wrong\n\n[Skip to navigation](#navigation-container)  [Skip to right column](#right-rail)"
               "\n\n# Yahoo Finance\n\n[Sign in](https://login.yahoo.com/x)\n" + "[Mail](https://mail.yahoo.com) " * 20)


ARTICLE = ("# Apple Q3 earnings beat estimates\n\nApple reported revenue of $94.0 billion for its June quarter, up 5% "
           "year over year, as iPhone sales rose in China. ") * 8


@pytest.mark.parametrize("text, expected", [
    (YAHOO_ERROR, True), ("Access denied. " + "[menu](https://x.test/a) " * 40, True),
    ("[a](https://x.test/1) " * 60, True),
    (ARTICLE, False), ("Something went wrong at the supplier, executives said. " + ARTICLE, False), ("", False),
])
def test_boilerplate_pages_are_told_from_articles(text, expected):
    from app.agent_web_research import is_boilerplate_page
    assert is_boilerplate_page(text) is expected


def test_an_error_page_is_not_listed_and_the_next_candidate_is_read(monkeypatch):
    from test_agent_web_research import _fake_web, _row, _article_html, APPLE_HIT
    good1, bad, good2, good3 = ("https://example.com/a", "https://finance.example.com/err",
                                "https://example.com/b", "https://example.com/c")
    web = _fake_web(
        monkeypatch,
        [_row(good1, "Apple results A"), _row(bad, "Apple Q3 earnings beat", raw=YAHOO_ERROR),
         _row(good2, "Apple results B"), _row(good3, "Apple results C")],
        {good1: _article_html("Apple results A", APPLE_HIT), bad: _article_html("Err", "Oops"),
         good2: _article_html("Apple results B", APPLE_HIT), good3: _article_html("Apple results C", APPLE_HIT)})
    result = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news",max_results=3))
    urls = [row["url"] for row in result["data"]]
    assert bad not in urls and urls == [good1, good2, good3]
    assert all(row.get("excerpt") and not row.get("fetch_error") for row in result["data"])


def test_an_error_page_that_is_the_only_hit_is_reported_as_unavailable_not_as_a_source(monkeypatch):
    from test_agent_web_research import _fake_web, _row
    bad = "https://finance.example.com/err"
    web = _fake_web(monkeypatch, [_row(bad, "Apple Q3 earnings beat", raw=YAHOO_ERROR)],
                    {bad: "<html><body><p>Apple.</p></body></html>"})
    result = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news",max_results=3))
    row = result["data"][0]
    assert row["fetch_error"] == "boilerplate_page" and row["excerpt"] == ""


def test_rename_validates_and_is_owner_scoped(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import agent_chat_identity as identity
    seen = []

    async def fake(credential, conversation_id, title):
        seen.append((credential, conversation_id, title))
        return conversation_id.endswith("1")

    monkeypatch.setattr(identity, "set_conversation_title", fake)
    app = FastAPI()
    app.include_router(identity.router)
    headers = {"X-Chat-Session": "o" * 48}
    cid = "00000000-0000-4000-8000-000000000001"
    with TestClient(app) as client:
        missing = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "x"})
        named = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "  苹果   营收  复盘 "}, headers=headers)
        cleared = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "   "}, headers=headers)
        foreign = client.patch("/agent-chat/conversations/00000000-0000-4000-8000-000000000002", json={"title": "x"}, headers=headers)
        control = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "a\x00b"}, headers=headers)
        long = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "字" * 81}, headers=headers)
    assert missing.status_code == 401
    assert named.status_code == 200 and named.json()["title"] == "苹果 营收 复盘"
    assert cleared.status_code == 200 and cleared.json()["title"] is None
    assert foreign.status_code == 404
    assert control.status_code == 422 and long.status_code == 422
    assert seen == [("o" * 48, cid, "苹果 营收 复盘"), ("o" * 48, cid, None),
                    ("o" * 48, "00000000-0000-4000-8000-000000000002", "x")]


def test_a_custom_name_replaces_the_first_question_in_the_list(disposable_database):
    from app import agent_chat_store as store
    from app.agent_chat_models import AgentConversation, AgentConversationTitle, AgentMessage
    from app.database import Base, engine
    Base.metadata.create_all(engine, tables=[AgentConversation.__table__, AgentMessage.__table__,
                                              AgentConversationTitle.__table__])

    async def exercise():
        mine, theirs = "owner-credential-" + "a" * 40, "other-credential-" + "b" * 40
        cid = await store.create_conversation(mine)
        await store.append_message(cid, "11111111-1111-4111-8111-111111111111", "user", "这是我问的第一个很长的问题" * 8)
        before = (await store.list_conversations(mine))[0]
        assert await store.set_conversation_title(theirs, cid, "偷改") is False        # not theirs
        assert await store.set_conversation_title(mine, cid, "苹果营收复盘") is True
        after = (await store.list_conversations(mine))[0]
        assert await store.set_conversation_title(mine, cid, None) is True
        restored = (await store.list_conversations(mine))[0]
        return before, after, restored

    before, after, restored = asyncio.run(exercise())
    assert before["custom_title"] is False and before["title"].startswith("这是我问的第一个")
    assert after["title"] == "苹果营收复盘" and after["custom_title"] is True
    assert restored["title"] == before["title"] and restored["custom_title"] is False


def test_a_guest_may_name_their_own_conversation_in_public_mode(monkeypatch):
    """The access middleware treats a PATCH as an owner-only write; naming a conversation is not one
    (the handler only reaches conversations of the credential sent)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import agent_chat_identity as identity
    from app.agent_chat_middleware import create_agent_chat_access_middleware

    async def fake(_credential, _conversation_id, _title):
        return True

    monkeypatch.setattr(identity, "set_conversation_title", fake)
    app = FastAPI()
    app.include_router(identity.router)
    app.middleware("http")(create_agent_chat_access_middleware(True, "operator-key"))
    cid = "00000000-0000-4000-8000-000000000001"
    with TestClient(app) as client:
        named = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "我的复盘"},
                             headers={"X-Chat-Session": "g" * 48})
        other = client.patch("/agent-chat/something-else", json={"title": "x"}, headers={"X-Chat-Session": "g" * 48})
    assert named.status_code == 200 and named.json()["title"] == "我的复盘"
    assert other.status_code in {401, 503}        # any other write is still the owner's


def _stream_once(message, **extra):
    import app.agent_chat_stream as stream
    from starlette.requests import Request

    async def run():
        payload = stream.StreamRequest(provider="deepseek", model="deepseek-chat", api_key="abcdefgh",
                                       message=message, conversation_id=None, **extra)
        request = Request({"type": "http", "method": "POST", "path": "/agent-chat/stream", "headers": []})
        response = await stream.stream_chat(payload, request, "x" * 40)
        return "".join([part async for part in response.body_iterator])

    return asyncio.run(run())


def test_a_card_press_is_stored_as_the_button_label_and_a_failed_turn_keeps_its_words(stream_fixture):
    card = "11111111-1111-4111-8111-111111111111"
    _, _, _, _, stored, _, _ = stream_fixture
    wire = _stream_once(f"confirm:{card}", pending_request_id=card, display_message="同意")
    users = [args[3] for args, _ in stored if len(args) > 3 and args[2] == "user"]
    assert users == ["同意"]                                       # the history shows the button, not the token
    assistants = [(args[3], kwargs.get("status")) for args, kwargs in stored if len(args) > 3 and args[2] == "assistant"]
    # A press on a card that is no longer waiting is answered, not failed (2026-10-19): the user is
    # told the card expired.  Either way the bubble is never blank.
    assert assistants and assistants[-1][0].strip() and "失效" in assistants[-1][0]
    assert "event: delta" in wire and "event: done" in wire
    assert "pending_request_mismatch" not in assistants[-1][0]


def test_a_label_is_only_taken_for_a_press_on_a_card(stream_fixture):
    _, _, _, _, stored, _, _ = stream_fixture
    _stream_once("show AAPL prices", display_message="同意")
    users = [args[3] for args, _ in stored if len(args) > 3 and args[2] == "user"]
    assert users == ["show AAPL prices"]


from test_agent_chat_stream import stream_fixture
