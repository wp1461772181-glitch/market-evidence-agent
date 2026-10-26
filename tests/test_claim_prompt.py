from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."


def _turn():
    ctx = AgentRuntimeContext("custom", "test", "dummy")
    turn = direct.Turn("What was revenue?", ctx, lambda *_: None, [], {})
    turn.route = "A"
    turn.observations = [{"accepted_evidence": True}]
    turn.citations = [{"ordinal": 1, "quote": SOURCE, "source_type": "official_filing"}]
    return turn


def test_prompt_requests_claims_only_when_the_gate_is_enabled(monkeypatch):
    monkeypatch.delenv("CLAIM_QUOTE_GATE", raising=False)
    off = _turn().tool_messages(closing=True)[0]["content"]
    assert "evidence_quote" not in off
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "shadow")
    shadow = _turn().tool_messages(closing=True)[0]["content"]
    assert "evidence_quote" in shadow
    assert '"type":"field"' in shadow or '"type": "field"' in shadow
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    assert "evidence_quote" in _turn().tool_messages(closing=True)[0]["content"]


def test_enforce_without_claims_drops_the_factual_sentence(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    result = _turn().finish("Revenue was 16,128 [1].")
    assert result["reason"] == "claims_missing"
    assert "16,128" not in result["answer"]
    assert result["audit"]["claim_gate"]["claims_missing"] is True
    assert "待核对" not in result["answer"]
