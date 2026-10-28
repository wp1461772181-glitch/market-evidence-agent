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


def test_enforce_without_claims_keeps_a_sourced_sentence_and_records_it(monkeypatch):
    # Levi 2026-10-28: a missing claims block is a rewording, not an error; the figure is in the source.
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    result = _turn().finish("Revenue was 16,128 [1].")
    assert result["status"] == "succeeded" and result["answer"] == "Revenue was 16,128 [1]."
    assert result["audit"]["claim_gate"]["claims_missing"] is True
    assert result["audit"]["claim_gate"]["warned_lines"] == [{"line": "Revenue was 16,128 [1].", "reason": "claims_missing"}]


def test_enforce_still_drops_a_figure_no_evidence_holds(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    result = _turn().finish("Revenue was 16,128 [1].\nOperating income was 99,999 [1].")
    assert "16,128" in result["answer"] and "99,999" not in result["answer"]
    assert result["reason"] == "unsupported_numeric" and result["status"] == "partially_succeeded"
    assert "待核对" not in result["answer"]
