"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
from app.agent_workflow.graph import deterministic_confidence_gate

def test_deterministic_gate_requires_nonempty_real_sources():
    assert deterministic_confidence_gate("revenue AAPL", {"ok": True, "data": [], "source": "knowledge"})[0] is False
    assert deterministic_confidence_gate("revenue AAPL", {"ok": True, "data": [{"excerpt": "Revenue was $1bn"}], "source": "knowledge"})[0] is False
    assert deterministic_confidence_gate("revenue AAPL", {"ok": True, "data": [{"excerpt": "AAPL revenue was $1bn", "source_url": "https://example.com", "id": "source"}], "source": "knowledge"})[0] is True


def test_gate_rejects_symbol_only_for_revenue_and_does_not_mutate_manifest():
    rows = [{"id": "1", "symbol": "AAPL", "title": "AAPL company overview", "source_url": "https://example.com"}]
    manifest = [{"ordinal": 1, "source_url": "https://example.com"}]
    original = list(manifest)
    passed, reason = deterministic_confidence_gate("AAPL 的本季度营收是多少？", {"ok": True, "data": rows}, manifest)
    assert not passed and reason.startswith("requested_metric_missing")
    assert manifest == original


def test_gate_checks_metric_period_entity_and_only_requires_dates_for_web_freshness():
    q = "AAPL 2025 年营收是多少？"
    saved = {"ok": True, "source": "saved filing", "data": [{"title": "AAPL 2025 年营收", "excerpt": "AAPL 2025 年营收 revenue was $1bn", "source_url": "https://example.com"}]}
    assert deterministic_confidence_gate(q, saved)[0]
    saved["data"][0]["title"] = "AAPL financial overview"
    saved["data"][0]["excerpt"] = "AAPL 2024 年营收 revenue was $1bn"
    assert deterministic_confidence_gate(q, saved)[1] == "requested_period_missing"
    web = {"ok": True, "source": "public web search and fetched page excerpts", "data": [{"title": "AAPL 2025 revenue", "excerpt": "AAPL 2025 revenue was $1bn", "url": "https://example.com"}]}
    assert deterministic_confidence_gate("latest AAPL 2025 revenue", web,days_back=7)[1] == "publisher_date_unavailable"


def test_mixed_web_publish_dates_do_not_pass_latest_claim():
    rows=[{"url":"https://example.com/a","excerpt":"AAPL recent revenue update","published_at":"2026-10-01T00:00:00+00:00"},
          {"url":"https://example.com/b","excerpt":"AAPL recent revenue update","published_at":None}]
    result={"ok":True,"source":"public web search and fetched page excerpts","data":rows}
    assert deterministic_confidence_gate("latest AAPL revenue",result,days_back=7)[1]=="publisher_date_unavailable"


def test_gate_accepts_fetched_body_for_explicit_https_page_read():
    result = {"ok": True, "source": "public webpage excerpt", "data": [{
        "url": "https://example.com/", "title": "Example Domain",
        "excerpt": "This domain is for use in documentation examples without needing permission. Avoid use in operations.",
        "fetched_at": "2026-10-12T00:00:00+00:00", "published_at": None,
        "body_provenance": "public_page_fetch", "truncated": False,
    }]}
    question = "请读取 https://example.com/ 并用一句话概括网页内容，注明来源。"
    assert deterministic_confidence_gate(question, result) == (True, "heuristic_coverage_pass")


def test_gate_keeps_topic_outside_explicit_https_url():
    result = {"ok": True, "source": "public webpage excerpt", "data": [{
        "url": "https://example.com/", "title": "Example Domain",
        "excerpt": "This domain is for use in documentation examples without needing permission.",
        "fetched_at": "2026-10-12T00:00:00+00:00", "body_provenance": "public_page_fetch",
    }]}
    question = "请读取 https://example.com/ 并概括 superconductivity 的内容。"
    assert deterministic_confidence_gate(question, result) == (False, "weak_question_coverage")


def test_web_news_gate_accepts_fetched_amd_news_in_english_and_chinese():
    result = {"ok": True, "source": "public web search and fetched page excerpts", "data": [{
        "url": "https://finance.yahoo.com/news/amd-results-example.html",
        "title": "AMD announces new data center products",
        "excerpt": "AMD announced new data center products and discussed customer demand in its company update.",
        "published_at": "2026-10-11T14:00:00+00:00",
        "fetched_at": "2026-10-12T00:00:00+00:00",
        "body_provenance": "public_page_fetch",
    }]}
    questions = (
        "Search for latest AMD company news. List the source and publication date, and use only verified page content.",
        "搜索 AMD 最新公司新闻，列出来源和发布日期，只使用已核实的网页正文。",
    )
    for question in questions:
        assert deterministic_confidence_gate(question, result, verified_symbols=["AMD"]) == (True, "heuristic_coverage_pass")


def test_web_news_gate_rejects_unrelated_fetched_bodies_even_with_dates():
    result = {"ok": True, "source": "public web search and fetched page excerpts", "data": [{
        "url": "https://example.com/market-news",
        "title": "Company news today",
        "excerpt": "The company announced a new product. The page includes a source and publication date.",
        "published_at": "2026-10-11T14:00:00+00:00",
        "fetched_at": "2026-10-12T00:00:00+00:00",
        "body_provenance": "public_page_fetch",
        "search_snippet": "AMD latest company news",
    }]}
    question = "Search for latest AMD company news. List the source and publication date, and use only verified page content."
    assert deterministic_confidence_gate(question, result, verified_symbols=["AMD"]) == (False, "requested_entity_missing")
    assert deterministic_confidence_gate(question, result) == (False, "requested_entity_missing")


def test_web_news_still_requires_requested_metric_period_and_publisher_date():
    result = {"ok": True, "source": "public web search and fetched page excerpts", "data": [{
        "url": "https://example.com/amd", "title": "AMD company update",
        "excerpt": "AMD discussed new processors.",
        "fetched_at": "2026-10-12T00:00:00+00:00", "body_provenance": "public_page_fetch",
    }]}
    assert deterministic_confidence_gate("latest AMD 2025 revenue", result, verified_symbols=["AMD"],days_back=7)[1] == "requested_metric_missing:revenue"
    result["data"][0]["excerpt"] = "AMD 2024 revenue rose."
    assert deterministic_confidence_gate("latest AMD 2025 revenue", result, verified_symbols=["AMD"],days_back=7)[1] == "requested_period_missing"
    result["data"][0]["excerpt"] = "AMD 2025 revenue rose."
    assert deterministic_confidence_gate("latest AMD 2025 revenue", result, verified_symbols=["AMD"],days_back=7)[1] == "publisher_date_unavailable"
