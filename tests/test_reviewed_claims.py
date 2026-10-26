import os

from app.agent_workflow.claim_units import parse_claims
from app.agent_workflow.evidence import reviewed_answer, reviewed_claims

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."
CITATIONS = [{"ordinal": 1, "quote": SOURCE, "source_type": "official_filing"}]


def _quote_claim(statement, quote, ordinal=1, claim_id="c1"):
    return parse_claims({
        "claims": [{
            "id": claim_id,
            "statement": statement,
            "citation_ordinal": ordinal,
            "evidence": {"type": "quote", "evidence_quote": quote},
        }]
    })


def test_not_found_drops_without_pending():
    claims = _quote_claim(
        "Revenue was 16,128 million.",
        "This sentence does not appear anywhere in the filing text.",
    )
    accepted, issues = reviewed_claims(claims, CITATIONS, {1: SOURCE})
    assert accepted == []
    assert issues[0]["reason"] == "quote_not_found"
    assert "pending" not in issues[0]["reason"]
    assert "待核对" not in issues[0]["reason"]


def test_short_quote_drops():
    claims = _quote_claim("Revenue was 16,128 million.", "16,128")
    accepted, issues = reviewed_claims(claims, CITATIONS, {1: SOURCE}, min_chars=24)
    assert accepted == []
    assert issues[0]["reason"] == "quote_too_short"


def test_fuzzy_resolved_span_rejects_typo_digits():
    claims = _quote_claim(
        "Revenue was 16,182 million.",
        "Total net sales were 16,182 for fiscal 2024.",
    )
    accepted, issues = reviewed_claims(claims, CITATIONS, {1: SOURCE})
    assert accepted == []
    assert issues[0]["reason"] == "unsupported_numeric"


def test_fuzzy_bind_keeps_claim_when_statement_matches_source_span():
    claims = _quote_claim(
        "Revenue was 16,128 million.",
        "Total net sales were 16,182 for fiscal 2024.",
    )
    accepted, issues = reviewed_claims(claims, CITATIONS, {1: SOURCE})
    assert issues == []
    assert len(accepted) == 1
    assert "16,128" in accepted[0].evidence.evidence_quote
    assert "16,182" not in accepted[0].evidence.evidence_quote


def test_field_evidence_price_ok_and_bad_ordinal_drops():
    claims = parse_claims({
        "claims": [
            {
                "id": "price",
                "statement": "Close was 192.5 on 2024-01-02.",
                "citation_ordinal": 2,
                "evidence": {"type": "field", "fields": {"trading_date": "2024-01-02", "close": 192.5}},
            },
            {
                "id": "missing",
                "statement": "Close was 192.5.",
                "citation_ordinal": 9,
                "evidence": {"type": "field", "fields": {"close": 192.5}},
            },
        ]
    })
    citations = [{
        "ordinal": 2,
        "source_type": "saved_price",
        "figures": "trading_date 2024-01-02 close 192.5",
        "quote": "192.5",
    }]
    accepted, issues = reviewed_claims(claims, citations, {})
    assert [item.id for item in accepted] == ["price"]
    assert issues[0]["reason"] == "bad_ordinal"


def test_bare_margin_requires_calculate_but_calculation_field_passes():
    filing = "Gross margin was 46.2 percent for the fiscal year ended September 28."
    bare = _quote_claim("Gross margin was 46.2%.", filing)
    accepted, issues = reviewed_claims(
        bare,
        [{"ordinal": 1, "quote": filing, "source_type": "official_filing"}],
        {1: filing},
    )
    assert accepted == []
    assert issues[0]["reason"] == "derived_requires_calculate"

    calculated = parse_claims({
        "claims": [{
            "id": "margin",
            "statement": "Gross margin was 46.2%.",
            "citation_ordinal": 3,
            "evidence": {"type": "field", "value": 46.2},
        }]
    })
    citations = [{
        "ordinal": 3,
        "source_type": "calculation",
        "quote": '{"value": "46.20"}',
        "calculation": {"value": "46.20"},
    }]
    accepted, issues = reviewed_claims(calculated, citations, {})
    assert issues == []
    assert accepted[0].id == "margin"


def test_field_mismatch_drops():
    claims = parse_claims({
        "claims": [{
            "id": "bad",
            "statement": "Close was 999.",
            "citation_ordinal": 1,
            "evidence": {"type": "field", "fields": {"close": 999}},
        }]
    })
    citations = [{"ordinal": 1, "source_type": "saved_price", "figures": "close 192.5", "quote": "192.5"}]
    accepted, issues = reviewed_claims(claims, citations, {})
    assert accepted == []
    assert issues[0]["reason"] == "field_mismatch"


def test_reviewed_answer_is_unchanged_when_claim_gate_flag_is_set(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    quote = "Total net sales were 16,128 million for fiscal 2024."
    answer = "Revenue was 16,128 million [1]."
    kept, issues = reviewed_answer(
        answer,
        [{"ordinal": 1, "quote": quote, "source_type": "official_filing"}],
        require_citations=True,
    )
    assert os.environ["CLAIM_QUOTE_GATE"] == "enforce"
    assert issues == []
    assert "16,128" in kept
