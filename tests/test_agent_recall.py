"""Past conversations (personal agent plan §2.2): an owner-only index of summaries, titles and the owner's
questions, read by recall_conversations.  What it returns is memory, not evidence: it is never numbered,
and a figure an answer takes from it still needs this turn's read."""
import asyncio
import json

import pytest

from app import agent_recall as recall
from app.agent_chat_models import (AgentConversation, AgentConversationRecall, AgentConversationSummary,
                                   AgentConversationTitle, AgentMessage)
from app.agent_chat_store import owner_hash
from app.database import Base, SessionLocal, engine

OWNER = "owner"
OWNER_HASH = owner_hash(OWNER)
STRANGER = "s" * 64


@pytest.fixture
def owner(monkeypatch, disposable_database):
    Base.metadata.create_all(engine, tables=[AgentConversation.__table__, AgentMessage.__table__,
                                             AgentConversationTitle.__table__, AgentConversationSummary.__table__,
                                             AgentConversationRecall.__table__])
    with SessionLocal() as db:
        db.query(AgentConversationRecall).delete()
        db.query(AgentConversation).delete()
        db.commit()
    from app.agent_workflow import consent, direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    return OWNER


def _conversation(credential, turns, *, summary=None, title=None):
    """A stored conversation, written through the chat store so its index hooks run."""
    from app import agent_chat_store as store

    async def run():
        try:
            cid = await store.create_conversation(credential)
            for n, (question, answer) in enumerate(turns, 1):
                turn = f"33333333-3333-4333-8333-{n:012d}"
                await store.append_message(cid, turn, "user", question)
                await store.append_message(cid, turn, "assistant", answer)
            if summary:
                assert await store.append_summary(cid, 1, 2 * len(turns), summary)
            if title:
                assert await store.set_conversation_title(credential, cid, title)
            return cid
        finally:
            await store.engine.dispose()
    return asyncio.run(run())


def _context(**kwargs):
    from app.agent_workflow.state import AgentRuntimeContext
    credential = kwargs.pop("credential", OWNER)
    return AgentRuntimeContext("custom", "test", "dummy", identity_credential=credential,
                               owner_hash=owner_hash(credential), conversation_id="conv-b", **kwargs)


def _call(name, arguments, call_id="c1"):
    return {"tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def _route(_payload):
    return {"category": "D", "days_back": None, "requested_actions": []}


async def _quiet(_event):
    pass


def _turn(question, context):
    from app.agent_workflow import direct
    return asyncio.run(direct.run_direct_turn(question, context, emit=_quiet))


# --- the index ----------------------------------------------------------------------------------

def test_conversation_a_is_found_from_conversation_b(owner):
    a = _conversation(OWNER, [("英伟达最新一季的数据中心收入怎么样？", "数据中心收入增长 [1]。")],
                      summary="用户问英伟达 NVDA 数据中心收入，读了最新 10-Q。")
    other = _conversation(OWNER, [("苹果的回购计划有多大？", "回购 [1]。")])
    found = recall.search(OWNER_HASH, "上次我们说了英伟达什么", limit=5)
    assert [row["conversation_id"] for row in found] == [a]
    row = found[0]
    assert "英伟达" in row["summary"] and row["date"] and row["link"] == f"#agent/{a}"
    assert other not in {r["conversation_id"] for r in found}


def test_a_short_conversation_without_a_summary_is_found_by_the_owners_question(owner):
    a = _conversation(OWNER, [("英伟达和 AMD 的 GPU 份额对比", "……")])
    assert [r["conversation_id"] for r in recall.search(OWNER_HASH, "英伟达", limit=5)] == [a]


def test_a_title_names_the_conversation_and_is_searchable(owner):
    a = _conversation(OWNER, [("看看这家公司", "……")], title="台积电产能跟踪")
    [row] = recall.search(OWNER_HASH, "台积电", limit=5)
    assert row["conversation_id"] == a and row["title"] == "台积电产能跟踪"


def test_recall_is_owner_filtered(owner):
    _conversation(STRANGER, [("英伟达的毛利率", "……")], summary="陌生人问了英伟达毛利率。")
    assert recall.search(OWNER_HASH, "英伟达", limit=5) == []
    assert recall.recent(OWNER_HASH) == []


def test_recent_lists_one_line_per_conversation_newest_first(owner):
    first = _conversation(OWNER, [("英伟达", "……")], summary="第一段摘要")
    second = _conversation(OWNER, [("苹果", "……")])
    listed = recall.recent(OWNER_HASH)
    assert [r["conversation_id"] for r in listed] == [second, first]
    assert listed[1]["summary"] == "第一段摘要"


def test_backfill_indexes_conversations_stored_before_the_index(owner):
    a = _conversation(OWNER, [("英伟达的库存周转", "……")], summary="用户问英伟达库存周转。")
    with SessionLocal() as db:
        db.query(AgentConversationRecall).delete()
        db.commit()
    assert recall.search(OWNER_HASH, "英伟达", limit=5) == []
    recall.backfill()
    recall.backfill()  # idempotent
    assert [r["conversation_id"] for r in recall.search(OWNER_HASH, "英伟达", limit=5)] == [a]


# --- the tool -----------------------------------------------------------------------------------

def test_recall_tool_is_an_owner_only_read():
    from app.agent_tool_catalog import definitions, validate
    policy = definitions()["recall_conversations"]
    assert policy.level == "read" and not policy.guest
    with pytest.raises(Exception):
        validate("recall_conversations", {"query": "英伟达", "limit": 6})


def test_recall_rows_are_memory_not_evidence_and_never_numbered(owner):
    a = _conversation(OWNER, [("英伟达数据中心收入", "……")], summary="用户问英伟达数据中心收入，读了 10-Q。")
    steps = iter([_call("recall_conversations", {"query": "英伟达", "limit": 3}), {"content": "上次在另一个对话里聊过英伟达的数据中心收入。"}])
    seen = []
    def decider(messages, _schemas):
        seen.append(messages)
        return next(steps)
    result = _turn("上次我们说了英伟达什么？", _context(intent_recognizer=_route, decider=decider))
    tool_reply = next(m for m in seen[-1] if m.get("role") == "tool")
    payload = json.loads(tool_reply["content"])
    assert payload["label"] == "往事，不是证据" and payload["data"][0]["conversation_id"] == a
    assert all("ordinal" not in row and "citation" not in row for row in payload["data"])
    evidence = next(m["content"] for m in seen[-1] if str(m.get("content") or "").startswith("Accepted evidence DATA"))
    assert evidence.endswith("[]")
    assert not result.get("citations")
    assert "上次在另一个对话里" in result["answer"]


def test_a_figure_from_recall_still_needs_a_fresh_read(owner):
    _conversation(OWNER, [("英伟达营收", "……")], summary="用户问英伟达营收，回答说 FY2025 营收 1305 亿美元。")
    steps = iter([_call("recall_conversations", {"query": "英伟达营收"}), {"content": "英伟达 FY2025 营收 1305 亿美元。"}])
    result = _turn("上次说的英伟达营收是多少？", _context(intent_recognizer=_route, decider=lambda *_: next(steps)))
    assert "1305" not in result["answer"]


def test_a_guest_cannot_recall(owner):
    _conversation(OWNER, [("英伟达", "……")], summary="用户问英伟达。")
    steps = iter([_call("recall_conversations", {"query": "英伟达"}), {"content": "无法查看。"}])
    seen = []
    def decider(messages, _schemas):
        seen.append(messages)
        return next(steps)
    _turn("上次我们说了英伟达什么？", _context(credential=STRANGER, intent_recognizer=_route, decider=decider))
    replies = [json.loads(m["content"]) for m in seen[-1] if m.get("role") == "tool"]
    assert replies and replies[0]["ok"] is False and replies[0]["error_code"] == "owner_login_required"


def test_recall_api_is_owner_only(owner, client):
    a = _conversation(OWNER, [("英伟达", "……")], summary="用户问英伟达。")
    assert client.get("/agent-chat/recall", headers={"X-Chat-Session": STRANGER}).status_code == 403
    listed = client.get("/agent-chat/recall", headers={"X-Chat-Session": OWNER}).json()["items"]
    assert [r["conversation_id"] for r in listed] == [a]
    found = client.get("/agent-chat/recall?q=英伟达", headers={"X-Chat-Session": OWNER}).json()["items"]
    assert [r["conversation_id"] for r in found] == [a]


# --- reading a found conversation (Levi via TutorBot 2026-10-28: 有目录、无取书) -------------------

ANALYSIS = "英伟达新闻分析：数据中心需求仍强，管理层上调了指引。" + "细节。" * 30


def test_read_returns_the_newest_replies_oldest_first_and_only_the_owners(owner):
    a = _conversation(OWNER, [("英伟达有什么新闻？", ANALYSIS), ("还有呢？", "第二条回复。")])
    value = recall.read(OWNER_HASH, a, limit=2)
    assert [m["content"] for m in value["messages"]] == [ANALYSIS, "第二条回复。"]
    assert {m["role"] for m in value["messages"]} == {"assistant"} and value["link"] == f"#agent/{a}"
    both = recall.read(OWNER_HASH, a, limit=4, include_user=True)
    assert [m["role"] for m in both["messages"]] == ["user", "assistant", "user", "assistant"]
    assert recall.read(STRANGER, a) is None and recall.read(OWNER_HASH, "not-an-id") is None


def test_read_past_conversation_is_an_owner_only_read():
    from app.agent_tool_catalog import OWNER_ONLY, definitions, validate
    assert definitions()["read_past_conversation"].level == "read" and "read_past_conversation" in OWNER_ONLY
    with pytest.raises(Exception):
        validate("read_past_conversation", {"conversation_id": "x", "limit": 3})
    with pytest.raises(Exception):
        validate("read_past_conversation", {"conversation_id": "33333333-3333-4333-8333-000000000001", "limit": 11})


def test_a_read_conversation_is_memory_and_never_numbered(owner):
    a = _conversation(OWNER, [("英伟达有什么新闻？", ANALYSIS)], summary="用户问英伟达新闻，回复了一篇分析。")
    steps = iter([_call("read_past_conversation", {"conversation_id": a}), {"content": "那次的分析说数据中心需求仍强。"}])
    seen = []
    def decider(messages, _schemas):
        seen.append(messages)
        return next(steps)
    result = _turn("把上次英伟达那篇新闻分析读给我", _context(intent_recognizer=_route, decider=decider))
    payload = json.loads(next(m for m in seen[-1] if m.get("role") == "tool")["content"])
    assert payload["label"] == "往事，不是证据" and payload["data"][0]["messages"][0]["content"] == ANALYSIS
    evidence = next(m["content"] for m in seen[-1] if str(m.get("content") or "").startswith("Accepted evidence DATA"))
    assert evidence.endswith("[]") and not result.get("citations")
    assert "数据中心需求仍强" in result["answer"]


def test_a_guest_cannot_read_a_past_conversation(owner):
    a = _conversation(OWNER, [("英伟达", ANALYSIS)])
    steps = iter([_call("read_past_conversation", {"conversation_id": a}), {"content": "无法查看。"}])
    seen = []
    def decider(messages, _schemas):
        seen.append(messages)
        return next(steps)
    _turn("读一下那次对话", _context(credential=STRANGER, intent_recognizer=_route, decider=decider))
    replies = [json.loads(m["content"]) for m in seen[-1] if m.get("role") == "tool"]
    assert replies and replies[0]["ok"] is False and replies[0]["error_code"] == "owner_login_required"
