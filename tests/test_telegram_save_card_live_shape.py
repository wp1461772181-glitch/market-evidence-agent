"""「把第三条新闻存到资料库」 on Telegram: a real card with 同意/取消, never prose about a card that is not there.

Regression (2026-10-28, live after fb1de74): the owner asked the 助理 line to save the third news item.  The reply
said 「已提交保存请求，确认卡正在等你处理」 but carried no buttons and no 「网页」 note.  The bridge was fine: the
stream had no clarification frame because the server never minted a card.  ``propose`` had told the model
``awaiting_confirmation`` at call time; at the end of the batch ``confirm_proposals`` checked 第三条 against the
newest result set (not the news list the owner counted), refused with ``result_position_mismatch`` and dropped the
proposal, but the model's tool message still said ``awaiting_confirmation``.  The model then wrote its own prose about
the waiting card, and the 「操作未执行」 notice was suppressed because that prose mentioned 确认卡.
"""
import asyncio
import copy
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_chat_store import owner_hash
from app.agent_chat_models import AgentResearchQueue
from app.agent_workflow import consent, direct, tools
from app.agent_workflow.direct import run_direct_turn
from app.agent_workflow.models import AgentConsent, AgentWebSource
from app.agent_workflow.state import AgentRuntimeContext
from app.database import Base, engine
from app.knowledge.models import KnowledgeDocument
from scripts import run_telegram_bridge as bridge

NOW = datetime.now(UTC)
REEL = "https://www.instagram.com/reel/De4twkDBvHq"
CLAIM = ("已提交保存请求，确认卡正在等你处理：来源是 2026-10-24 彼得森汽车博物馆的 Instagram 帖子，已关联 TSLA。"
         "在这张卡上点同意后才会真正写入资料库。")


@pytest.fixture(autouse=True)
def official_entity_metadata(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities, "ticker_records", lambda: [{"ticker": "TSLA", "title": "TESLA, INC."}])


@pytest.fixture
def owned(monkeypatch, disposable_database):
    Base.metadata.create_all(engine, tables=[AgentConsent.__table__, AgentWebSource.__table__,
                                             KnowledgeDocument.__table__, AgentResearchQueue.__table__])
    for module in (direct, consent, tools):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == "owner")
    return "owner"


def _page(n, url=None, title=None):
    return {"url": url or f"https://example.org/tesla/{n}", "title": title or f"Tesla news {n}",
            "excerpt": "Tesla Model news.", "published_at": (NOW - timedelta(days=4)).isoformat(),
            "fetched_at": NOW.isoformat(), "body_provenance": "public_page_fetch"}


def _call(name, **args):
    return {"tool_calls": [{"id": uuid.uuid4().hex, "function": {"name": name, "arguments": json.dumps(args)}}]}


def _state():
    """The news list the owner read (the reel is its third item), then a later list from another question."""
    news = [_page(1), _page(2), _page(3, url=REEL, title="Instagram"), _page(4), _page(5)]
    later = [_page(11), _page(12), _page(13)]
    sets = [{"id": "news", "rows": news}, {"id": "later", "rows": later}]
    return {"result_sets": [{**s, "owner": owner_hash("owner"), "conversation": "c", "scope": {"company": "Tesla"},
                             "tool": "web_research"} for s in sets]}


def _run(steps, state=None, question="把第三条新闻存到资料库"):
    """One owner turn with the model's decisions injected; returns the result and what the model last saw."""
    steps = iter(steps)
    seen = []

    async def decide(messages, *_):
        seen.append(copy.deepcopy(messages))
        return next(steps, {"content": CLAIM})

    async def execute(name, args):
        return {"ok": True, "data": []}

    context = AgentRuntimeContext(
        "custom", "test", "dummy", owner_hash=owner_hash("owner"), conversation_id="c", identity_credential="owner",
        classifier=lambda q: {"category": "D", "requested_actions": ["ingest_web_source"], "company": "Tesla",
                              "symbol": "TSLA"},
        decider=decide, executor=execute, answerer=lambda *_: CLAIM, resolver=lambda _: {"relationship": "new"})
    result = asyncio.run(run_direct_turn(question, context, emit=lambda _: asyncio.sleep(0), state=state or _state()))
    return result, seen


def _telegram(result):
    """What the bridge sends for this turn: the stream the server writes for it (graph._direct_agent_turn order),
    parsed and turned into the message and its keyboard exactly as _bot_turn does."""
    stream = [("conversation", {"conversation_id": "c", "turn_id": "t"})]
    pending = result.get("pending_request")
    if pending:
        stream.append(("clarification", {"request_id": pending["id"], "reason": pending["reason"],
                                         "choices": pending["choices"], "display": pending.get("display"),
                                         "question": pending.get("question")}))
    stream += [("delta", {"text": result["answer"]}), ("done", {"conversation_id": "c", "status": "complete"})]
    raw = "".join(f"event: {k}\ndata: {json.dumps(v, ensure_ascii=False)}\n\n" for k, v in stream)
    sent = []

    async def ask_agent(*_args, **_kwargs):
        from app.agent_telegram import sse_events
        return bridge.format_turn(sse_events(raw))

    async def send_text(client, token, chat, text, inline_keyboard=None, *, markdown=True):
        sent.append((text, inline_keyboard))
        return 1

    class _Tg:
        def record_reply(self, *a):
            pass

    import unittest.mock as mock
    with mock.patch.object(bridge, "_ask_agent", ask_agent), mock.patch.object(bridge, "_send_text", send_text), \
            mock.patch.object(bridge, "_bot_token_for_send", lambda *a: "token"):
        asyncio.run(bridge._bot_turn(None, "owner", "o", 1, {}, _Tg(), "bot", "助理", "把第三条新闻存到资料库"))
    return sent


def test_the_third_news_item_the_owner_counted_gets_a_card_with_telegram_buttons(owned):
    """The live turn: the model names the reel (third in the news list), not the newest list's third row."""
    result, _ = _run([_call("ingest_web_source", url=REEL)])
    card = result["pending_request"]
    assert card and card["reason"] == "write_confirmation" and card["display"]["url"] == REEL
    (text, keyboard), = _telegram(result)
    assert keyboard and [b["text"] for b in keyboard[0]] == ["同意", "取消"]
    assert "网页" not in text and "确认卡" in text


def _stray_state():
    state = _state()
    state["result_sets"][1]["rows"].append(_page(99, url=STRAY))
    return state


STRAY = "https://example.org/tesla/stray"


def test_a_save_the_server_refuses_never_reaches_the_owner_as_a_waiting_card(owned):
    """A target that is third in no list: no card.  The model's tool message says so (not the stale
    awaiting_confirmation), and a reply that still claims a waiting card carries the 「操作未执行」 notice."""
    result, seen = _run([_call("ingest_web_source", url=STRAY)], state=_stray_state())
    assert result["pending_request"] is None
    tool_replies = [json.loads(m["content"]) for m in seen[-1] if m.get("role") == "tool"]
    assert [r.get("error_code") for r in tool_replies] == ["result_position_mismatch"]
    assert REEL in {t["url"] for t in tool_replies[0]["expected_targets"]}
    assert "awaiting_confirmation" not in json.dumps(seen[-1], ensure_ascii=False)
    assert "操作未执行" in result["answer"]
    (text, keyboard), = _telegram(result)
    assert keyboard is None


def test_after_a_refused_target_the_model_can_name_the_right_one_and_get_the_card(owned):
    result, _ = _run([_call("ingest_web_source", url=STRAY), _call("ingest_web_source", url=REEL)],
                     state=_stray_state())
    assert result["pending_request"]["display"]["url"] == REEL
    (text, keyboard), = _telegram(result)
    assert [b["text"] for b in keyboard[0]] == ["同意", "取消"] and "操作未执行" not in text
