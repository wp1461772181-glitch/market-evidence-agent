import json

from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."


def _turn():
    ctx = AgentRuntimeContext("custom", "test", "dummy")
    turn = direct.Turn("What was revenue?", ctx, lambda *_: None, [], {})
    turn.route = "A"
    turn.observations = [{"accepted_evidence": True}]
    turn.citations = [{"ordinal": 1, "quote": SOURCE, "source_type": "official_filing", "source_id": "src-1"}]
    return turn


def _answer(quote):
    claims = {
        "claims": [{
            "id": "c1",
            "statement": "Revenue was 16,128.",
            "citation_ordinal": 1,
            "evidence": {"type": "quote", "evidence_quote": quote},
        }]
    }
    return "Revenue was 16,128 [1].\n" + json.dumps(claims)


def test_flag_off_does_not_record_a_claim_gate(monkeypatch):
    monkeypatch.delenv("CLAIM_QUOTE_GATE", raising=False)
    result = _turn().finish("Revenue was 16,128 [1].")
    assert "claim_gate" not in result["audit"]
    assert "claim_gate_shadow" not in result["audit"]
    assert result["status"] == "succeeded"
    assert "16,128" in result["answer"]


def test_shadow_records_a_bad_quote_while_legacy_keeps_the_prose(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "shadow")
    result = _turn().finish(_answer("This sentence does not appear anywhere in the filing text."))
    assert result["status"] == "succeeded"
    assert "16,128" in result["answer"]
    assert "evidence_quote" not in result["answer"]
    shadow = result["audit"]["claim_gate_shadow"]
    assert "quote_not_found" in shadow["drop_reasons"]
    assert "latency_ms" in shadow
    assert shadow["answer_quality_notes"]["prose_chars"] > 0
    assert "legacy_disagreement" in shadow["answer_quality_notes"]


def test_enforce_drops_a_claim_the_resolved_span_does_not_support(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    claims = {
        "claims": [{
            "id": "c1",
            "statement": "Revenue was 16,182 million.",
            "citation_ordinal": 1,
            "evidence": {"type": "quote", "evidence_quote": "Total net sales were 16,182 for fiscal 2024."},
        }]
    }
    answer = "Revenue was 16,182 million [1].\n" + json.dumps(claims)
    result = _turn().finish(answer)
    assert "16,182" not in result["answer"]
    assert "待核对" not in result["answer"]
    gate = result["audit"]["claim_gate"]
    assert "unsupported_numeric" in gate["drop_reasons"]
    assert result["reason"] == "unsupported_numeric"
