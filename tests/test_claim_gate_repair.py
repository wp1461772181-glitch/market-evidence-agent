import asyncio
import json

from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."


def test_a_quote_that_does_not_match_word_for_word_is_delivered_without_a_repair(monkeypatch):
    # Levi 2026-10-28: 换个说法对不上 is not a model error; the figure is in the source, so nothing is cut.
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
    assert seen == []
    assert result["status"] == "succeeded"
    assert result["answer"] == "Revenue was 16,128 [1]."
    assert result["audit"]["claim_gate"]["drop_reasons"] == ["quote_not_found"]
    assert result["audit"]["claim_gate"]["warned_lines"][0]["reason"] == "quote_not_found"
