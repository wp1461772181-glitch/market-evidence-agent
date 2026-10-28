"""Skills: recipes, not code (personal agent plan §2.5; second edition, stage E).

Acceptance of the original phase 5, plus the stage E row:
* a skill that names a tool outside the catalog (or asks to run code) is not saved;
* a write a skill allows still makes a confirmation card (and needs the owner's words for it);
* with 20 skills enabled, the per-turn listing stays under 1 KB; the steps come only with use_skill;
* the tools offered = this turn's ∩ the bot's scope ∩ the skill's; 「/名字」 starts a skill in a bot's line;
  a bot names its usual skills and a task runs with its skill;
* a draft from 「把刚才这个流程存成技能」 is inert until the owner saves it; import shows the tools first.
"""
import asyncio
import json
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app import agent_bots as bots
from app import agent_skills as skills
from app import agent_tasks as tasks
from app import agent_task_runner as runner
from app.agent_chat_models import AgentBot, AgentSkill, AgentTask
from app.agent_chat_store import owner_hash
from app.database import Base, SessionLocal, engine

OWNER = "o" * 40
OWNER_HASH = owner_hash(OWNER)
H = {"X-Chat-Session": OWNER}
KEY = "sk-test-background-0123456789"


@pytest.fixture
def owner(monkeypatch, disposable_database):
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    skills.ensure_columns()
    with SessionLocal() as db:
        for model in (AgentSkill, AgentTask, AgentBot):
            db.query(model).delete()
        db.commit()
    skills.seed_builtins()
    from app.agent_workflow import consent, direct, tools
    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    return OWNER


def _skill(name="读现金流", tools=("query_financials",), **fields):
    return skills.create_skill(OWNER_HASH, {"name": name, "description": "读经营现金流和自由现金流。",
                                            "body": "1. 用 query_financials 读经营现金流。\n2. 标来源。",
                                            "allowed_tools": list(tools), **fields})


def _turn(*, bot_id=None, decider=None, recognizer=None, question="q", active_skill=None, unattended=False,
          executor=None):
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    settings = bots.turn_settings(OWNER_HASH, bot_id) if bot_id else {}
    context = AgentRuntimeContext("custom", "m", "k", owner_hash=OWNER_HASH, identity_credential=OWNER,
                                  conversation_id=settings.get("conversation_id", str(uuid.uuid4())),
                                  bot_id=settings.get("bot_id"), tool_scope=settings.get("tool_scope"),
                                  skill_block=skills.catalogue_block(OWNER_HASH), active_skill=active_skill,
                                  decider=decider, intent_recognizer=recognizer, executor=executor,
                                  unattended=unattended)

    async def quiet(_event):
        pass
    return direct, direct.Turn(question, context, quiet, [], {}), context, quiet


def _names(turn):
    return {t["function"]["name"] for t in turn.exposed_tools()}


# --- saving --------------------------------------------------------------------------------------------

def test_the_three_builtins_are_seeded_once_and_versioned_on_change(owner):
    listed = {s["name"]: s for s in skills.list_skills(OWNER_HASH)}
    assert {"财报速读", "同行对比", "新闻摘要"} <= set(listed)
    assert all(listed[n]["builtin"] and listed[n]["origin"] == "builtin" for n in ("财报速读", "同行对比", "新闻摘要"))
    assert skills.seed_builtins() == 0
    with SessionLocal() as db:
        db.query(AgentSkill).filter(AgentSkill.name == "同行对比").update({"body": "旧的步骤"})
        db.commit()
    assert skills.seed_builtins() == 1
    assert {s["name"]: s for s in skills.list_skills(OWNER_HASH)}["同行对比"]["version"] == 2
    # A built-in is not edited in place: it is copied, and the copy is the owner's.
    builtin = listed["财报速读"]
    assert skills.update_skill(OWNER_HASH, builtin["id"], {"body": "改"}) is None
    copy = skills.copy_skill(OWNER_HASH, builtin["id"])
    assert copy["origin"] == "mine" and copy["name"] == "财报速读（我的）" and not copy["builtin"]


def test_a_skill_with_an_unknown_or_overreaching_tool_or_code_is_not_saved(owner):
    for tools_, code in ((["query_financials", "rm_rf"], "unknown_tool"), (["use_skill"], "unknown_tool"),
                         (["draft_skill"], "unknown_tool"), ([], "invalid_allowed_tools")):
        with pytest.raises(skills.SkillError, match=code):
            _skill(tools=tools_)
    with pytest.raises(skills.SkillError, match="code_not_allowed"):
        _skill(body="照着跑：\n```python\nimport os\n```")
    assert [s for s in skills.list_skills(OWNER_HASH) if not s["builtin"]] == []
    saved = _skill()
    with pytest.raises(skills.SkillError, match="unknown_tool"):
        skills.update_skill(OWNER_HASH, saved["id"], {"allowed_tools": ["query_financials", "shell"]})
    with pytest.raises(skills.SkillError, match="name_taken"):
        _skill()


# --- the prompt ---------------------------------------------------------------------------------------

def test_twenty_enabled_skills_take_under_1_kb_and_the_steps_come_only_with_use_skill(owner):
    for index in range(17):
        _skill(name=f"很长名字的技能第{index}号", description="这是一个描述写得非常非常长的技能，用来测试列表会不会超过一千字节的上限" * 2,
               body=f"秘密步骤 {index}：先读这个再读那个。")
    enabled = [s for s in skills.list_skills(OWNER_HASH) if s["enabled"]]
    assert len(enabled) == 20
    block = skills.catalogue_block(OWNER_HASH)
    assert len(block.encode()) < 1024
    assert all(s["name"] in block for s in enabled)
    direct, turn, context, quiet = _turn()
    prompt = json.dumps(turn.tool_messages(), ensure_ascii=False)
    assert "很长名字的技能第3号" in prompt and "秘密步骤 3" not in prompt
    outcome = asyncio.run(turn.use_skill({"name": "很长名字的技能第3号"}))
    assert outcome["ok"] and "秘密步骤 3" in outcome["data"][0]["steps"]
    assert "秘密步骤 3" in json.dumps(turn.tool_messages(), ensure_ascii=False)
    # A disabled skill is neither listed nor usable.
    third = next(s for s in enabled if s["name"] == "很长名字的技能第4号")
    skills.set_enabled(OWNER_HASH, third["id"], False)
    assert "很长名字的技能第4号" not in skills.catalogue_block(OWNER_HASH)
    assert asyncio.run(turn.use_skill({"name": "很长名字的技能第4号"}))["error_code"] == "skill_not_found"


def test_tools_are_this_turns_and_the_bots_and_the_skills(owner):
    bot = bots.create_bot(OWNER_HASH, {"name": "读财报", "tool_scope": ["query_financials", "query_sec_filings", "web_research"]})
    skill = _skill(tools=["query_financials", "query_stock_prices"])
    direct, turn, context, quiet = _turn(bot_id=bot["id"])
    before = _names(turn)
    assert {"query_financials", "query_sec_filings", "web_research"} <= before and "query_stock_prices" not in before
    asyncio.run(turn.use_skill({"name": skill["name"]}))
    after = _names(turn)
    # The skill keeps query_financials; query_stock_prices is outside the bot's scope; the rest is gone.
    assert "query_financials" in after and "query_stock_prices" not in after
    assert "query_sec_filings" not in after and "web_research" not in after
    assert {"ask_user", "calculate", "read_history_citation"} <= after  # the basics stay
    assert after <= before


def test_a_write_a_skill_allows_still_needs_the_owners_words_and_a_card(owner):
    skill = _skill(name="扩研究队列", tools=["query_research_queue", "add_to_research_queue"])
    executed = []

    def executor(name, args):
        executed.append(name)
        return {"ok": True, "data": []}
    active = skills.active(OWNER_HASH, name=skill["name"])
    # Without the owner's words the write is not even offered: calling it is refused, never converted.
    steps = iter([{"tool_calls": [{"id": "w1", "function": {"name": "add_to_research_queue", "arguments": json.dumps({"symbol": "AMD"})}}]},
                  {"content": "没有执行。"}])
    direct, turn, context, quiet = _turn(active_skill=active, decider=lambda *_: next(steps), executor=executor,
                                         recognizer=lambda _p: {"category": "D", "days_back": None, "requested_actions": []})
    assert "add_to_research_queue" not in _names(turn)
    result = asyncio.run(direct.run_direct_turn("看看研究队列", context, emit=quiet))
    assert executed == [] and result.get("pending_request") is None
    # With them, it is a confirmation card, still not executed.
    steps = iter([{"tool_calls": [{"id": "w2", "function": {"name": "add_to_research_queue", "arguments": json.dumps({"symbol": "AMD"})}}]},
                  {"content": "请确认。"}])
    direct, turn, context, quiet = _turn(active_skill=active, decider=lambda *_: next(steps), executor=executor,
                                         recognizer=lambda _p: {"category": "D", "days_back": None, "requested_actions": ["add_to_research_queue"]})
    result = asyncio.run(direct.run_direct_turn("把 AMD 加入研究队列", context, emit=quiet))
    assert executed == [] and result["pending_request"]["reason"] == "write_confirmation"


# --- starting a skill ------------------------------------------------------------------------------------

def test_slash_starts_an_enabled_skill_and_leaves_other_messages_alone(owner):
    skill, question = skills.slash(OWNER_HASH, "/财报速读 AAPL 最新一季")
    assert skill["name"] == "财报速读" and question == "AAPL 最新一季"
    assert skills.slash(OWNER_HASH, "/财报速读")[1] == "按技能「财报速读」做。"
    for text in ("/不存在的技能 你好", "/财报速读器", "财报速读 AAPL", "看看 /财报速读"):
        assert skills.slash(OWNER_HASH, text) == (None, text)
    builtin = next(s for s in skills.list_skills(OWNER_HASH) if s["name"] == "财报速读")
    skills.set_enabled(OWNER_HASH, builtin["id"], False)
    assert skills.slash(OWNER_HASH, "/财报速读 AAPL")[0] is None


def test_a_stream_turn_starts_the_skill_and_lists_the_bots_usual_first(owner, monkeypatch):
    from app import agent_chat_stream as stream
    monkeypatch.setattr(stream, "_is_owner", lambda c: c == OWNER)
    mine = _skill(name="苹果现金流")
    bot = bots.create_bot(OWNER_HASH, {"name": "苹果", "skills": [mine["id"]]})
    settings = bots.turn_settings(OWNER_HASH, bot["id"])
    block, active, question = asyncio.run(stream._skills(OWNER, settings, "/苹果现金流 去年"))
    assert active["name"] == "苹果现金流" and question == "去年"
    assert block.splitlines()[1].startswith("- 苹果现金流")  # the bot's usual skill first
    assert asyncio.run(stream._skills("g" * 40, settings, "/苹果现金流 去年")) == (None, None, "/苹果现金流 去年")
    with pytest.raises(bots.BotError, match="unknown_skill"):
        bots.update_bot(OWNER_HASH, bot["id"], {"skills": [str(uuid.uuid4())]})
    assert bots.get_bot(OWNER_HASH, bot["id"])["skills"] == [mine["id"]]


def test_a_task_runs_with_its_skill(owner, monkeypatch):
    tasks.save_background_model(OWNER_HASH, provider="deepseek", model="deepseek-chat", base_url=None, api_key=KEY)
    builtin = next(s for s in skills.list_skills(OWNER_HASH) if s["name"] == "新闻摘要")
    with pytest.raises(tasks.TaskError, match="unknown_skill"):
        tasks.create_task(OWNER_HASH, {"name": "x", "kind": "brief", "schedule_kind": "daily", "schedule_time": "08:00",
                                       "instruction": "x", "skill_id": str(uuid.uuid4())})
    task = tasks.create_task(OWNER_HASH, {"name": "新闻日报", "kind": "brief", "schedule_kind": "daily", "schedule_time": "08:00",
                                          "instruction": "按技能整理新闻。", "skill_id": builtin["id"]},
                             now=datetime(2026, 10, 30, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert task["skill_id"] == builtin["id"]
    from app.agent_workflow import graph
    seen = {}

    async def stream_workflow(**kwargs):
        seen.update(kwargs)
        yield {"event": "delta", "text": "无新消息"}
        yield {"event": "done", "status": "complete", "citations": [], "final_protocol": {}, "audit": {}}
    monkeypatch.setattr(graph, "stream_workflow", stream_workflow)
    from app import agent_chat_store as store

    async def go():
        try:
            return await runner.run_due(now=datetime(2026, 10, 30, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")), credential=OWNER)
        finally:
            await store.engine.dispose()
    assert asyncio.run(go()) == {task["id"]: "quiet"}
    assert seen["active_skill"]["name"] == "新闻摘要" and "新闻摘要" in seen["skill_block"]
    assert tasks.update_task(OWNER_HASH, task["id"], {"skill_id": ""})["skill_id"] is None


# --- drafts, import, export ------------------------------------------------------------------------------

def test_a_draft_is_made_only_when_the_owner_asks_and_is_inert_until_saved(owner):
    fields = {"name": "查苹果现金流", "description": "读苹果的现金流。", "body": "1. query_financials 读现金流。",
              "allowed_tools": ["query_financials"]}
    direct, turn, context, quiet = _turn(question="把刚才这个流程存成技能")
    outcome = asyncio.run(turn.draft_skill(fields))
    assert outcome["ok"] and outcome["data"][0]["status"] == "draft_saved"
    draft = next(s for s in skills.list_skills(OWNER_HASH) if s["name"] == "查苹果现金流")
    assert draft["status"] == "draft" and draft["enabled"] is False
    assert "查苹果现金流" not in skills.catalogue_block(OWNER_HASH) and skills.active(OWNER_HASH, name="查苹果现金流") is None
    assert skills.slash(OWNER_HASH, "/查苹果现金流 x")[0] is None
    # Not asked (a page or tool result cannot ask for it), or nobody there: refused.
    direct, turn, context, quiet = _turn(question="苹果现金流多少")
    assert asyncio.run(turn.draft_skill({**fields, "name": "别的"}))["error_code"] == "not_asked"
    direct, turn, context, quiet = _turn(question="存成技能", unattended=True)
    assert asyncio.run(turn.draft_skill({**fields, "name": "别的"}))["error_code"] == "unattended_no_user"
    # The owner saves it in the editor: now it is a skill.
    saved = skills.update_skill(OWNER_HASH, draft["id"], {"save_draft": True, "body": "1. 读现金流并标来源。"})
    assert saved["status"] == "active" and saved["enabled"] and "查苹果现金流" in skills.catalogue_block(OWNER_HASH)


def test_markdown_export_and_import_show_the_tools_and_refuse_code(owner):
    mine = _skill(name="读现金流", tools=["query_financials", "fetch_sec"], output_template="**结论**")
    text = skills.export_markdown(OWNER_HASH, mine["id"])
    assert text.startswith("---\nname: 读现金流\n") and "  - fetch_sec" in text and "## 输出格式" in text
    skills.delete_skill(OWNER_HASH, mine["id"])
    preview = skills.import_preview(text)
    assert preview["skill"]["allowed_tools"] == ["query_financials", "fetch_sec"]
    assert {t["name"]: t["write"] for t in preview["tools"]} == {"query_financials": False, "fetch_sec": True}
    imported = skills.import_skill(OWNER_HASH, text)
    assert imported["origin"] == "imported" and imported["output_template"] == "**结论**"
    for bad, code in (("no front matter", "invalid_skill_file"),
                      ("---\nname: x\ndescription: d\ntools: [query_financials, curl]\n---\n步骤", "unknown_tool"),
                      ("---\nname: x\ndescription: d\nscript: run.sh\ntools: [query_financials]\n---\n步骤", "code_not_allowed"),
                      ("---\nname: x\ndescription: d\ntools: [query_financials]\n---\n```bash\nrm -rf /\n```", "code_not_allowed")):
        with pytest.raises(skills.SkillError, match=code):
            skills.import_preview(bad)


def test_the_skills_api_is_owner_only(owner, client):
    guest = {"X-Chat-Session": "g" * 40}
    assert client.get("/agent-chat/skills", headers=guest).status_code in {401, 403}
    listed = client.get("/agent-chat/skills", headers=H).json()
    assert len(listed["skills"]) == 3 and "use_skill" not in {t["name"] for t in listed["tools"]}
    created = client.post("/agent-chat/skills", headers=H, json={"name": "读现金流", "description": "d", "body": "b",
                                                                 "allowed_tools": ["query_financials"]})
    assert created.status_code == 200, created.text
    sid = created.json()["skill"]["id"]
    bad = client.post("/agent-chat/skills", headers=H, json={"name": "坏", "description": "d", "body": "b", "allowed_tools": ["nope"]})
    assert bad.status_code == 422 and bad.json()["detail"] == "unknown_tool"
    assert client.post(f"/agent-chat/skills/{sid}/enabled", headers=H, json={"enabled": False}).json()["skill"]["enabled"] is False
    exported = client.get(f"/agent-chat/skills/{sid}/export", headers=H)
    assert exported.headers["content-type"].startswith("text/markdown") and "name: 读现金流" in exported.text
    assert client.post("/agent-chat/skills/import/preview", headers=H, json={"markdown": exported.text}).status_code == 200
    assert client.delete(f"/agent-chat/skills/{sid}", headers=H).json() == {"deleted": True}
