import json
from pathlib import Path

def test_checked_in_acceptance_corpus_contains_all_33_required_scenarios():
    path=Path(__file__).resolve().parents[1]/'scripts/evaluation/cases/agent-routing-v3.jsonl'
    cases=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(cases)>=50 and len({c['id'] for c in cases})==len(cases)
    assert {c['spec_case'] for c in cases if c['spec_case'] is not None}==set(range(1,34))
    assert all(c['setup']['identity'] in {'owner','guest'} and c['utterance'] and c['expect'] for c in cases)

def test_controlled_tool_probe_retains_native_thinking_protocol():
    import runpy
    root=Path(__file__).resolve().parents[1]/'scripts/evaluation'
    fixture=runpy.run_path(str(root/'routing_fixtures.py'))['Fixture']
    cases=[json.loads(line) for line in (root/'cases/agent-routing-v3.jsonl').read_text().splitlines()]
    for case in cases:
        if case['setup']['scenario'] not in {'limit','no_progress','repair'}:continue
        message=fixture(case).forced_decision([{'function':{'name':'web_research'}}])
        assert isinstance(message.get('reasoning_content'),str) and message['tool_calls']
