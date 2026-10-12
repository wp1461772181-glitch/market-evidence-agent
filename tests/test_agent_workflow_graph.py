import asyncio

from app.agent_workflow.graph import build_workflow, deterministic_confidence_gate
from app.agent_workflow.state import AgentRuntimeContext


def test_workflow_classifies_and_routes_without_persisting_request_secrets():
    async def classifier(_question):
        return '{"intent":"saved_data","symbol":"aapl"}'

    runtime = AgentRuntimeContext("custom", "model", "sentinel-secret",
                                  owner_hash="owner", conversation_id="11111111-1111-4111-8111-111111111111",
                                  classifier=classifier,
                                  executor=_fixture_executor, answerer=_fixture_answer)
    result = asyncio.run(build_workflow().ainvoke(
        {"question": "show AAPL prices", "messages": [], "observations": [], "citations": [],
         "tool_calls": 0, "decision_steps": 0, "token_usage": 0, "seen_tool_signatures": []},
        {"configurable": {"thread_id": "a" * 64}}, context=runtime))
    assert result["intent"] == "saved_data"
    assert result["symbol_focus"] == "AAPL"
    assert result["tool_calls"] == 1
    assert result["next_action"] == "final"
    assert "record_log" in result["node_log"]
    assert "sentinel-secret" not in repr(result)


async def _fixture_executor(name, args):
    return {"ok": True, "data": [{"symbol": args.get("symbol"), "close": 100}], "source": "saved"}


async def _fixture_answer(_messages):
    yield "AAPL close is available."


def test_deterministic_gate_requires_nonempty_real_sources():
    assert deterministic_confidence_gate("revenue AAPL", {"ok": True, "data": [], "source": "knowledge"})[0] is False
    assert deterministic_confidence_gate("revenue AAPL", {"ok": True, "data": [{"excerpt": "Revenue was $1bn"}], "source": "knowledge"})[0] is False
    assert deterministic_confidence_gate("revenue AAPL", {"ok": True, "data": [{"excerpt": "AAPL revenue was $1bn", "source_url": "https://example.com", "id": "source"}], "source": "knowledge"})[0] is True


def test_gate_rejects_symbol_only_for_revenue_and_does_not_mutate_manifest():
    rows = [{"id": "1", "symbol": "AAPL", "title": "AAPL company overview", "source_url": "https://example.com"}]
    manifest = [{"ordinal": 1, "source_url": "https://example.com"}]
    original = list(manifest)
    passed, reason = deterministic_confidence_gate("AAPL 的本季度营收是多少？", {"ok": True, "data": rows}, manifest)
    assert not passed and reason.startswith("requested_metric_missing")
    assert manifest == original


def test_gate_checks_metric_period_entity_and_only_requires_dates_for_web_freshness():
    q = "AAPL 2025 年营收是多少？"
    saved = {"ok": True, "source": "saved filing", "data": [{"title": "AAPL 2025 年营收", "excerpt": "AAPL 2025 年营收 revenue was $1bn", "source_url": "https://example.com"}]}
    assert deterministic_confidence_gate(q, saved)[0]
    saved["data"][0]["title"] = "AAPL financial overview"
    saved["data"][0]["excerpt"] = "AAPL 2024 年营收 revenue was $1bn"
    assert deterministic_confidence_gate(q, saved)[1] == "requested_period_missing"
    web = {"ok": True, "source": "public web search and fetched page excerpts", "data": [{"title": "AAPL 2025 revenue", "excerpt": "AAPL 2025 revenue was $1bn", "url": "https://example.com"}]}
    assert deterministic_confidence_gate("latest AAPL 2025 revenue", web)[1] == "publisher_date_unavailable"


def test_mixed_web_publish_dates_do_not_pass_latest_claim():
    rows=[{"url":"https://example.com/a","excerpt":"AAPL recent revenue update","published_at":"2026-10-01T00:00:00+00:00"},
          {"url":"https://example.com/b","excerpt":"AAPL recent revenue update","published_at":None}]
    result={"ok":True,"source":"public web search and fetched page excerpts","data":rows}
    assert deterministic_confidence_gate("latest AAPL revenue",result)[1]=="publisher_date_unavailable"
