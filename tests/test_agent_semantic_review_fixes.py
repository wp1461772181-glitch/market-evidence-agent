"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations

SHORT_MIXED = ("Apple introduced a new iPhone. Samsung reported strong demand for its phones. "
               "The company revenue rose 20% in 2025.")


def test_a_short_page_is_held_to_the_same_company_and_metric_rule():
    """RED (review finding 6): a page that fits the bound came back unchanged, so a metric sentence
    that followed another company's sentence was handed to the gate as Apple's."""
    from app.agent_web_research import focus_excerpt
    from app.agent_workflow.graph import deterministic_confidence_gate
    out = focus_excerpt(SHORT_MIXED, "Apple revenue news")
    assert "Apple introduced a new iPhone." in out and "revenue rose 20%" not in out
    shown = {"ok": True, "source": "public web search and fetched page excerpts",
             "data": [{"title": "Apple news", "url": "https://example.com/a", "excerpt": out,
                       "published_at": "2026-10-12T00:00:00Z"}]}
    assert deterministic_confidence_gate("Apple revenue news", shown, [], ["AAPL"], company="Apple") == (
        False, "requested_metric_missing:revenue")


def test_a_short_page_with_the_company_beside_its_metric_sentence_is_unchanged():
    """GUARD."""
    from app.agent_web_research import focus_excerpt
    text = "Apple introduced a new iPhone. The company revenue rose 20% in 2025."
    assert focus_excerpt(text, "Apple revenue news") == text
    assert focus_excerpt("Apple revenue rose 6%.", "Apple revenue") == "Apple revenue rose 6%."
    assert focus_excerpt(SHORT_MIXED, "latest news") == SHORT_MIXED            # no metric asked: no rule


def test_the_title_that_names_the_company_stands_before_the_first_sentence():
    """A snippet of a page whose headline names Apple and whose first sentence only says 'revenue'
    belongs to Apple; the headline is not part of the text, so it is passed along."""
    from app.agent_web_research import _focus_source, focus_excerpt
    text = "Revenue rose 20% in 2025. The company also announced a buyback."
    assert focus_excerpt(text, "Apple revenue news") == "The company also announced a buyback."
    assert focus_excerpt(text, "Apple revenue news", heading="Apple results: services grow") == text
    source = {"excerpt": SHORT_MIXED, "title": "Samsung and Apple at the same show"}
    _focus_source(source, "Apple revenue news")
    assert "revenue rose 20%" not in source["excerpt"] and source["truncated"] is True
