from app.agent_workflow.claim_numeric import (
    field_evidence_matches,
    header_context,
    numbers_supported,
)
from app.agent_workflow.claim_units import FieldEvidence


def test_reject_when_statement_digits_missing_from_resolved_span():
    bad = numbers_supported(
        "Revenue was 16,182 million.",
        "Total net sales were 16,128 for fiscal 2024.",
    )
    assert bad  # the resolved span's 16,128 does not support 16,182


def test_accept_when_digits_in_resolved_span():
    assert numbers_supported(
        "Revenue was 16,128 million.",
        "Total net sales were 16,128 for fiscal 2024.",
    ) == []


def test_model_quote_digits_are_not_consulted():
    """Only the resolved span counts. A header is unit context, not a second number source."""
    bad = numbers_supported(
        "Revenue was 16,182 million.",
        "Total net sales were 16,128 for fiscal 2024.",
        header_context="In millions\nThe model wrote 16,182 nearby.",
    )
    assert any(abs(value - 16182000000.0) < 1 for value in bad)


def test_header_context_walk_up():
    text = "In millions\nItem A\nTotal net sales were 16,128 for fiscal 2024.\n"
    start = text.index("Total net sales")
    ctx = header_context(text, start, lines_up=8)
    assert "In millions" in ctx
    assert "16,128" not in ctx


def test_millions_header_allows_yi_scaling():
    span = "Total net sales were 16,128 for fiscal 2024."
    header = "In millions"
    assert numbers_supported("营收为 161.28 亿美元。", span, header_context=header) == []
    assert numbers_supported("营收为 161.28 亿美元。", span, header_context="")


def test_time_and_duration_are_not_numeric_claims():
    span = "The call started at 15:00 and covered the last 15 days of trading."
    assert numbers_supported("电话开始于 15:00，覆盖最近15天。", span) == []
    assert numbers_supported("我们在最近15天内分析了前3项。", "Apple published its statements recently.") == []


def test_field_evidence_matches_price_citation():
    citation = {
        "source_type": "saved_price",
        "figures": "trading_date 2024-01-02 close 192.5",
        "quote": "192.5",
    }
    evidence = FieldEvidence("field", {"trading_date": "2024-01-02", "close": 192.5})
    assert field_evidence_matches(evidence, citation)
    assert not field_evidence_matches(FieldEvidence("field", {"close": 999}), citation)


def test_field_evidence_matches_calculate_and_xbrl_values():
    calculation = {
        "source_type": "calculation",
        "quote": '{"value": "46.20"}',
        "calculation": {"value": "46.20", "expression": "a/b*100"},
    }
    assert field_evidence_matches(FieldEvidence("field", {"value": 46.2}), calculation)
    assert not field_evidence_matches(FieldEvidence("field", {"value": 40}), calculation)
    filing = {
        "source_type": "xbrl",
        "figures": "concept Revenues period 2024 value 16128",
        "quote": "Revenues 2024 16,128",
    }
    evidence = FieldEvidence("field", {"concept": "Revenues", "period": "2024", "value": 16128})
    assert field_evidence_matches(evidence, filing)
