"""Acceptance tests for FinBot News Sources Engine, Product Todos A & B, and Self-Healing (§8)."""
from __future__ import annotations

import asyncio
import json

import pytest

from app import fin_news_engine as engine_mod
from app.agent_chat_models import AgentConversation, AgentInboxItem, AgentMessage, NewsItem, NewsPushLog
from app.agent_chat_store import owner_hash
from app.agent_workflow import consent, direct, evidence, tools
from app.agent_workflow.state import AgentRuntimeContext
from app.database import Base, SessionLocal, engine

OWNER = "o" * 40
OWNER_HASH = owner_hash(OWNER)
H = {"X-Chat-Session": OWNER}


@pytest.fixture
def news_env(monkeypatch, disposable_database, tmp_path):
    skipped = {
        "material_analysis_jobs",
        "material_analysis_versions",
        "material_analysis_job_model_configs",
        "material_analysis_settings",
        "ai_content_translations",
    }
    Base.metadata.create_all(
        engine,
        tables=[
            t
            for t in Base.metadata.tables.values()
            if not t.name.endswith("_v2") and not t.name.startswith("knowledge_") and t.name not in skipped
        ],
    )
    with SessionLocal() as db:
        for model in (NewsItem, NewsPushLog, AgentInboxItem, AgentMessage, AgentConversation):
            db.query(model).delete()
        db.commit()

    cfg_path = tmp_path / "news_sources.yaml"
    cfg_path.write_text(
        engine_mod.config_path().read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("FINBOT_NEWS_SOURCES_CONFIG", str(cfg_path))
    engine_mod.clear_runtime_state()
    engine_mod.set_http_fetcher(None)

    import app.agent_chat_identity as identity
    for module in (consent, direct, tools, identity):
        monkeypatch.setattr(module, "owner_credential_matches", lambda c: c == OWNER)
    yield cfg_path
    engine_mod.set_http_fetcher(None)


def _call(name: str, arguments: dict, call_id: str) -> dict:
    return {"tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


async def _quiet(_event: dict) -> None:
    return None


def test_1_single_rss_source_returns_items_with_url(news_env):
    """§8.1 只开启一个 RSS 源时，search_configured_news(tickers=['NVDA']) 能返回带 url 的条目。"""
    cfg = engine_mod.load_config(news_env)
    for src in cfg["sources"]:
        src["enabled"] = (src["id"] == "yahoo-finance-rss")
    engine_mod.save_config(cfg, news_env)

    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Market Feed</title>
      <item>
        <title>NVIDIA (NVDA) announces next-gen AI GPU rack shipments</title>
        <link>https://finance.yahoo.com/news/nvda-gpu-rack-2026</link>
        <pubDate>Thu, 29 Oct 2026 09:00:00 GMT</pubDate>
        <description>NVIDIA reported strong demand for NVDA Blackwell racks totaling 42000 units.</description>
      </item>
    </channel></rss>"""

    engine_mod.set_http_fetcher(lambda url, headers, timeout: (200, sample_rss))
    res = engine_mod.search_configured_news(tickers=["NVDA"], refresh_first=True, limit=10)
    assert len(res["items"]) == 1
    item = res["items"][0]
    assert item["url"] == "https://finance.yahoo.com/news/nvda-gpu-rack-2026"
    assert "NVDA" in item["tickers"]
    assert res["meta"].get("hint") is None


def test_2_all_sources_disabled_returns_empty_with_fallback_hint_and_agent_falls_back(news_env, monkeypatch):
    """§8.2 所有源都 enabled: false 时，search_configured_news 返回空 + fallback_to_web_search_recommended，Agent 自动走网页搜索兜底。"""
    cfg = engine_mod.load_config(news_env)
    for src in cfg["sources"]:
        src["enabled"] = False
    engine_mod.save_config(cfg, news_env)

    res = engine_mod.search_configured_news(tickers=["NVDA"])
    assert res["items"] == []
    assert res["meta"]["hint"] == "fallback_to_web_search_recommended"

    calls_made: list[str] = []

    def executor(name: str, args: dict):
        calls_made.append(name)
        if name == "search_configured_news":
            return engine_mod.search_configured_news(**args)
        if name == "web_research":
            return {
                "ok": True,
                "data": [
                    {
                        "title": "NVDA AI News Fallback",
                        "url": "https://example.com/nvda-fallback",
                        "published_at": "2026-10-29",
                        "excerpt": "NVIDIA share price rose 5 percent on new data center order.",
                        "content_hash": "hash1",
                    }
                ],
                "source": "public web",
            }
        raise AssertionError(name)

    def resolver(payload: dict) -> dict:
        return {"relationship": "new", "canonical_question": "查 NVDA 最新新闻", "symbols": ["NVDA"], "referents": []}

    def recognizer(payload: dict) -> dict:
        return {"category": "A", "mode": "none", "symbols": ["NVDA"]}

    step_idx = {"n": 0}

    def decider(_messages: list[dict], _tools: list[dict]) -> dict:
        step_idx["n"] += 1
        if step_idx["n"] == 1:
            return _call("search_configured_news", {"tickers": ["NVDA"]}, "c1")
        if step_idx["n"] == 2:
            return _call("web_research", {"query": "NVDA latest news"}, "c2")
        return {"content": "根据网页兜底检索，NVIDIA 因新数据中心订单上涨 5% [1]。"}

    ctx = AgentRuntimeContext("custom", "test", "dummy", resolver=resolver, intent_recognizer=recognizer, decider=decider, executor=executor)
    result = asyncio.run(direct.run_direct_turn("查 NVDA 最新新闻", ctx, emit=_quiet))
    assert result["status"] == "succeeded"
    assert calls_made == ["search_configured_news", "web_research"]


def test_3_forged_news_item_without_url_is_rejected_by_citation_gate(news_env):
    """§8.3 伪造一条没有 url 的新闻入库后，引用层拒绝该条作为证据（missing_citation_url）。"""
    # 1. normalize_item rejects missing url
    assert engine_mod.normalize_item({"title": "No URL News"}, {"id": "src1", "tier": "media"}) is None

    # 2. Even if a row without URL is forged into evidence.gate_row or citation_errors, it is rejected
    ok, reason = evidence.gate_row(
        "NVDA revenue",
        {"source_type": "configured_news", "title": "Forged News Without URL", "url": "", "summary": "Revenue was 999 billion."},
    )
    assert ok is False
    assert reason == "missing_citation_url"

    sources = [
        {
            "ordinal": 1,
            "source_type": "configured_news",
            "title": "Forged News Without URL",
            "source_url": None,
            "quote": "Revenue was 999 billion.",
        }
    ]
    errors = direct.citation_errors("NVDA 营收达到 999 billion [1]。", sources)
    assert errors == [1]


def test_4_numeric_gate_enforced_on_configured_news_claims(news_env):
    """§8.4 新闻条目里的数字进入事实句时，数字门禁仍然生效（未出现在证据里的数字被拦截并修复）。"""
    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Feed</title>
      <item>
        <title>NVDA Q3 Preview</title>
        <link>https://finance.yahoo.com/news/nvda-q3-preview</link>
        <pubDate>Thu, 29 Oct 2026 09:00:00 GMT</pubDate>
        <description>Analysts expect NVDA revenue of 35 billion dollars in Q3.</description>
      </item>
    </channel></rss>"""
    engine_mod.set_http_fetcher(lambda url, headers, timeout: (200, sample_rss))

    def resolver(payload: dict) -> dict:
        return {"relationship": "new", "canonical_question": "NVDA Q3 新闻里预期营收多少？", "symbols": ["NVDA"], "referents": []}

    def recognizer(payload: dict) -> dict:
        return {"category": "A", "mode": "none", "symbols": ["NVDA"]}

    turn_step = {"n": 0}

    def decider(_messages: list[dict], _tools: list[dict]) -> dict:
        turn_step["n"] += 1
        if turn_step["n"] == 1:
            return _call("search_configured_news", {"tickers": ["NVDA"], "refresh_first": True}, "c1")
        if turn_step["n"] == 2:
            # First draft fabricates 88 billion (not in the news item!) -> rejected by numeric gate
            return {"content": "新闻显示分析师预计 NVDA Q3 营收为 88 billion dollars [1]。"}
        # Repaired draft uses 35 billion from the cited news item
        return {"content": "新闻显示分析师预计 NVDA Q3 营收为 35 billion dollars [1]。"}

    ctx = AgentRuntimeContext("custom", "test", "dummy", resolver=resolver, intent_recognizer=recognizer, decider=decider)
    result = asyncio.run(direct.run_direct_turn("NVDA Q3 新闻里预期营收多少？", ctx, emit=_quiet))
    assert result["status"] == "succeeded"
    assert "35 billion" in result["answer"]
    assert "88 billion" not in result["answer"]
    assert result["audit"].get("answer_repair_count", 0) >= 1


def test_5_push_summary_max_tokens_truncation_and_validation(news_env, client):
    """§8.5 修改 push_summary_max_tokens 为 128 与 512 时，推送摘要长度随之变化；非法值（0、99999）被配置校验拦截。"""
    items = [
        {
            "title": f"Headline {i}: Semiconductor supply chain expansion and AI capex update",
            "url": f"https://example.com/news/{i}",
            "summary": "Detailed analysis of wafer fab utilization, packaging capacity, and cloud GPU cluster deployments across North America and Asia.",
        }
        for i in range(1, 15)
    ]

    short_res = engine_mod.generate_digest_summary(items, max_tokens=128)
    long_res = engine_mod.generate_digest_summary(items, max_tokens=512)

    assert short_res["summary_max_tokens"] == 128
    assert long_res["summary_max_tokens"] == 512
    assert short_res["truncated"] is True
    assert "（已按你的长度上限截断）" in short_res["summary"]
    assert len(long_res["summary"]) > len(short_res["summary"])

    # Validation rejects 0 and 99999 via validate_config(strict_tokens=True) and API
    bad_cfg_zero = engine_mod.load_config(news_env)
    bad_cfg_zero["push"]["summary_max_tokens"] = 0
    with pytest.raises(ValueError, match="invalid_summary_max_tokens"):
        engine_mod.validate_config(bad_cfg_zero, strict_tokens=True)

    bad_cfg_huge = engine_mod.load_config(news_env)
    bad_cfg_huge["push"]["summary_max_tokens"] = 99999
    with pytest.raises(ValueError, match="invalid_summary_max_tokens"):
        engine_mod.validate_config(bad_cfg_huge, strict_tokens=True)

    resp_bad = client.put("/agent-chat/news-sources/push-settings", headers=H, json={"summary_max_tokens": 0})
    assert resp_bad.status_code == 422
    resp_huge = client.put("/agent-chat/news-sources/push-settings", headers=H, json={"summary_max_tokens": 99999})
    assert resp_huge.status_code == 422

    resp_ok = client.put("/agent-chat/news-sources/push-settings", headers=H, json={"summary_max_tokens": 512})
    assert resp_ok.status_code == 200
    assert resp_ok.json()["push"]["summary_max_tokens"] == 512


def test_6_inline_confirmation_on_notification_card_without_opening_chat(news_env, client):
    """§8.6 在通知卡片上直接点击「确认」或「跳过」，无需打开对话页面，状态即流转为 confirmed 或 skipped，对话内不重复弹出确认请求。"""
    with SessionLocal() as db:
        conv = AgentConversation(owner_hash=OWNER_HASH)
        db.add(conv)
        db.commit()
        cid = str(conv.id)

    card1 = engine_mod.create_inline_confirmation(
        owner_hash=OWNER_HASH,
        title="发现 NVDA 重大财报预告，是否立即触发深度分析？",
        summary="NVDA 发布最新 8-K 预告，点击确认即刻执行。",
        operation={"action": "acknowledge_news_digest", "item_ids": ["1"]},
        conversation_id=cid,
    )
    assert card1["confirmation_state"] == "pending_confirm"
    assert len(card1["actions"]) == 3

    # Confirm directly via /finbot/routines/{id}/confirm without opening chat
    resp = client.post(
        f"/finbot/routines/{card1['id']}/confirm",
        headers=H,
        json={"action": "confirm", "action_token": card1["action_token"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["confirmation_state"] == "confirmed"

    # Pressing confirm again is idempotent and does not duplicate messages
    resp_again = client.post(
        f"/finbot/routines/{card1['id']}/confirm",
        headers=H,
        json={"action": "confirm", "action_token": card1["action_token"]},
    )
    assert resp_again.status_code == 200
    assert resp_again.json()["already_processed"] is True

    # Verify conversation recorded the confirmed state so opening chat later won't re-prompt
    with SessionLocal() as db:
        msgs = db.query(AgentMessage).filter(AgentMessage.conversation_id == conv.id).all()
        assert len(msgs) == 1
        assert "已通过通知直接确认执行" in msgs[0].content

    # Test skip via short_code (Telegram text fallback `SKIP <short_code>`)
    card2 = engine_mod.create_inline_confirmation(
        owner_hash=OWNER_HASH,
        title="是否切换备用 RSS 源？",
        summary="主源连续超时。",
        operation={"action": "acknowledge_news_digest"},
    )
    skip_res = engine_mod.handle_inline_confirmation_action(
        card2["short_code"],
        action="skip",
        owner_hash=OWNER_HASH,
    )
    assert skip_res["ok"] is True
    assert skip_res["confirmation_state"] == "skipped"


def test_7_normal_search_path_never_triggers_rea(news_env):
    """§8.7 正常 search_configured_news 查询路径在无源失效时不产生任何 REA 调用日志。"""
    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Feed</title>
      <item>
        <title>AAPL launches new M5 Mac Studio</title>
        <link>https://finance.yahoo.com/news/aapl-m5.html</link>
        <pubDate>Thu, 29 Oct 2026 09:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    engine_mod.set_http_fetcher(lambda url, headers, timeout: (200, sample_rss))

    engine_mod.search_configured_news(tickers=["AAPL"], refresh_first=True)
    assert engine_mod.get_rea_audit_log() == []


def test_8_human_disables_source_and_agent_re_enables_or_updates_url(news_env):
    """§8.8 人工把某个源设为 enabled: false 后，Agent 可通过工具再次开启或改写该源 URL。"""
    # sec-atom-optional is disabled in config/news_sources.yaml by default
    listed = engine_mod.list_news_sources()
    sec_entry = next(s for s in listed["sources"] if s["id"] == "sec-atom-optional")
    assert sec_entry["enabled"] is False

    # Agent re-enables via disable_news_source(enabled=True)
    re_enabled = engine_mod.disable_news_source("sec-atom-optional", enabled=True)
    assert re_enabled["ok"] is True
    assert re_enabled["enabled"] is True

    # Agent updates URL via upsert_news_source
    upserted = engine_mod.upsert_news_source(
        {
            "id": "sec-atom-optional",
            "url": "https://www.sec.gov/Archives/edgar/usgaap.rss.xml",
            "enabled": True,
        }
    )
    assert upserted["ok"] is True
    assert upserted["source"]["url"] == "https://www.sec.gov/Archives/edgar/usgaap.rss.xml"
    assert upserted["source"]["health"] == "ok"


def test_9_consecutive_failures_mark_degraded_and_rea_repair_creates_inline_confirm(news_env):
    """§8.9 模拟某源连续失败进入 degraded（保持 enabled: true）后，Agent 定时任务可自动走 REA 修复或切换备源并生成待确认卡，用户在通知卡内联确认后恢复抓取。"""
    # Simulate 3 consecutive HTTP 403 failures on jin10-flash
    engine_mod.set_http_fetcher(lambda url, headers, timeout: (403, "Forbidden"))
    for _ in range(3):
        engine_mod.poll_once(source_ids=["jin10-flash"], force=True)

    listed = engine_mod.list_news_sources()
    jin10 = next(s for s in listed["sources"] if s["id"] == "jin10-flash")
    assert jin10["enabled"] is True
    assert jin10["health"] == "degraded"

    # Diagnose source
    diag = engine_mod.diagnose_news_source("jin10-flash")
    assert diag["ok"] is True
    assert diag["diagnosis"]["health"] == "degraded"
    assert diag["diagnosis"]["consecutive_failures"] >= 3

    # Now let alternate URL succeed when REA probes it
    valid_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Jin10 Alternate</title>
      <item><title>NVDA 财报超预期 1</title><link>https://www.jin10.com/flash/1</link></item>
      <item><title>AAPL 新指引 2</title><link>https://www.jin10.com/flash/2</link></item>
      <item><title>美联储降息预期 3</title><link>https://www.jin10.com/flash/3</link></item>
    </channel></rss>"""
    engine_mod.set_http_fetcher(
        lambda url, headers, timeout: (200, valid_rss) if "jin10.com/flash" in url else (403, "Forbidden")
    )

    repair = engine_mod.repair_news_source_with_rea(
        "jin10-flash",
        require_confirm=True,
        owner_hash=OWNER_HASH,
    )
    assert repair["ok"] is True
    assert repair["status"] == "pending_confirm"
    assert repair["candidate"]["url"] == "https://www.jin10.com/flash"
    assert repair["confirmation"]["confirmation_state"] == "pending_confirm"
    assert len(engine_mod.get_rea_audit_log()) == 1

    # Rate limit check: calling repair_news_source_with_rea a second time within 1 hour is rejected
    rate_limited = engine_mod.repair_news_source_with_rea("jin10-flash", owner_hash=OWNER_HASH)
    assert rate_limited["ok"] is False
    assert rate_limited["error_code"] == "rea_repair_rate_limited"

    # User clicks inline confirm on the notification card -> alternate source is applied and health becomes ok
    card = repair["confirmation"]
    confirmed = engine_mod.handle_inline_confirmation_action(
        card["id"],
        action="confirm",
        action_token=card["action_token"],
        owner_hash=OWNER_HASH,
    )
    assert confirmed["ok"] is True
    assert confirmed["confirmation_state"] == "confirmed"

    after = next(s for s in engine_mod.list_news_sources()["sources"] if s["id"] == "jin10-flash")
    assert after["url"] == "https://www.jin10.com/flash"
    assert after["health"] == "ok"
