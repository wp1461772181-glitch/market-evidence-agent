import pytest

from app.agent_workflow.claim_units import ClaimParseError, parse_claims


def test_parse_quote_evidence():
    units = parse_claims({
        "claims": [{
            "id": "c1",
            "statement": "Revenue was 16,128 million.",
            "citation_ordinal": 1,
            "evidence": {"type": "quote", "evidence_quote": "Total revenue was 16,128 for the year."},
        }]
    })
    assert len(units) == 1
    assert units[0].evidence.type == "quote"
    assert units[0].evidence.evidence_quote == "Total revenue was 16,128 for the year."
    assert units[0].citation_ordinal == 1


def test_parse_field_evidence_price():
    units = parse_claims({
        "claims": [{
            "id": "c2",
            "statement": "Close was 192.5 on 2024-01-02.",
            "citation_ordinal": 2,
            "evidence": {"type": "field", "fields": {"trading_date": "2024-01-02", "close": 192.5}},
        }]
    })
    assert units[0].evidence.type == "field"
    assert units[0].evidence.fields["close"] == 192.5


def test_parse_flat_field_evidence_for_structured_sources():
    units = parse_claims([{
        "id": "c4",
        "statement": "Revenue was 16128.",
        "citation_ordinal": 3,
        "evidence": {"type": "field", "concept": "Revenue", "period": "2024", "value": 16128},
    }])
    assert units[0].evidence.type == "field"
    assert units[0].evidence.fields == {"concept": "Revenue", "period": "2024", "value": 16128}


def test_reject_missing_evidence():
    with pytest.raises(ClaimParseError):
        parse_claims({"claims": [{"id": "c3", "statement": "x", "citation_ordinal": 1}]})


def test_reject_empty_statement_and_short_quote():
    with pytest.raises(ClaimParseError):
        parse_claims({"claims": [{
            "id": "c5",
            "statement": "   ",
            "citation_ordinal": 1,
            "evidence": {"type": "quote", "evidence_quote": "Total revenue was 16,128 for the year."},
        }]})
    with pytest.raises(ClaimParseError):
        parse_claims({"claims": [{
            "id": "c6",
            "statement": "A claim.",
            "citation_ordinal": "nope",
            "evidence": {"type": "quote", "evidence_quote": "Total revenue was 16,128 for the year."},
        }]})
