"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations


def test_a_headline_does_not_make_another_companys_figure_the_asked_ones():
    from app.agent_web_research import focus_excerpt
    assert "revenue rose 20%" not in focus_excerpt(
        "Samsung revenue rose 20% in 2025.", "Apple revenue news", heading="Apple revenue report")
    assert "Samsung revenue rose 20%" not in focus_excerpt(
        "Apple introduced a phone. Samsung revenue rose 20% in 2025.", "Apple revenue news")
    assert "Microsoft revenue rose" not in focus_excerpt(
        "Apple introduced a phone. Microsoft revenue rose 12% in 2025.", "Apple revenue news")
    text = "Apple introduced a phone. The company revenue rose 20% in 2025."
    assert focus_excerpt(text, "Apple revenue news") == text
