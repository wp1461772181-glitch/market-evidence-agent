"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
from app.agent_workflow.semantic import hydrate_legacy_web_sets
from app.agent_workflow.semantic import legacy_web_source_choices

def _result(symbol="AAPL"):
    return {"ok": True, "source": "public web search and fetched page excerpts", "data": [
        {"title": f"{symbol} revenue report", "url": "https://example.com/a",
         "excerpt": f"{symbol} revenue grew with services sales and product demand." * 2,
         "published_at": "2026-10-12T00:00:00Z", "fetched_at": "2026-10-13T00:00:00Z",
         "body_provenance": "public_page_fetch"},
        {"title": f"{symbol} revenue analysis", "url": "https://example.com/b",
         "excerpt": f"{symbol} revenue in the latest quarter rose as the company reported sales." * 2,
         "published_at": "2026-10-12T00:00:00Z", "fetched_at": "2026-10-13T00:00:00Z",
         "body_provenance": "public_page_fetch"},
    ]}


def _citations(result):
    return [{"ordinal": n, "source_type": "web", "source_url": row["url"],
             "title": row["title"], "quote": row["excerpt"], "observed_at": row["fetched_at"],
             "published_at": row["published_at"]}
            for n, row in enumerate(result["data"], 1)]


def test_legacy_web_sidecar_requires_completed_gate_and_trace():
    result = _result()
    protocol = {"audit": {"gate_passed": True, "tasks": [{"route": "public_web_research", "status": "succeeded"}]},
                "tool_trace": [{"name": "web_research", "status": "complete", "result": {"ok": True, "count": 2}}],
                "citations": _citations(result)}
    row = {"role": "assistant", "status": "complete", "turn_id": "legacy-turn", "tool_calls": protocol}
    saved = hydrate_legacy_web_sets([row], owner="owner", conversation="chat")
    assert len(saved) == 1 and len(saved[0]["items"]) == 2
    row["tool_calls"]["audit"]["gate_passed"] = False
    assert hydrate_legacy_web_sets([row], owner="owner", conversation="chat") == []
    # Unproven content stays untrusted, but the exact safe URLs can be offered
    # for a fresh read after the user picks one.
    choices = legacy_web_source_choices([row])
    assert len(choices) == 2 and choices[0]["url"] == "https://example.com/a"
