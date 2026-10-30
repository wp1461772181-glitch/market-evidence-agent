"""Routine tasks belong to bots (personal agent plan, second edition, stage C).

* a task is a bot's: created on a bot (the default bot when none is named, and old tasks are adopted by it);
  another owner's or an unknown bot is refused; deleting a bot deletes its tasks and touches no other bot;
* a result is posted in the bot's line as a turn of its own (a head row that says it is a task's result,
  then the brief with its citations), so a follow-up there carries it; the inbox row is its unread mark;
* a run with nothing new posts nothing: an alerts run that found nothing, a brief run that answers
  「无新消息」, a run without cited evidence;
* a post waits while the owner's own turn in that line is unfinished, and goes in after it;
* a brief runs with its bot's settings (instruction with its notify rule, tool scope, model, memory layer)
  and a short note of the task's previous conclusion; the default bot's digest sees the other bots' posts;
* 待你确认 stays, labelled with the bot; the sidebar counts unread posts and pending suggestions per bot.
"""
import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import agent_bots as bots
from app import agent_context as layers
from app import agent_task_runner as runner
from app import agent_tasks as tasks
from app.agent_chat_models import (AgentBackgroundModel, AgentBot, AgentConversation, AgentInboxItem, AgentMessage,
                                   AgentModelEntry, AgentProfileItem, AgentResearchQueue, AgentTask, AgentTaskRun)
from app.agent_chat_store import owner_hash
from app.database import Base, SessionLocal, engine

OWNER = "o" * 40
OWNER_HASH = owner_hash(OWNER)
OTHER_HASH = owner_hash("x" * 40)
SHANGHAI = ZoneInfo("Asia/Shanghai")
KEY = "sk-test-background-0123456789"
H = {"X-Chat-Session": OWNER}


def at(*args):
    return datetime(*args, tzinfo=SHANGHAI).astimezone(UTC)


@pytest.fixture
def owner(monkeypatch, disposable_database, tmp_path):
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    from app.models import SecFilingInventory
    with SessionLocal() as db:
        db.query(SecFilingInventory).delete()
        for model in (AgentInboxItem, AgentTask, AgentTaskRun, AgentBackgroundModel, AgentResearchQueue, AgentBot,
                      AgentModelEntry, AgentProfileItem):
            db.query(model).delete()
        db.commit()
    monkeypatch.setenv("AGENT_MODELS_CONFIG", str(tmp_path / "models.yaml"))
    from app.agent_workflow import consent, direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    return OWNER


def run(coroutine):
    from app import agent_chat_store as store

    async def wrapped():
        try:
            return await coroutine
        finally:
            await store.engine.dispose()
    return asyncio.run(wrapped())


def _bot(name, **fields):
    return bots.create_bot(OWNER_HASH, {"name": name, **fields})


def _default():
    return bots.ensure_default_bot(OWNER_HASH)


def _line(bot_id) -> list[AgentMessage]:
    with SessionLocal() as db:
        bot = db.get(AgentBot, uuid.UUID(str(bot_id)))
        rows = db.query(AgentMessage).filter(AgentMessage.conversation_id == bot.conversation_id).order_by(
            AgentMessage.sequence).all()
        for row in rows:
            db.expunge(row)
        return rows


def _counts(bot_id) -> dict:
    listed = {b["id"]: b for b in bots.list_bots(OWNER_HASH)}[str(bot_id)]
    return {"unread": listed["unread"], "pending": listed["pending"]}


def _filing(symbol, accession, observed_at, form="10-Q"):
    from app.models import SecFilingInventory
    with SessionLocal() as db:
        db.add(SecFilingInventory(symbol=symbol, cik="0000320193", accession_number=accession, form=form,
                                  filed_at=date(2026, 10, 29), primary_document="a.htm",
                                  source_url=f"https://www.sec.gov/{accession}.htm", source="sec_edgar",
                                  review_status="pending_review", observed_at=observed_at, content_status="not_fetched"))
        db.commit()


def _alerts(bot_id, symbols=("AAPL",), **config):
    return tasks.create_task(OWNER_HASH, {"name": "新申报提醒", "kind": "alerts", "schedule_kind": "hourly",
                                          "schedule_time": "00:05", "timezone": "Asia/Shanghai", "scope": "symbols",
                                          "symbols": list(symbols), "config": {"news": False, **config},
                                          **({"bot_id": bot_id} if bot_id else {})},
                             now=at(2026, 10, 30, 7, 0))


def _brief(bot_id=None, **fields):
    values = {"name": "苹果日报", "kind": "brief", "schedule_kind": "weekdays", "schedule_time": "08:00",
              "timezone": "Asia/Shanghai", "instruction": "看看苹果过去一天的新申报和新闻。", "scope": "symbols",
              "symbols": ["AAPL"]}
    if bot_id:
        values["bot_id"] = bot_id
    values.update(fields)
    return tasks.create_task(OWNER_HASH, values, now=at(2026, 10, 30, 7, 0))


def _with_key():
    tasks.save_background_model(OWNER_HASH, provider="deepseek", model="deepseek-chat", base_url=None, api_key=KEY)


def _stub_turn(monkeypatch, results):
    calls = []

    async def fake(credential, question, model, *, title, bot=None, notes=None, token_budget=None):
        calls.append({"question": question, "model": model, "bot": bot, "notes": list(notes or []), "budget": token_budget})
        cid = str(uuid.uuid4())
        with SessionLocal() as db:
            db.add(AgentConversation(id=uuid.UUID(cid), owner_hash=OWNER_HASH,
                                     bot_id=uuid.UUID(bot["bot_id"]) if bot else None))
            db.commit()
        return {"conversation_id": cid, "actions": [], "citations": [], "audit": {}, "answer": "",
                "status": "complete", "reason": None, **results[len(calls) - 1]}
    monkeypatch.setattr(runner, "run_unattended_turn", fake)
    return calls


CITED = {"answer": "苹果交了新的 10-Q [1]，服务收入同比增长。",
         "citations": [{"ordinal": 1, "title": "AAPL 10-Q", "url": "https://sec.gov/aapl", "source_type": "official_filing",
                        "quote": "Services net sales increased"}],
         "audit": {"token_usage": 900}}


# --- a task is a bot's ------------------------------------------------------------------------------

def test_a_task_belongs_to_a_bot_and_old_tasks_are_adopted_by_the_default_bot(owner):
    apple = _bot("苹果", focus={"companies": ["AAPL"]})
    task = _brief(apple["id"])
    assert task["bot_id"] == apple["id"]
    assert [t["id"] for t in tasks.list_tasks(OWNER_HASH, apple["id"])] == [task["id"]]
    default = _default()
    # Without a bot the task is the default bot's.
    plain = _brief(name="研究队列简报")
    assert plain["bot_id"] == str(default.id)
    assert [t["id"] for t in tasks.list_tasks(OWNER_HASH, default.id)] == [plain["id"]]
    # Another owner's bot or an unknown one is refused; so is moving a task to one.
    with SessionLocal() as db:
        stranger = AgentBot(owner_hash=OTHER_HASH, name="别人的", focus={}, is_default=False)
        db.add(stranger)
        db.commit()
        stranger_id = str(stranger.id)
    for bad in (stranger_id, str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(tasks.TaskError, match="unknown_bot"):
            _brief(bad, name="越权")
        with pytest.raises(tasks.TaskError, match="unknown_bot"):
            tasks.update_task(OWNER_HASH, task["id"], {"bot_id": bad})
    # A task from before bots (bot_id NULL) is adopted by the default bot, and so is its inbox entry.
    with SessionLocal() as db:
        db.query(AgentTask).filter(AgentTask.id == uuid.UUID(plain["id"])).update({"bot_id": None})
        db.commit()
    tasks.add_item(OWNER_HASH, section="confirm", kind="action", title="旧建议", task_id=plain["id"])
    bots.ensure_default_bot(OWNER_HASH)
    assert tasks.list_tasks(OWNER_HASH, default.id)[0]["bot_id"] == str(default.id)
    assert tasks.list_inbox(OWNER_HASH, section="confirm")[0]["bot_id"] == str(default.id)


def test_deleting_a_bot_deletes_its_tasks_and_leaves_the_other_bot_alone(owner):
    apple, news = _bot("苹果"), _bot("抓新闻")
    apple_task, news_task = _brief(apple["id"]), _brief(news["id"], name="新闻日报")
    tasks.add_item(OWNER_HASH, section="confirm", kind="action", title="苹果的建议", bot_id=apple["id"])
    tasks.add_item(OWNER_HASH, section="confirm", kind="action", title="新闻的建议", bot_id=news["id"])
    tasks.add_item(OWNER_HASH, section="brief", kind="task_brief", title="苹果的简报", body="x [1]", bot_id=apple["id"])
    assert _counts(apple["id"]) == {"unread": 1, "pending": 1}
    assert bots.delete_bot(OWNER_HASH, apple["id"])
    assert [t["id"] for t in tasks.list_tasks(OWNER_HASH)] == [news_task["id"]]
    assert tasks.update_task(OWNER_HASH, apple_task["id"], {"enabled": False}) is None
    assert [i["title"] for i in tasks.list_inbox(OWNER_HASH, section="confirm")] == ["新闻的建议"]
    assert _counts(news["id"]) == {"unread": 0, "pending": 1}


# --- results go into the bot's line ----------------------------------------------------------------

def test_a_new_filing_alert_is_posted_in_the_bot_it_belongs_to_and_can_be_followed_up(owner):
    apple, news = _bot("苹果", focus={"companies": ["AAPL"]}), _bot("抓新闻")
    _default()
    now = at(2026, 10, 30, 8, 5)
    _filing("AAPL", "0000320193-26-000101", now - timedelta(hours=2))
    task = _alerts(apple["id"])
    assert run(runner.run_due(now=now, credential=OWNER)) == {task["id"]: "ok"}

    line = _line(apple["id"])
    assert [r.role for r in line] == ["user", "assistant"]
    head, post = line
    assert head.content.startswith(tasks.POST_HEAD) and "新申报提醒" in head.content and "不是所有者说的话" in head.content
    assert "AAPL" in post.content and "10-Q" in post.content and "[1]" in post.content
    marker = post.tool_calls["task_post"]
    assert marker["task_id"] == task["id"] and marker["kind"] == "filing"
    assert post.tool_calls["citations"][0]["url"] == "https://www.sec.gov/0000320193-26-000101.htm"
    # Only the bot it belongs to: the news bot's and the default bot's lines stay empty.
    assert _line(news["id"]) == [] and _line(_default().id) == []

    # It counts as unread on the Apple bot until its line is opened; 待你确认 is untouched.
    assert _counts(apple["id"]) == {"unread": 1, "pending": 0} and _counts(news["id"]) == {"unread": 0, "pending": 0}
    assert tasks.mark_bot_read(OWNER_HASH, apple["id"]) == 1
    assert _counts(apple["id"])["unread"] == 0

    # A follow-up in the line carries the post: it is a complete turn of the history, with its citation.
    from app.agent_chat_store import conversation_context
    settings = bots.turn_settings(OWNER_HASH, apple["id"])
    context = run(conversation_context(settings["conversation_id"], OWNER))
    turns = layers.complete_turns(context["l1_rows"])
    assert len(turns) == 1
    history = layers.project_turn_history(turns[0])
    assert history[0]["content"].startswith(tasks.POST_HEAD) and "10-Q" in history[1]["content"]
    assert layers.turn_catalogue_entry(turns[0])["sources"][0]["ordinal"] == 1

    # The next tick and the SEC monitor's hook see the same filing: nothing is posted twice.
    runner.run_alert_hooks("filings", now=now)
    assert run(runner.run_due(now=now + timedelta(hours=1), credential=OWNER)) == {task["id"]: "quiet"}
    assert len(_line(apple["id"])) == 2


def test_nothing_new_posts_nothing(owner, monkeypatch):
    _with_key()
    apple = _bot("苹果", focus={"companies": ["AAPL"]})
    alerts = _alerts(apple["id"])
    brief = _brief(apple["id"])
    calls = _stub_turn(monkeypatch, [{"answer": "无新消息。", "audit": {"token_usage": 300}},
                                     {"answer": "今天没找到带来源的新东西。"}])
    outcome = run(runner.run_due(now=at(2026, 10, 30, 8, 5), credential=OWNER))
    assert outcome == {alerts["id"]: "quiet", brief["id"]: "quiet"}
    assert len(calls) == 1 and runner.QUIET_ANSWER in calls[0]["question"]
    # Nothing in the line, nothing unread, no 简报 entry; the task says it ran quietly.
    assert _line(apple["id"]) == [] and _counts(apple["id"]) == {"unread": 0, "pending": 0}
    assert tasks.list_inbox(OWNER_HASH) == []
    listed = {t["id"]: t for t in tasks.list_tasks(OWNER_HASH, apple["id"])}
    assert listed[brief["id"]]["last_status_label"] == "已运行：没有新东西"
    assert listed[alerts["id"]]["last_status"] == "quiet"
    # A run without cited evidence posts nothing either.
    with SessionLocal() as db:
        db.query(AgentTask).filter(AgentTask.id == uuid.UUID(brief["id"])).update({"next_run_at": at(2026, 11, 2, 8, 0)})
        db.commit()
    assert run(runner.run_due(now=at(2026, 11, 2, 8, 0), credential=OWNER))[brief["id"]] == "no_evidence"
    assert _line(apple["id"]) == [] and tasks.list_inbox(OWNER_HASH) == []
    # Every run is logged for the bot page, newest first, posted or not.
    runs = tasks.list_runs(OWNER_HASH, apple["id"])
    assert [r["status"] for r in runs][:1] == ["no_evidence"] and all(r["posted"] == 0 for r in runs)
    assert {r["status"] for r in runs} == {"no_evidence", "quiet"}


def test_a_brief_runs_with_its_bots_settings_and_posts_its_conclusion_in_the_line(owner, monkeypatch):
    _with_key()
    apple = _bot("苹果", focus={"companies": ["AAPL"]}, tool_scope=["query_sec_filings", "query_financials"],
                 description="盯苹果。只有重要程度为高时才发 Telegram，其余只留在对话里。")
    from app.agent_profile import add_item as add_profile
    add_profile(OWNER_HASH, kind="about_me", text="苹果我主要看服务收入", quote="苹果我主要看服务收入",
                message="苹果我主要看服务收入", origin="explicit", bot_id=apple["id"])
    calls = _stub_turn(monkeypatch, [CITED])
    task = _brief(apple["id"])
    assert run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER)) == {task["id"]: "ok"}
    bot = calls[0]["bot"]
    assert bot["bot_id"] == apple["id"] and bot["tool_scope"] >= {"query_sec_filings", "query_financials"}
    assert "fetch_sec" not in bot["tool_scope"] and "web_research" not in bot["tool_scope"]
    assert "只有重要程度为高时才发 Telegram" in bot["block"]  # the notify rule lives in the bot's instruction
    assert "看看苹果过去一天" in calls[0]["question"] and calls[0]["model"]["api_key"] == KEY

    line = _line(apple["id"])
    assert [r.role for r in line] == ["user", "assistant"]
    assert "苹果日报" in line[0].content and line[1].content == CITED["answer"]
    assert line[1].tool_calls["citations"][0]["quote"] == "Services net sales increased"
    run_conversation = line[1].tool_calls["task_post"]["run_conversation_id"]
    settings = bots.turn_settings(OWNER_HASH, apple["id"])
    assert run_conversation and run_conversation != settings["conversation_id"]  # the run had a thread of its own
    with SessionLocal() as db:
        assert str(db.get(AgentConversation, uuid.UUID(run_conversation)).bot_id) == apple["id"]
    brief = tasks.list_inbox(OWNER_HASH, section="brief")[0]
    assert brief["bot_id"] == apple["id"] and brief["posted"] and brief["sources"][0]["ordinal"] == 1
    assert tasks.list_runs(OWNER_HASH, apple["id"])[0] == {**tasks.list_runs(OWNER_HASH, apple["id"])[0],
                                                           "status": "ok", "posted": 1, "tokens": 900}

    # The next run knows its previous conclusion (to tell what is new), as a note that is not evidence.
    calls = _stub_turn(monkeypatch, [{"answer": "无新消息"}])
    with SessionLocal() as db:
        db.query(AgentTask).filter(AgentTask.id == uuid.UUID(task["id"])).update({"next_run_at": at(2026, 11, 2, 8, 0)})
        db.commit()
    assert run(runner.run_due(now=at(2026, 11, 2, 8, 0), credential=OWNER)) == {task["id"]: "quiet"}
    assert any("上次贴进对话的结论" in note and "服务收入同比增长" in note for note in calls[0]["notes"])
    assert len(_line(apple["id"])) == 2


def test_the_unattended_turn_gets_the_bots_memory_layer_scope_and_the_note(owner, monkeypatch):
    """The real run_unattended_turn: the run thread is the bot's, the profile block has the bot's layer,
    the bot settings reach the workflow, and the notes ride in the system-written memory note."""
    apple = _bot("苹果", focus={"companies": ["AAPL"]}, tool_scope=["query_sec_filings"])
    from app.agent_profile import add_item as add_profile
    add_profile(OWNER_HASH, kind="about_me", text="苹果我主要看服务收入", quote="苹果我主要看服务收入",
                message="苹果我主要看服务收入", origin="explicit", bot_id=apple["id"])
    from app.agent_workflow import graph
    seen = {}

    async def stream_workflow(**kwargs):
        seen.update(kwargs)
        yield {"event": "delta", "text": "无新消息"}
        yield {"event": "done", "status": "complete", "citations": [], "final_protocol": {}, "audit": {}}
    monkeypatch.setattr(graph, "stream_workflow", stream_workflow)
    settings = bots.turn_settings(OWNER_HASH, apple["id"])
    result = run(runner.run_unattended_turn(OWNER, "看看苹果。", {"provider": "deepseek", "model": "m", "api_key": KEY},
                                            token_budget=1000, title="t", bot=settings, notes=["上次的结论：服务收入涨了"]))
    assert seen["bot"]["bot_id"] == apple["id"] and seen["unattended"] is True
    assert "苹果我主要看服务收入" in seen["profile_block"]
    note = seen["messages"][-1]
    assert layers.is_memory_message(note) and "上次的结论：服务收入涨了" in note["content"]
    assert seen["messages"][1] == {"role": "user", "content": "看看苹果。"}
    with SessionLocal() as db:
        assert str(db.get(AgentConversation, uuid.UUID(result["conversation_id"])).bot_id) == apple["id"]
    assert runner.is_quiet(result["answer"])


def test_a_bot_task_uses_the_bots_catalog_model_before_the_background_one(owner, monkeypatch):
    from app import agent_model_catalog as catalog
    catalog.save_entry("fast", display_name="Fast", provider="deepseek", model="deepseek-chat",
                       api_key="sk-catalog-0123456789abcd", create=True)
    apple = _bot("苹果", model="fast")
    calls = _stub_turn(monkeypatch, [{"answer": "无新消息"}])
    task = _brief(apple["id"])
    assert run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER)) == {task["id"]: "quiet"}
    assert calls[0]["model"]["api_key"] == "sk-catalog-0123456789abcd"
    # A bot whose model has no key and no background model left: not run, missing model.
    bots.update_bot(OWNER_HASH, apple["id"], {"model": None})
    catalog.delete_entry("fast")
    with SessionLocal() as db:
        db.query(AgentTask).filter(AgentTask.id == uuid.UUID(task["id"])).update({"next_run_at": at(2026, 11, 2, 8, 0)})
        db.commit()
    assert run(runner.run_due(now=at(2026, 11, 2, 8, 0), credential=OWNER)) == {task["id"]: "skipped_no_model"}


def test_the_default_bots_digest_sees_the_other_bots_posts_since_its_last_run(owner, monkeypatch):
    _with_key()
    apple, news = _bot("苹果"), _bot("抓新闻")
    default = _default()
    tasks.add_item(OWNER_HASH, section="brief", kind="task_brief", title="苹果日报", body="苹果交了 10-Q [1]。",
                   bot_id=apple["id"])
    tasks.add_item(OWNER_HASH, section="brief", kind="news", title="NVDA 新闻线索", body="英伟达发布新芯片 [1]（未核实）",
                   bot_id=news["id"])
    tasks.add_item(OWNER_HASH, section="brief", kind="task_brief", title="助理自己的", body="不该出现", bot_id=default.id)
    with SessionLocal() as db:  # posted in the evening before the digest runs
        db.query(AgentInboxItem).update({"created_at": at(2026, 10, 29, 20, 0)})
        db.commit()
    calls = _stub_turn(monkeypatch, [{"answer": "无新消息"}])
    digest = _brief(None, name="早间汇总", instruction="把各 bot 的新消息串成一条。", scope="research_queue", symbols=[])
    run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER))
    note = next(n for n in calls[0]["notes"] if n.startswith("各 bot"))
    assert "bot「苹果」苹果日报" in note and "bot「抓新闻」NVDA 新闻线索" in note and "不该出现" not in note
    assert "不是证据" in note
    assert digest["bot_id"] == str(default.id)
    # Another bot's task does not get the digest note.
    calls = _stub_turn(monkeypatch, [{"answer": "无新消息"}, {"answer": "无新消息"}])
    _brief(apple["id"], name="苹果周报")
    run(runner.run_due(now=at(2026, 10, 30, 9, 0) + timedelta(days=3), credential=OWNER))
    assert all(not n.startswith("各 bot") for call in calls if call["bot"]["bot_id"] == apple["id"] for n in call["notes"])


# --- a post never splits the owner's turn ------------------------------------------------------------

def test_a_post_waits_while_the_owners_turn_in_the_line_is_unfinished(owner):
    apple = _bot("苹果")
    settings = bots.turn_settings(OWNER_HASH, apple["id"])
    line = settings["conversation_id"]
    from app.agent_chat_store import append_message
    owner_turn = str(uuid.uuid4())
    run(append_message(line, owner_turn, "user", "苹果最新的毛利率是多少？"))
    now = datetime.now(UTC)
    _filing("AAPL", "0000320193-26-000202", now - timedelta(hours=1))
    task = _alerts(apple["id"])
    assert runner.run_alerts(uuid.UUID(task["id"]), now=now)["filings"] == 1
    assert [r.content for r in _line(apple["id"])] == ["苹果最新的毛利率是多少？"]  # held back
    assert _counts(apple["id"])["unread"] == 1
    assert tasks.flush_posts(OWNER_HASH, now=now) == 0  # still busy
    run(append_message(line, owner_turn, "assistant", "毛利率是 46% [1]。"))
    assert tasks.flush_posts(OWNER_HASH, apple["id"], now=now) == 1
    rows = _line(apple["id"])
    assert [r.role for r in rows] == ["user", "assistant", "user", "assistant"]
    assert rows[1].turn_id == rows[0].turn_id and rows[2].content.startswith(tasks.POST_HEAD)
    assert len(layers.complete_turns([{"role": r.role, "turn_id": r.turn_id, "status": r.status, "content": r.content,
                                       "tool_calls": r.tool_calls} for r in rows])) == 2
    assert tasks.flush_posts(OWNER_HASH, now=now) == 0  # posted once
    # A turn cut off long ago does not hold posts back for ever.
    run(append_message(line, str(uuid.uuid4()), "user", "这一轮断了"))
    _filing("AAPL", "0000320193-26-000203", now - timedelta(minutes=30))
    later = now + tasks.BUSY_FOR + timedelta(minutes=1)
    runner.run_alerts(uuid.UUID(task["id"]), now=later)
    assert _line(apple["id"])[-1].tool_calls["task_post"]["kind"] == "filing"


# --- 待你确认 and the sidebar ---------------------------------------------------------------------------

def test_a_suggestion_stays_in_confirm_labelled_with_its_bot_and_lights_its_row(owner, monkeypatch):
    _with_key()
    news = _bot("抓新闻")
    action = {"operation": {"name": "add_to_research_queue", "arguments": {"symbol": "AMD"}},
              "display": {"title": "这是 Agent 的建议，不是你的要求：加入研究队列"}, "question": "q"}
    _stub_turn(monkeypatch, [{"actions": [action]}])
    task = _brief(news["id"], name="新闻日报")
    run(runner.run_due(now=at(2026, 10, 30, 8, 0), credential=OWNER))
    confirm = tasks.list_inbox(OWNER_HASH, section="confirm")
    assert len(confirm) == 1 and confirm[0]["bot_id"] == news["id"] and confirm[0]["task_id"] == task["id"]
    assert _line(news["id"]) == []  # a suggestion is not posted in the line; its card is minted on demand
    assert _counts(news["id"]) == {"unread": 0, "pending": 1}
    assert tasks.mark_bot_read(OWNER_HASH, news["id"]) == 0 and _counts(news["id"])["pending"] == 1
    assert tasks.list_inbox(OWNER_HASH, section="confirm", bot_id=_default().id) == []


def test_task_inbox_and_read_api_take_the_bot(owner, client):
    apple = _bot("苹果")
    created = client.post("/agent-chat/tasks", headers=H, json={
        "name": "苹果日报", "kind": "brief", "schedule_kind": "daily", "schedule_time": "08:00",
        "timezone": "Asia/Shanghai", "instruction": "看看苹果。", "bot_id": apple["id"]})
    assert created.status_code == 200, created.text
    assert created.json()["task"]["bot_id"] == apple["id"]
    bad = client.post("/agent-chat/tasks", headers=H, json={
        "name": "x", "kind": "brief", "schedule_kind": "daily", "schedule_time": "08:00", "instruction": "x",
        "bot_id": str(uuid.uuid4())})
    assert bad.status_code == 422 and bad.json()["detail"] == "unknown_bot"
    assert len(client.get(f"/agent-chat/tasks?bot_id={apple['id']}", headers=H).json()["tasks"]) == 1
    assert client.get(f"/agent-chat/tasks?bot_id={_default().id}", headers=H).json()["tasks"] == []
    assert client.get(f"/agent-chat/tasks/runs?bot_id={apple['id']}", headers=H).json() == {"runs": []}
    tasks.add_item(OWNER_HASH, section="brief", kind="task_brief", title="t", body="b [1]", bot_id=apple["id"])
    listed = {b["id"]: b for b in client.get("/agent-chat/bots", headers=H).json()["bots"]}
    assert listed[apple["id"]]["unread"] == 1
    assert client.post(f"/agent-chat/bots/{apple['id']}/read", headers=H).json() == {"read": 1}
    assert client.post(f"/agent-chat/bots/{uuid.uuid4()}/read", headers=H).status_code == 404
    assert client.get(f"/agent-chat/inbox?bot_id={apple['id']}", headers=H).json()["items"][0]["status"] == "read"
    guest = {"X-Chat-Session": "g" * 40}
    for method, path in (("get", "/agent-chat/tasks/runs"), ("post", f"/agent-chat/bots/{apple['id']}/read")):
        assert getattr(client, method)(path, headers=guest).status_code in {401, 403}
