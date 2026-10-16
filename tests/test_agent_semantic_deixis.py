"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import pytest
from app.agent_workflow.actions import requested_action
from app.agent_workflow.semantic import stated_count

@pytest.mark.parametrize("words, expected", [
    ("把刚刚那两条导入", 2), ("把这三份财报导入", 3), ("导入那2条", 2), ("import those two", 2),
    ("把第二条导入", None), ("把刚刚那些导入", None), ("导入 AAPL 的资料", None),
])
def test_a_count_the_user_states_is_read_from_the_words(words, expected):
    assert stated_count(words) == expected


@pytest.mark.parametrize("words, action", [
    ("把刚刚那两条导入", "import"), ("把刚刚那两条导入资料库", "import"), ("导入刚刚那两条", "import"),
    ("导入第二份", "import"), ("把这份财报导入", "import"), ("import those two", "import"),
    ("对刚刚那两份生成 AI 分析", "analyze"), ("分析一下刚刚那份财报，生成 AI 分析", "analyze"),
])
def test_a_write_verb_with_a_pointer_for_its_object_is_a_requested_action(words, action):
    """RED: the object had to be a noun of a fixed list (资料/材料/来源 ...), so 'import the two I
    just listed' was no write at all and ended as a generic failure."""
    assert requested_action(words) == action


@pytest.mark.parametrize("words", [
    "刚刚那条新闻说的导入政策是什么", "最近导入关税有什么变化", "那份报告的导入业务占比多少",
    "它的最新新闻呢", "刚刚那两条新闻的第二条讲了什么",
])
def test_a_pointer_near_an_ordinary_word_is_no_write(words):
    assert requested_action(words) is None
