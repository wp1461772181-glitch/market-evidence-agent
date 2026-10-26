import json

from app.agent_workflow.claim_review import apply_claim_gate
from app.jev_claim_support import ClaimSupportResult

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."
CITATIONS = [{"ordinal": 1, "quote": SOURCE, "source_type": "official_filing"}]


def _answer():
    claims = {
        "claims": [{
            "id": "c1",
            "statement": "Revenue was 16,128.",
            "citation_ordinal": 1,
            "evidence": {"type": "quote", "evidence_quote": "Total net sales were 16,128 for fiscal 2024."},
        }]
    }
    return "Revenue was 16,128 [1].\n" + json.dumps(claims)


def _result(choice, status="ok", supports=0.9):
    rest = round(1 - supports, 4)
    return ClaimSupportResult(
        choice,
        {"supports": supports, "does_not_support": rest if choice != "does_not_support" else supports, "unrelated": 0.0},
        status,
    )


def test_support_off_makes_no_call():
    calls = []

    def support(items, **kwargs):
        calls.append(items)
        return []

    apply_claim_gate(_answer(), CITATIONS, mode="enforce", support_mode="off", support_fn=support)
    assert calls == []


def test_enforce_drops_only_explicit_does_not_support():
    def support(items, **kwargs):
        assert len(items) == 1
        return [_result("does_not_support", supports=0.8)]

    application = apply_claim_gate(
        _answer(), CITATIONS, mode="enforce", support_mode="enforce", support_fn=support,
    )
    assert "does_not_support" in application.display_issues
    assert "16,128" not in application.kept_prose
    assert application.audit["jev_support_calls"] == 1


def test_timeout_keeps_the_quote_gated_claim():
    def support(items, **kwargs):
        return [ClaimSupportResult(None, {}, "timeout")]

    application = apply_claim_gate(
        _answer(), CITATIONS, mode="enforce", support_mode="enforce", support_fn=support,
    )
    assert "16,128" in application.kept_prose
    assert application.display_issues == []
    assert application.audit["semantic_unchecked"][0]["status"] == "timeout"


def test_shadow_records_does_not_support_without_dropping():
    def support(items, **kwargs):
        return [_result("does_not_support", supports=0.2)]

    application = apply_claim_gate(
        _answer(), CITATIONS, mode="shadow", support_mode="shadow", support_fn=support,
    )
    assert "16,128" in application.prose
    assert application.display_issues == []
    assert application.audit["jev_choices"][0]["choice"] == "does_not_support"


def test_unrelated_and_low_confidence_stay_unchecked():
    def unrelated(items, **kwargs):
        return [_result("unrelated", supports=0.1)]

    application = apply_claim_gate(
        _answer(), CITATIONS, mode="enforce", support_mode="enforce", support_fn=unrelated,
    )
    assert "16,128" in application.kept_prose
    assert application.audit["semantic_unchecked"][0]["status"] == "unrelated"

    def low(items, **kwargs):
        return [_result("supports", supports=0.4)]

    application = apply_claim_gate(
        _answer(), CITATIONS, mode="enforce", support_mode="enforce", support_fn=low, support_threshold=0.7,
    )
    assert "16,128" in application.kept_prose
    assert application.audit["semantic_unchecked"][0]["status"] == "below_threshold"


def test_per_turn_cap_batches_one_request_and_skips_the_rest():
    claims = {
        "claims": [
            {
                "id": "c1",
                "statement": "Revenue was 16,128.",
                "citation_ordinal": 1,
                "evidence": {"type": "quote", "evidence_quote": "Total net sales were 16,128 for fiscal 2024."},
            },
            {
                "id": "c2",
                "statement": "Operating income follows.",
                "citation_ordinal": 1,
                "evidence": {"type": "quote", "evidence_quote": "Total net sales were 16,128 for fiscal 2024. Operating income follows."},
            },
        ]
    }
    seen = []

    def support(items, **kwargs):
        seen.append(items)
        return [_result("supports") for _ in items]

    answer = "Revenue was 16,128 [1].\n" + json.dumps(claims)
    application = apply_claim_gate(
        answer, CITATIONS, mode="shadow", support_mode="shadow", support_fn=support, max_per_turn=1,
    )
    assert len(seen) == 1 and len(seen[0]) == 1
    assert application.audit["jev_support_calls"] == 1
    assert application.audit["jev_skipped_reasons"][0]["reason"] == "per_turn_cap"
