import asyncio
import json

from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."


def test_repair_asks_for_a_verbatim_quote_and_calculate(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    seen = []

    def decide(messages, tools):
        seen.append(messages)
        claims = {
            "claims": [{
                "id": "c1",
                "statement": "Revenue was 16,128.",
                "citation_ordinal": 1,
                "evidence": {"type": "quote", "evidence_quote": "Total net sales were 16,128 for fiscal 2024."},
            }]
        }
        return {"content": "Revenue was 16,128 [1].\n" + json.dumps(claims)}

    async def emit(_event):
        return None

    ctx = AgentRuntimeContext("custom", "test", "dummy", decider=decide)
    turn = direct.Turn("What was revenue?", ctx, emit, [], {})
    turn.route = "A"
    turn.observations = [{"accepted_evidence": True}]
    turn.citations = [{"ordinal": 1, "quote": SOURCE, "source_type": "official_filing"}]
    bad = {
        "claims": [{
            "id": "c1",
            "statement": "Revenue was 16,128.",
            "citation_ordinal": 1,
            "evidence": {"type": "quote", "evidence_quote": "This sentence does not appear anywhere in the filing text."},
        }]
    }
    result = asyncio.run(turn.finish_answer("Revenue was 16,128 [1].\n" + json.dumps(bad)))
    assert seen, "enforce bind failure should ask for a repair"
    blob = json.dumps(seen[0], ensure_ascii=False)
    assert "quote_not_found" in blob
    assert "连续出现的原文" in blob
    assert "calculate" in blob
    assert "不要写成待核对" in blob
    assert result["status"] == "succeeded"
    assert "16,128" in result["answer"]
    assert "待核对" not in result["answer"]
