import json
from pathlib import Path

from app.agent_workflow.claim_review import apply_claim_gate
from app.agent_workflow.claim_units import parse_claims
from app.agent_workflow.evidence import reviewed_claims

FIXTURES = Path(__file__).parent / "fixtures" / "claim_gate"


def test_phase1_acceptance_fixtures():
    paths = sorted(FIXTURES.glob("*.json"))
    assert len(paths) >= 6
    for path in paths:
        case = json.loads(path.read_text())
        citations = case["citations"]
        bodies = {}
        for citation in citations:
            if citation.get("quote"):
                bodies[citation["ordinal"]] = citation["quote"]
        accepted, issues = reviewed_claims(parse_claims(case["claims"]), citations, bodies)
        assert [item.id for item in accepted] == case["expect_accepted"], path.name
        assert [item["reason"] for item in issues] == case["expect_reasons"], path.name


def test_shadow_pack_records_latency_and_quality_not_only_hit_rate(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "shadow")
    case = json.loads((FIXTURES / "digit_typo.json").read_text())
    prose = "Revenue was 16,128 [1].\n" + json.dumps(case["claims"])
    application = apply_claim_gate(prose, case["citations"], mode="shadow")
    notes = application.audit["answer_quality_notes"]
    assert "latency_ms" in application.audit
    assert notes["prose_chars"] > 0
    assert "claims_missing" in notes
    assert application.audit["drop_reasons"] == ["unsupported_numeric"]
