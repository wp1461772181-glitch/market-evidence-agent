"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import pytest
from app.agent_workflow.semantic import stated_count

@pytest.mark.parametrize("words, expected", [
    ("把刚刚那两条导入", 2), ("把这三份财报导入", 3), ("导入那2条", 2), ("import those two", 2),
    ("把第二条导入", None), ("把刚刚那些导入", None), ("导入 AAPL 的资料", None),
])
def test_a_count_the_user_states_is_read_from_the_words(words, expected):
    assert stated_count(words) == expected
