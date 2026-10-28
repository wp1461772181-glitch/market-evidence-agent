"""Bots and the server model catalog (personal agent plan, second edition, stage A).

* the owner's 「助理」 exists once, can be renamed, never deleted or hidden; conversations from before bots
  move under it;
* bots are created from a form (a template only fills it), each with its own conversation; deleting one
  leaves every other bot and its conversation as they were;
* a bot whose focus is exactly one company answers an unnamed question about it, and the audit says the
  company came from the bot's settings; a named company wins; several companies give no default;
* a tool outside the bot's scope is neither offered nor run, whatever the model asks for;
* the model comes from the catalog on every turn, so a changed model is used on the next one; no response,
  log line or error ever carries a key.
"""
import asyncio
import json
import logging
import uuid

import pytest

from app import agent_bots as bots
from app import agent_model_catalog as catalog
from app.agent_chat_models import AgentBot, AgentConversation, AgentModelEntry
from app.agent_chat_store import owner_hash
from app.database import Base, SessionLocal, engine

# The stream endpoint wants a session credential of at least 32 characters.
OWNER = "o" * 40
OWNER_HASH = owner_hash(OWNER)
GUEST = "g" * 40
KEY_A = "sk-catalog-alpha-0123456789abcdef"
KEY_B = "sk-catalog-bravo-fedcba9876543210"
H = {"X-Chat-Session": OWNER}


@pytest.fixture
def owner(monkeypatch, disposable_database, tmp_path):
    skipped = {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs",
               "material_analysis_settings", "ai_content_translations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values()
                                             if not t.name.endswith("_v2") and not t.name.startswith("knowledge_")
                                             and t.name not in skipped])
    from app.agent_chat_store import ensure_conversation_archive_column
    ensure_conversation_archive_column()
    with SessionLocal() as db:
        db.query(AgentBot).delete()
        db.query(AgentModelEntry).delete()
        db.query(AgentConversation).filter(AgentConversation.owner_hash == OWNER_HASH).delete()
        db.commit()
    # No deployment config file: only entries added here.
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


def _call(name, arguments, call_id):
    return {"tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


async def _quiet(_event):
    pass


def _turn(question, settings, *, recognizer, decider, executor=None):
    """One real Turn of a bot with injected model callbacks."""
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    context = AgentRuntimeContext("custom", "test", "dummy", identity_credential=OWNER, owner_hash=OWNER_HASH,
                                  conversation_id=settings["conversation_id"], intent_recognizer=recognizer,
                                  decider=decider, executor=executor, bot_id=settings["bot_id"],
                                  bot_block=settings["block"], tool_scope=settings["tool_scope"],
                                  default_company=settings["default_company"])
    return asyncio.run(direct.run_direct_turn(question, context, emit=_quiet))


# --- default bot -------------------------------------------------------------------------------------

def test_default_bot_is_made_once_adopts_old_conversations_and_cannot_be_deleted_or_hidden(owner, client):
    with SessionLocal() as db:
        old = AgentConversation(owner_hash=OWNER_HASH)
        db.add(old)
        db.commit()
        old_id = old.id
    listed = client.get("/agent-chat/bots", headers=H).json()
    again = client.get("/agent-chat/bots", headers=H).json()
    assert len(listed["bots"]) == len(again["bots"]) == 1
    default = listed["bots"][0]
    assert default["name"] == "助理" and default["is_default"] and listed["default_bot_id"] == default["id"]
    assert default["conversation_id"] and default["conversation_id"] != str(old_id)
    with SessionLocal() as db:
        assert str(db.get(AgentConversation, old_id).bot_id) == default["id"]  # 往事 stays, under 助理
    renamed = client.patch(f"/agent-chat/bots/{default['id']}", headers=H, json={"name": "总助理"}).json()["bot"]
    assert renamed["name"] == "总助理" and renamed["is_default"]
    assert client.delete(f"/agent-chat/bots/{default['id']}", headers=H).status_code == 422
    assert client.patch(f"/agent-chat/bots/{default['id']}", headers=H, json={"hidden": True}).status_code == 422
    assert client.get("/agent-chat/bots", headers={"X-Chat-Session": GUEST}).status_code == 403


# --- create / edit / delete ----------------------------------------------------------------------------

def test_two_bots_with_different_descriptions_and_scopes_and_deleting_one_leaves_the_other(owner, client):
    templates = {t["id"]: t for t in client.get("/agent-chat/bot-templates", headers=H).json()["templates"]}
    assert set(templates) == {"blank", "company_researcher", "news_monitor", "earnings_reader"}
    assert client.get("/agent-chat/bots", headers=H).json()["bots"][0]["is_default"]  # no seven sisters
    assert len(client.get("/agent-chat/bots", headers=H).json()["bots"]) == 1
    # A template only fills the form; the owner edits it before saving.
    earnings = {**templates["earnings_reader"], "description": "只读 10-K 和 10-Q，和上一期对比。"}
    reader = client.post("/agent-chat/bots", headers=H, json={k: earnings[k] for k in
                         ("name", "title", "description", "tool_scope")} | {"template": "earnings_reader"}).json()["bot"]
    news = client.post("/agent-chat/bots", headers=H, json={
        "name": "抓新闻", "description": "只抓新闻，重要的才告诉我。", "template": "news_monitor",
        "tool_scope": ["web_research", "search_knowledge"]}).json()["bot"]
    assert reader["name"] == "读财报" and news["name"] == "抓新闻"
    assert reader["description"] != news["description"] and set(reader["tool_scope"]) != set(news["tool_scope"])
    assert "query_financials" in reader["tool_scope"] and news["tool_scope"] == ["web_research", "search_knowledge"]
    assert len({reader["conversation_id"], news["conversation_id"]}) == 2
    # Unknown tools and duplicate names are refused; the scope can only name catalog tools.
    assert client.post("/agent-chat/bots", headers=H, json={"name": "x", "tool_scope": ["rm_rf"]}).status_code == 422
    assert client.post("/agent-chat/bots", headers=H, json={"name": "抓新闻"}).status_code == 422
    assert client.post("/agent-chat/bots", headers=H, json={"name": "y", "model": "nope"}).status_code == 422

    assert client.delete(f"/agent-chat/bots/{news['id']}", headers=H).json() == {"deleted": True}
    left = client.get(f"/agent-chat/bots/{reader['id']}", headers=H).json()["bot"]
    assert left == {**reader, "updated_at": left["updated_at"]}
    assert client.get(f"/agent-chat/bots/{news['id']}", headers=H).status_code == 404
    with SessionLocal() as db:
        archived = db.get(AgentConversation, uuid.UUID(news["conversation_id"]))
        kept = db.get(AgentConversation, uuid.UUID(reader["conversation_id"]))
        assert archived.archived_at is not None and kept.archived_at is None
    names = [b["name"] for b in client.get("/agent-chat/bots", headers=H).json()["bots"]]
    assert names == ["助理", "读财报"]


def test_scope_null_means_every_tool_and_basics_stay_in_a_narrow_scope(owner):
    bot = bots.create_bot(OWNER_HASH, {"name": "全能"})
    assert bot["tool_scope"] is None and bots.turn_settings(OWNER_HASH, bot["id"])["tool_scope"] is None
    narrow = bots.create_bot(OWNER_HASH, {"name": "窄", "tool_scope": ["web_research", "ask_user"]})
    assert narrow["tool_scope"] == ["web_research"]
    scope = bots.turn_settings(OWNER_HASH, narrow["id"])["tool_scope"]
    assert "web_research" in scope and bots.ALWAYS_ON <= scope and "query_financials" not in scope


def test_another_owner_cannot_see_or_change_a_bot(owner):
    bot = bots.create_bot(OWNER_HASH, {"name": "私人"})
    other = owner_hash("x" * 40)
    assert bots.get_bot(other, bot["id"]) is None and bots.turn_settings(other, bot["id"]) is None
    assert bots.update_bot(other, bot["id"], {"name": "偷"}) is None and bots.delete_bot(other, bot["id"]) is False
    assert bots.get_bot(OWNER_HASH, bot["id"])["name"] == "私人"


# --- focus: the default company ---------------------------------------------------------------------------

def _apple_bot():
    bot = bots.create_bot(OWNER_HASH, {"name": "苹果", "focus": {"companies": [{"symbol": "aapl", "name": "Apple"}]}})
    assert bot["default_company"] == {"symbol": "AAPL", "company": "Apple"}
    return bots.turn_settings(OWNER_HASH, bot["id"])


def test_apple_bot_answers_an_unnamed_revenue_question_for_apple_and_audits_the_source(owner):
    settings = _apple_bot()
    reads, payloads = [], []

    def executor(name, args):
        reads.append((name, args))
        return {"ok": True, "data": [{"symbol": args.get("symbol"), "metric": "Revenues", "value": 391035000000,
                                       "unit": "USD", "period": "FY2024", "source_url": "https://www.sec.gov/x"}]}

    def recognizer(payload):
        payloads.append(payload)
        return {"category": "A", "days_back": None, "requested_actions": [], "metrics": ["revenue"]}
    result = _turn("去年营收多少", settings, recognizer=recognizer,
                   decider=lambda *_: {"content": "苹果去年营收 3910 亿美元 [1]。"}, executor=executor)
    assert payloads[0]["default_company"]["symbol"] == "AAPL"
    assert reads and reads[0][0] == "query_financials" and reads[0][1]["symbol"] == "AAPL"
    audit = result["audit"]["default_company"]
    assert audit["symbol"] == "AAPL" and audit["source"] == "bot_settings" and audit["bot_id"] == settings["bot_id"]


def test_a_named_company_wins_over_the_bots_default(owner):
    settings = _apple_bot()
    reads = []

    def executor(name, args):
        reads.append((name, args))
        return {"ok": True, "data": []}
    result = _turn("微软去年营收多少", settings,
                   recognizer=lambda _p: {"category": "A", "days_back": None, "requested_actions": [],
                                          "symbol": "MSFT", "company": "微软", "metrics": ["revenue"]},
                   decider=lambda *_: {"content": "没有找到。"}, executor=executor)
    assert "default_company" not in result["audit"]
    assert all(args.get("symbol") != "AAPL" for _, args in reads)


def test_several_or_no_focus_companies_give_no_default(owner):
    many = bots.create_bot(OWNER_HASH, {"name": "科技", "focus": {"companies": ["AAPL", "MSFT"], "theme": "AI"}})
    assert many["default_company"] is None
    payloads = []

    def recognizer(payload):
        payloads.append(payload)
        return {"category": "A", "days_back": None, "requested_actions": [], "metrics": ["revenue"]}
    result = _turn("去年营收多少", bots.turn_settings(OWNER_HASH, many["id"]), recognizer=recognizer,
                   decider=lambda *_: {"content": "哪家公司？"}, executor=lambda *_: {"ok": True, "data": []})
    assert "default_company" not in payloads[0] and "default_company" not in result["audit"]


# --- tool scope ---------------------------------------------------------------------------------------------

def test_a_tool_outside_the_bots_scope_is_not_offered_and_not_run(owner):
    news = bots.create_bot(OWNER_HASH, {"name": "抓新闻", "tool_scope": ["web_research", "search_knowledge"]})
    settings = bots.turn_settings(OWNER_HASH, news["id"])
    offered, ran = [], []
    steps = iter([_call("query_financials", {"symbol": "AAPL", "metrics": ["Revenues"]}, "c1"),
                  _call("request_action", {"tool": "add_to_research_queue", "arguments": {"symbol": "AMD"},
                                           "reason": "竞争对手"}, "c2"),
                  {"content": "只能查新闻。"}])

    def decider(messages, schemas):
        offered.append({s["function"]["name"] for s in schemas})
        return next(steps)

    def executor(name, args):
        ran.append(name)
        return {"ok": True, "data": []}
    result = _turn("苹果最近有什么新闻", settings,
                   recognizer=lambda _p: {"category": "D", "days_back": None,
                                          "requested_actions": ["add_to_research_queue"]},
                   decider=decider, executor=executor)
    assert offered and all(names <= settings["tool_scope"] for names in offered)
    assert "query_financials" not in offered[0] and "web_research" in offered[0]
    assert "query_financials" not in ran and "add_to_research_queue" not in ran
    assert result["audit"]["out_of_scope_calls"] == ["query_financials"]
    assert not result.get("pending_request")  # no card for a tool the bot may not use


def test_invoke_refuses_out_of_scope_tools_even_when_called_directly(owner):
    from app.agent_workflow import tools
    from app.agent_workflow.state import AgentRuntimeContext
    context = AgentRuntimeContext("custom", "test", "dummy", identity_credential=OWNER, owner_hash=OWNER_HASH,
                                  conversation_id=str(uuid.uuid4()), tool_scope=frozenset({"web_research"}))
    outcome = asyncio.run(tools.invoke("query_financials", {"symbol": "AAPL", "metrics": ["Revenues"]}, context))
    assert outcome["ok"] is False and outcome["error_code"] == "tool_not_in_bot_scope"


# --- model catalog --------------------------------------------------------------------------------------

def _keys_absent(text):
    for key in (KEY_A, KEY_B):
        assert key not in text and key[-6:] not in text and key[:12] not in text


def test_catalog_api_says_configured_or_missing_and_never_returns_a_key(owner, client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    added = client.post("/agent-chat/model-catalog", headers=H, json={
        "name": "alpha", "provider": "deepseek", "model": "deepseek-chat", "api_key": KEY_A, "is_default": True})
    assert added.status_code == 200 and added.json()["model"]["key_status"] == "configured"
    monkeypatch.delenv("BRAVO_API_KEY", raising=False)
    client.post("/agent-chat/model-catalog", headers=H, json={
        "name": "bravo", "provider": "qwen", "model": "qwen-plus", "key_env": "BRAVO_API_KEY"})
    listed = client.get("/agent-chat/model-catalog", headers=H)
    status = {m["name"]: m["key_status"] for m in listed.json()["models"]}
    assert status == {"alpha": "configured", "bravo": "missing"} and listed.json()["default"] == "alpha"
    monkeypatch.setenv("BRAVO_API_KEY", KEY_B)
    assert {m["name"]: m["key_status"] for m in client.get("/agent-chat/model-catalog", headers=H).json()["models"]}["bravo"] == "configured"
    # A bad request is answered with a code and never echoes the key it carried.
    bad = client.post("/agent-chat/model-catalog", headers=H, json={"name": "BAD NAME", "provider": "deepseek",
                                                                     "model": "m", "api_key": KEY_B})
    assert bad.status_code == 422
    with SessionLocal() as db:
        row = db.get(AgentModelEntry, "alpha")
        assert row.ciphertext and KEY_A not in row.ciphertext  # encrypted at rest
    for line in catalog.startup_report():
        _keys_absent(line)
    report = "\n".join(catalog.startup_report())
    assert "alpha" in report and "usable" in report
    for response in (added, listed, bad):
        _keys_absent(response.text)
    _keys_absent(caplog.text)
    # The server's own secrets cannot be named as a model key.
    assert client.post("/agent-chat/model-catalog", headers=H, json={
        "name": "evil", "provider": "deepseek", "model": "m", "key_env": "MODEL_CREDENTIAL_KEY"}).status_code == 422
    assert client.get("/agent-chat/model-catalog", headers={"X-Chat-Session": GUEST}).status_code == 403


def test_changing_the_endpoint_needs_the_key_again(owner):
    catalog.save_entry("gw", provider="custom", model="m1", base_url="https://llm.example.com/v1", api_key=KEY_A,
                       create=True)
    with pytest.raises(catalog.CatalogError, match="key_required"):
        catalog.save_entry("gw", base_url="https://elsewhere.example.com/v1")
    with pytest.raises(catalog.CatalogError, match="key_required"):
        catalog.save_entry("gw", provider="deepseek")
    catalog.save_entry("gw", model="m2")  # same endpoint: the key stays
    assert catalog.resolve("gw")["api_key"] == KEY_A
    catalog.save_entry("gw", base_url="https://elsewhere.example.com/v1", api_key=KEY_B)
    assert catalog.resolve("gw")["api_key"] == KEY_B


def test_config_file_names_env_vars_only_and_a_key_in_it_is_ignored(owner, monkeypatch, tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("default: filed\nmodels:\n"
                    "  - {name: filed, provider: deepseek, model: deepseek-chat, key_env: FILED_API_KEY}\n"
                    f"  - {{name: leaky, provider: deepseek, model: deepseek-chat, api_key: {KEY_B}}}\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("FILED_API_KEY", KEY_A)
    public = catalog.list_public()
    assert [m["name"] for m in public["models"]] == ["filed"] and public["default"] == "filed"
    assert any("key_in_file" in p for p in public["config_file"]["problems"])
    _keys_absent(json.dumps(public))
    _keys_absent("\n".join(catalog.startup_report()))
    assert catalog.resolve(None)["api_key"] == KEY_A
    with pytest.raises(catalog.CatalogError, match="model_from_file"):
        catalog.save_entry("filed", api_key=KEY_B)


def test_legacy_background_model_and_local_profile_keys_move_into_the_vault(owner, monkeypatch):
    from app import agent_tasks as tasks
    tasks.save_background_model(OWNER_HASH, provider="deepseek", model="deepseek-chat", base_url=None, api_key=KEY_A)
    monkeypatch.setattr(catalog, "_local_profiles", lambda: [
        {"id": "p1", "display_name": "Kimi", "provider": "kimi", "model": "kimi-k2", "api_key": KEY_B}])
    added = catalog.migrate_legacy()
    assert len(added) == 2 and catalog.migrate_legacy() == []  # once each
    keys = {catalog.resolve(name)["api_key"] for name in added}
    assert keys == {KEY_A, KEY_B}
    with SessionLocal() as db:
        assert all(KEY_A not in (r.ciphertext or "") and KEY_B not in (r.ciphertext or "")
                   for r in db.query(AgentModelEntry).all())
    # Revoking the background key revokes the catalog's copy too.
    tasks.revoke_background_key(OWNER_HASH)
    background = next(n for n in added if n.startswith("background"))
    with pytest.raises(catalog.CatalogError, match="model_key_missing"):
        catalog.resolve(background)


def test_a_model_in_use_by_a_bot_cannot_be_deleted(owner):
    catalog.save_entry("alpha", provider="deepseek", model="deepseek-chat", api_key=KEY_A, create=True)
    bots.create_bot(OWNER_HASH, {"name": "用 alpha", "model": "alpha"})
    with pytest.raises(catalog.CatalogError, match="model_in_use"):
        catalog.delete_entry("alpha")


# --- a bot's turn through the stream endpoint ------------------------------------------------------------

def test_bot_turn_uses_the_catalog_model_and_a_changed_model_applies_on_the_next_turn(owner, client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    catalog.save_entry("alpha", provider="deepseek", model="deepseek-chat", api_key=KEY_A, create=True, is_default=True)
    catalog.save_entry("bravo", provider="qwen", model="qwen-plus", api_key=KEY_B, create=True)
    bot = client.post("/agent-chat/bots", headers=H, json={"name": "读财报", "model": "alpha",
                                                            "tool_scope": ["query_financials"],
                                                            "focus": {"companies": [{"symbol": "AAPL"}]}}).json()["bot"]
    seen = []

    async def stream_workflow(**kwargs):
        seen.append(kwargs)
        yield {"event": "delta", "text": "好的。"}
        yield {"event": "done", "status": "complete", "citations": [], "final_protocol": {}, "audit": {}}
    import app.agent_chat_stream as stream
    monkeypatch.setattr(stream, "stream_workflow", stream_workflow)
    monkeypatch.setattr("app.agent_context_summary.schedule_summary", lambda *a, **k: None)
    monkeypatch.setattr("app.agent_profile_extract.schedule_extraction", lambda *a, **k: None)

    first = client.post("/agent-chat/stream", headers=H, json={"message": "去年营收多少", "bot_id": bot["id"]})
    assert first.status_code == 200, first.text
    assert seen[0]["provider"] == "deepseek" and seen[0]["api_key"] == KEY_A
    assert seen[0]["conversation_id"] == bot["conversation_id"]
    assert seen[0]["bot"]["tool_scope"] >= {"query_financials"} and seen[0]["bot"]["default_company"]["symbol"] == "AAPL"

    client.patch(f"/agent-chat/bots/{bot['id']}", headers=H, json={"model": "bravo"})
    second = client.post("/agent-chat/stream", headers=H, json={"message": "那前年呢", "bot_id": bot["id"]})
    assert second.status_code == 200 and seen[1]["provider"] == "qwen" and seen[1]["api_key"] == KEY_B

    # The browser never sent a key, and nothing that goes back to it or into the log carries one.
    for response in (first, second, client.get("/agent-chat/bots", headers=H),
                     client.get(f"/agent-chat/bots/{bot['id']}", headers=H)):
        _keys_absent(response.text)
    _keys_absent(caplog.text)
    # Another bot's conversation cannot be used as this bot's line.
    other = client.post("/agent-chat/bots", headers=H, json={"name": "别的"}).json()["bot"]
    mismatch = client.post("/agent-chat/stream", headers=H, json={"message": "hi", "bot_id": bot["id"],
                                                                  "conversation_id": other["conversation_id"]})
    assert mismatch.status_code == 409
    # A guest has no bots, and a turn without bot_id still needs the browser's model settings.
    assert client.post("/agent-chat/stream", headers={"X-Chat-Session": GUEST},
                       json={"message": "hi", "bot_id": bot["id"]}).status_code == 403
    assert client.post("/agent-chat/stream", headers=H, json={"message": "hi"}).status_code == 422


def test_bot_turn_with_a_missing_key_fails_with_a_code(owner, client, monkeypatch):
    monkeypatch.delenv("NOKEY_API_KEY", raising=False)
    catalog.save_entry("nokey", provider="deepseek", model="deepseek-chat", key_env="NOKEY_API_KEY", create=True)
    bot = bots.create_bot(OWNER_HASH, {"name": "缺 key", "model": "nokey"})
    response = client.post("/agent-chat/stream", headers=H, json={"message": "hi", "bot_id": bot["id"]})
    assert response.status_code == 409 and response.json()["detail"] == "model_key_missing"


def test_an_orb_avatar_must_name_a_palette_colour_and_emoji_still_work(owner):
    """UX batch 2 (P2): ``orb:<colour>`` is the FinBot orb; an unknown colour is refused, an emoji is kept."""
    identity = OWNER_HASH
    made = bots.create_bot(identity, {"name": "圆球-" + uuid.uuid4().hex[:6], "avatar": "orb:pink"})
    assert made["avatar"] == "orb:pink"
    with pytest.raises(bots.BotError):
        bots.update_bot(identity, made["id"], {"avatar": "orb:neon"})
    assert bots.update_bot(identity, made["id"], {"avatar": "📈"})["avatar"] == "📈"
    assert bots.update_bot(identity, made["id"], {"avatar": "orb:finbot"})["avatar"] == "orb:finbot"
