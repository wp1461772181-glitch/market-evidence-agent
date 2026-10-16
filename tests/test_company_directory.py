"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
from app.agent_workflow.graph import deterministic_confidence_gate

def _result(text, title="Market news"):
    return {"ok": True, "source": "public web search and fetched page excerpts", "source_type": "web",
            "data": [{"title": title, "url": "https://example.com/a", "excerpt": text,
                      "published_at": "2026-10-12T00:00:00Z"}]}


def test_the_gate_wants_the_asked_company_stated_by_the_page():
    question = "latest news about Micron"
    about = _result("Micron (MU) shares fell after the results; the company guided lower.")
    other = _result("The community reacted to the music festival results with strong sales.")
    assert deterministic_confidence_gate(question, about, verified_symbols=["MU"])[0] is True
    passed, reason = deterministic_confidence_gate(question, other, verified_symbols=["MU"])
    assert (passed, reason) == (False, "requested_entity_missing")

def test_no_static_company_ticker_maps_outside_the_optional_display_directory():
    import ast,re
    from pathlib import Path
    violations=[]
    for path in (Path(__file__).resolve().parents[1]/'app').rglob('*.py'):
        if path.name=='company_directory.py':continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node,ast.Dict):continue
            mapped=[]
            for key,value in zip(node.keys,node.values):
                if not isinstance(key,ast.Constant) or not isinstance(key.value,str) or not re.fullmatch(r'[A-Z]{1,5}(?:\.[A-Z]{1,2})?',key.value):continue
                words=[s.value for s in ast.walk(value) if isinstance(s,ast.Constant) and isinstance(s.value,str) and any(c.isalpha() for c in s.value)]
                if words and any(word!=key.value for word in words):mapped.append(key.value)
            if mapped:violations.append((path.name,node.lineno,mapped))
    assert not violations,violations
