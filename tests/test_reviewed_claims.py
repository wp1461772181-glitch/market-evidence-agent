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


def test_computed_margin_requires_calculate_but_directly_reported_margin_passes():
    raw_cells = "Revenue was 100.0 and cost of sales was 53.8 for the fiscal year."
    computed = _quote_claim("Gross margin was 46.2%.", raw_cells)
    accepted, issues = reviewed_claims(
        computed,
        [{"ordinal": 1, "quote": raw_cells, "source_type": "official_filing"}],
        {1: raw_cells},
    )
    assert accepted == []
    assert issues[0]["reason"] == "derived_requires_calculate"

    reported = "Gross margin was 46.2 percent for the fiscal year ended September 28."
    direct = _quote_claim("Gross margin was 46.2%.", reported)
    accepted, issues = reviewed_claims(
        direct,
        [{"ordinal": 1, "quote": reported, "source_type": "official_filing"}],
        {1: reported},
    )
    assert issues == []
    assert [item.id for item in accepted] == ["c1"]

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


def test_number_rows_carries_analysis_metadata_and_verified_quotes():
    from app.agent_workflow.claim_review import collect_source_bodies

    summary = "版本 2 对英特尔最新季度经营情况作了更完整的梳理。"
    quote = "总资产为 2,024.39 亿美元，股东权益为 875.42 亿美元。"
    row = {
        "id": "analysis-version-2",
        "source_id": "intc-filing",
        "source_type": "official_filing",
        "version_no": 2,
        "created_at": "2026-10-26T02:17:00+00:00",
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "summary": summary,
        "evidence_quotes": [quote],
    }
    citations = []
    from app.agent_workflow.evidence import number_rows

    number_rows("query_material_analysis", [row], citations, {})
    assert citations[0]["fields"]["version_no"] == 2
    assert citations[0]["fields"]["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert citations[0]["evidence_quotes"] == [quote]

    claims = parse_claims({
        "claims": [
            {
                "id": "version",
                "statement": "第 2 版生成于 2026-10-26 02:17。",
                "citation_ordinal": 1,
                "evidence": {"type": "field", "fields": {"version_no": 2}},
            },
            {
                "id": "assets",
                "statement": "总资产为 2,024.39 亿美元。",
                "citation_ordinal": 1,
                "evidence": {"type": "quote", "evidence_quote": quote},
            },
        ]
    })
    accepted, issues = reviewed_claims(claims, citations, {})
    assert issues == []
    assert [item.id for item in accepted] == ["version", "assets"]

    bodies, fallbacks = collect_source_bodies(
        citations,
        [{"result": {"data": [{**row, "evidence_quotes": [quote]}]}}],
    )
    assert quote in bodies[1]
    assert fallbacks == []


def test_analysis_bodies_are_selected_by_analysis_id_not_shared_source_id():
    from app.agent_workflow.claim_review import collect_source_bodies
    from app.agent_workflow.evidence import number_rows

    rows = [
        {
            "source_id": "intc-filing",
            "analysis_id": "analysis-version-2",
            "source_type": "official_filing",
            "version_no": 2,
            "summary": "版本 2 摘要。",
            "evidence_quotes": ["版本 2 的资产为 2,024.39 亿美元。"],
        },
        {
            "source_id": "intc-filing",
            "analysis_id": "analysis-version-1",
            "source_type": "official_filing",
            "version_no": 1,
            "summary": "版本 1 摘要。",
            "evidence_quotes": ["版本 1 的资产为 1,900.00 亿美元。"],
        },
    ]
    citations = []
    number_rows("query_material_analysis", rows, citations, {})
    bodies, fallbacks = collect_source_bodies(citations, [{"result": {"data": rows}}])
    assert "版本 2 的资产" in bodies[1] and "版本 1 的资产" not in bodies[1]
    assert "版本 1 的资产" in bodies[2] and "版本 2 的资产" not in bodies[2]
    assert fallbacks == []
