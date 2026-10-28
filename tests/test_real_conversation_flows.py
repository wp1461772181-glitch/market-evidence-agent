"""Real-conversation paths for the four 2026-10-28 changes (Levi: 「先让 claude 写测试用真实对话跑一遍」).

Each test is one or more whole turns through ``run_direct_turn`` with a scripted model (never a live
provider): messages are written through the chat store, cards are pressed the way the client presses them,
and what the model reads back is the tool reply the server put into its protocol.

A. 主 bot 召回研报 bot 的一通对话，再用 read_past_conversation 读它的长回复（记忆，非证据）。
B. 「每小时帮我找一条新闻」→ create_routine 确认卡 → 按下 → 任务挂在该 bot 上。
C. claim gate enforce：换说法的句子留着，无依据的数字被删。
(D, 工作区财报 → 资料库, is in frontend/scripts/material-focus.test.mjs.)
"""
import asyncio
import json

import pytest

from app import agent_bots as bots
from app import agent_tasks as tasks
from app.agent_chat_models import AgentBot, AgentConversation, AgentTask
from app.agent_chat_store import owner_hash
from app.agent_workflow import consent
from app.database import Base, SessionLocal, engine

OWNER = "owner"
OWNER_HASH = owner_hash(OWNER)
STRANGER = "stranger"
ANALYSIS = ("英伟达本周新闻分析：多家云厂商追加 Blackwell 订单，数据中心需求仍强；"
            "出口管制传闻对中国区收入的影响仍待确认。" + "补充背景。" * 40)


@pytest.fixture
def owner(monkeypatch, disposable_database):
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    with SessionLocal() as db:
        for model in (AgentTask, AgentBot, AgentConversation):  # each test starts with no bots or conversations
            db.query(model).delete()
        db.commit()
    from app.agent_workflow import direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return OWNER


def _context(credential=OWNER, **kwargs):
    from app.agent_workflow.state import AgentRuntimeContext
    kwargs.setdefault("resolver", lambda payload: {"relationship": "new", "canonical_question": payload["question"]})
    return AgentRuntimeContext("custom", "test", "dummy", identity_credential=credential,
                               owner_hash=owner_hash(credential), **kwargs)


def _route(*actions, category="D", **scope):
    return lambda _payload: {"category": category, "days_back": None, "requested_actions": list(actions), **scope}


def _call(name, arguments, call_id=None):
    return {"tool_calls": [{"id": call_id or name, "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


async def _quiet(_event):
    pass


def _turn(question, context, **kwargs):
    from app import agent_chat_store as store
    from app.agent_workflow import direct

    async def run():
        try:
            return await direct.run_direct_turn(question, context, emit=_quiet, **kwargs)
        finally:
            await store.engine.dispose()
    return asyncio.run(run())


def _store(conversation_id, turns):
    """Write user/assistant pairs through the chat store, as a real conversation does (its recall hooks run)."""
    from app import agent_chat_store as store

    async def run():
        try:
            for n, (question, answer) in enumerate(turns, 1):
                turn = f"44444444-4444-4444-8444-{n:012d}"
                await store.append_message(conversation_id, turn, "user", question)
                await store.append_message(conversation_id, turn, "assistant", answer)
        finally:
            await store.engine.dispose()
    asyncio.run(run())


def _tool_replies(messages):
    return {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m.get("role") == "tool"}


# --- A. read_past_conversation: 跨 bot / 跨会话 ---------------------------------------------------

def _research_bot_with_a_long_analysis():
    """研报 bot 的一通对话：长助手回复，没有层 3 摘要，只能靠提问索引找到。"""
    research = bots.create_bot(OWNER_HASH, {"name": "研报"})
    _store(research["conversation_id"], [("帮我分析一下英伟达这周的新闻", ANALYSIS)])
    return research


def test_main_bot_recalls_another_bots_conversation_and_reads_its_reply(owner):
    research = _research_bot_with_a_long_analysis()
    main = bots.ensure_default_bot(OWNER_HASH)
    seen = []

    def model(messages, tools):
        seen.append(messages)
        replies = _tool_replies(messages)
        if "recall" not in replies:
            return _call("recall_conversations", {"query": "英伟达 新闻"}, "recall")
        if "read" not in replies:
            # The model reads the id off the recall reply, as it would in a real turn.
            return _call("read_past_conversation", {"conversation_id": replies["recall"]["data"][0]["conversation_id"]}, "read")
        return {"content": "研报 bot 那次说：云厂商追加 Blackwell 订单，数据中心需求仍强；出口管制的影响还待确认。"}

    context = _context(conversation_id=str(main.conversation_id), bot_id=str(main.id), bot_is_default=True,
                       intent_recognizer=_route(), decider=model)
    result = _turn("上次研报那边分析的英伟达新闻说了什么？", context)

    replies = _tool_replies(seen[-1])
    recalled = replies["recall"]["data"]
    assert [row["conversation_id"] for row in recalled] == [research["conversation_id"]]
    read = replies["read"]
    assert read["ok"] and read["label"] == "往事，不是证据"
    [conversation] = read["data"]
    assert conversation["conversation_id"] == research["conversation_id"]
    assert "数据中心需求仍强" in conversation["messages"][0]["content"]
    assert all("ordinal" not in row for row in read["data"])
    # Memory, not evidence: nothing was numbered, the answer cites nothing and still came through whole.
    evidence = next(m["content"] for m in seen[-1] if str(m.get("content") or "").startswith("Accepted evidence DATA"))
    assert evidence.endswith("[]") and not result["citations"]
    assert result["status"] == "succeeded" and "数据中心需求仍强" in result["answer"]


def test_a_figure_read_from_a_past_conversation_is_not_stated_without_a_fresh_read(owner):
    research = bots.create_bot(OWNER_HASH, {"name": "研报"})
    _store(research["conversation_id"], [("英伟达这季收入多少", "英伟达这季收入 467 亿美元。")])
    main = bots.ensure_default_bot(OWNER_HASH)
    steps = iter([_call("read_past_conversation", {"conversation_id": research["conversation_id"]}),
                  {"content": "那次说英伟达这季收入 467 亿美元 [1]。"}])
    context = _context(conversation_id=str(main.conversation_id), bot_id=str(main.id), bot_is_default=True,
                       intent_recognizer=_route(), decider=lambda *_: next(steps))
    result = _turn("把研报那次说的英伟达收入告诉我", context)
    assert "467" not in result["answer"] and not result["citations"]


def test_another_owners_conversation_cannot_be_read(owner):
    research = _research_bot_with_a_long_analysis()
    seen = []
    steps = iter([_call("read_past_conversation", {"conversation_id": research["conversation_id"]}, "read"),
                  {"content": "读不到那次对话。"}])

    def model(messages, _tools):
        seen.append(messages)
        return next(steps)
    # Signed in as someone else: the owner-only tool is refused before any read.
    _turn("读一下那次英伟达的对话", _context(STRANGER, conversation_id="conv-stranger", intent_recognizer=_route(),
                                          decider=model))
    assert _tool_replies(seen[-1])["read"]["error_code"] == "owner_login_required"
    # The same owner check guards the read itself: another owner's hash finds nothing.
    from app import agent_recall as recall
    assert recall.read(owner_hash(STRANGER), research["conversation_id"]) is None


def test_read_history_citation_stays_in_this_conversation(owner):
    """读另一通对话的引用要靠 read_past_conversation；read_history_citation 只认本会话目录里的引用。"""
    research = _research_bot_with_a_long_analysis()
    main = bots.ensure_default_bot(OWNER_HASH)
    seen = []
    steps = iter([_call("read_history_citation", {"turn_id": "44444444-4444-4444-8444-000000000001", "ordinal": 1}, "cite"),
                  _call("read_past_conversation", {"conversation_id": research["conversation_id"]}, "read"),
                  {"content": "那次的分析说数据中心需求仍强。"}])

    def model(messages, _tools):
        seen.append(messages)
        return next(steps)
    context = _context(conversation_id=str(main.conversation_id), bot_id=str(main.id), bot_is_default=True,
                       intent_recognizer=_route(), decider=model, history_citation_catalogue=[])
    result = _turn("研报那次英伟达分析的来源 [1] 说了什么？", context)
    replies = _tool_replies(seen[-1])
    assert replies["cite"]["ok"] is False and replies["cite"]["error_code"] == "history_citation_not_found"
    assert replies["read"]["ok"] and "数据中心需求仍强" in replies["read"]["data"][0]["messages"][0]["content"]
    assert "数据中心需求仍强" in result["answer"]


# --- B. 「每小时帮我找一条新闻」→ 确认卡 → 按下 ------------------------------------------------------

def _press(card, context):
    return _turn(card["choices"][0]["value"], context, state={"pending_request": card, "requested_pending_id": card["id"]})


def test_hourly_news_from_chat_card_and_press_creates_one_task_on_finbot(owner):
    finbot = bots.create_bot(OWNER_HASH, {"name": "FinBot"})
    offered = []

    def model(messages, tools):
        names = {(t.get("function") or {}).get("name") for t in tools or ()}
        offered.append(names)
        if "create_routine" in names and not _tool_replies(messages):
            return _call("create_routine", {
                "name": "每小时新闻", "schedule_kind": "hourly", "schedule_time": "00:05",
                "instruction": "每小时找一条关于研究队列公司的最新新闻；有值得说的新消息就用 notify_telegram 发给我，没有就不发。"})
        return {"content": "我已经提出了一个每小时运行的例行任务，请在确认卡上确认。"}

    context = dict(conversation_id=finbot["conversation_id"], bot_id=finbot["id"], bot_name="FinBot")
    # The classifier sees the catalogue and asks for it, as the prompt's boundary example says.
    first = _turn("每小时帮我找一条新闻", _context(intent_recognizer=_route("create_routine"), decider=model, **context))
    assert "create_routine" in offered[0]
    card = first["pending_request"]
    assert first["status"] == "waiting_for_input" and card["reason"] == "write_confirmation"
    assert card["display"]["title"] == "新建例行任务 · 每小时新闻"
    assert card["display"]["targets"] == [{"kind": "每小时第 05 分"}]
    assert "notify_telegram" in card["display"]["alternative"]
    assert tasks.list_tasks(OWNER_HASH) == []  # nothing before the press

    pressed = _press(card, _context(decider=lambda *_: {"content": "已建好。"}, **context))
    assert pressed["status"] != "failed", pressed["answer"]
    [task] = tasks.list_tasks(OWNER_HASH)
    assert task["bot_id"] == finbot["id"] and task["name"] == "每小时新闻"
    assert (task["kind"], task["schedule_kind"], task["schedule_time"], task["scope"]) == ("brief", "hourly", "00:05", "research_queue")
    assert "找一条" in task["instruction"] and task["enabled"] and task["next_run_at"]
    assert tasks.list_tasks(OWNER_HASH, bot_id=finbot["id"]) == [task]

    # The same card pressed again runs nothing more.
    _press(card, _context(decider=lambda *_: {"content": "已建好。"}, **context))
    assert len(tasks.list_tasks(OWNER_HASH)) == 1


def test_a_cancelled_routine_card_creates_nothing(owner):
    finbot = bots.create_bot(OWNER_HASH, {"name": "FinBot"})
    steps = iter([_call("create_routine", {"name": "每小时新闻", "schedule_kind": "hourly", "instruction": "找一条新闻"}),
                  {"content": "请确认。"}])
    context = dict(conversation_id=finbot["conversation_id"], bot_id=finbot["id"])
    card = _turn("每小时帮我找一条新闻", _context(intent_recognizer=_route(), decider=lambda *_: next(steps), **context))["pending_request"]
    cancel = next(c["value"] for c in card["choices"] if c["value"].startswith("cancel:"))
    _turn(cancel, _context(decider=lambda *_: {"content": "已取消。"}, **context),
          state={"pending_request": card, "requested_pending_id": card["id"]})
    assert tasks.list_tasks(OWNER_HASH) == []


# --- C. claim gate enforce: 换说法留着，编造的数字删掉 ---------------------------------------------

def _claims(*items):
    return json.dumps({"claims": [{"id": f"c{n}", "statement": statement, "citation_ordinal": 1,
                                   "evidence": {"type": "quote", "evidence_quote": quote}}
                                  for n, (statement, quote) in enumerate(items, 1)]}, ensure_ascii=False)


def test_enforce_keeps_a_paraphrase_and_drops_only_the_unsupported_figure(owner, monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    monkeypatch.delenv("CLAIM_JEV_SUPPORT", raising=False)
    paraphrase = "AMD 最近一个交易日收在 100 美元附近 [1]。"
    invented = "AMD 市值约 5000 亿美元 [1]。"
    answer = (paraphrase + "\n" + invented + "\n"
              + _claims(("AMD 最近一个交易日收在 100 美元附近。", "AMD 当天的收盘价落在一百美元上下，与前一个交易日基本持平"),
                        ("AMD 市值约 5000 亿美元。", "AMD 市值约 5000 亿美元，位列全球半导体公司前列")))
    calls = []

    def model(messages, tools):
        calls.append(tools)
        if len(calls) == 1:
            return _call("query_stock_prices", {"symbol": "AMD"})
        return {"content": answer}

    executed = []
    context = _context(conversation_id="conv-amd", intent_recognizer=_route(category="A", symbol="AMD", metrics=["price"]),
                       decider=model,
                       executor=lambda name, args: executed.append(name) or {"ok": True, "data": [] if name == "search_knowledge" else [
                           {"id": "p1", "symbol": "AMD", "date": "2026-10-27", "close": 100}]})
    result = _turn("AMD 最近收盘价多少？", context)

    # Route A searches saved knowledge first (empty here), then the price the model asked for.
    assert executed[-1] == "query_stock_prices" and result["citations"]
    assert "100 美元附近" in result["answer"]          # the reworded sentence is delivered
    assert "5000" not in result["answer"]               # the figure no evidence holds is not
    assert "evidence_quote" not in result["answer"] and "待核对" not in result["answer"]
    gate = result["audit"]["claim_gate"]
    assert "quote_not_found" in gate["drop_reasons"]     # the paraphrase is recorded, not cut
    assert any(row["reason"] == "quote_not_found" for row in gate["warned_lines"])


def test_enforce_delivers_a_fully_paraphrased_answer_untouched(owner, monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    monkeypatch.delenv("CLAIM_JEV_SUPPORT", raising=False)
    prose = "AMD 上一个交易日的收盘价是 100 美元 [1]。\n这个价位和前几天相比没有大的变化。"
    answer = prose + "\n" + _claims(("AMD 上一个交易日的收盘价是 100 美元。", "AMD 收盘报一百美元整，成交量与上周大致相当"))
    steps = iter([_call("query_stock_prices", {"symbol": "AMD"}), {"content": answer}])
    context = _context(conversation_id="conv-amd2", intent_recognizer=_route(category="A", symbol="AMD", metrics=["price"]),
                       decider=lambda *_: next(steps),
                       executor=lambda name, _args: {"ok": True, "data": [] if name == "search_knowledge" else [
                           {"id": "p1", "symbol": "AMD", "date": "2026-10-27", "close": 100}]})
    result = _turn("AMD 最近收盘价多少？", context)
    assert result["status"] == "succeeded", (result["reason"], result["answer"])
    assert "收盘价是 100 美元" in result["answer"] and "未能与来源核对" not in result["answer"]
