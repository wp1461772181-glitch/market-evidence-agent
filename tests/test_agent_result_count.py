"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import pytest
from app.agent_workflow.semantic import requested_result_count

@pytest.mark.parametrize("words, expected", [
    ("帮我找两条跟苹果营收相关的新闻报道，给我标题和来源链接", 2),
    ("找3篇英伟达的报道", 3),
    ("给我三则最新消息", 3),
    ("找一条苹果的新闻", 1),
    ("找十条新闻", 10),
    ("找 2 个新闻", 2),
    ("再帮我找两条英伟达的新闻，也给标题和链接", 2),
    ("find two articles about Apple", 2),
    ("top 3 news about NVDA", 3),
    ("show me 5 headlines", 5),
    ("苹果最近的新闻", None),                 # no count
    ("把第二条新闻详细解释一下", None),         # an ordinal is a position, not a count
    ("近3个月的营收怎么样", None),             # a time window
    ("找一下 2025 年第三季度的新闻", None),    # a period
    ("苹果的第3条新闻", None),
    ("top 20 stocks", None),                 # not a result count of a news search we would honor
    ("", None),
])
def test_the_count_a_search_request_asks_for(words, expected):
    assert requested_result_count(words) == expected
