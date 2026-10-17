"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import pytest

@pytest.mark.parametrize("text, total, expected", [
    ("第二条", 3, [2]), ("第2条", 3, [2]), ("第十条", 12, [10]), ("第十一条", 12, [11]), ("第二十三篇", 30, [23]),
    ("第两条", 3, [2]), ("前两条", 3, [1, 2]), ("前3篇", 5, [1, 2, 3]), ("最后一条", 4, [4]), ("最后1篇", 4, [4]),
    ("第一条和第三条", 3, [1, 3]), ("第三条和第一条", 3, [1, 3]), ("第一条，第一条", 3, [1]),
    ("the first article", 3, [1]), ("the 3rd one", 3, [3]),
    ("first two articles", 3, [1, 2]), ("the last story", 3, [3]),
    ("第三季度的营收", 3, None), ("第三个季度的营收", 3, None), ("前三个月的情况", 3, None),
    ("第二个月", 3, None), ("the first quarter", 3, None), ("the first two quarters", 3, None),
    ("第3个工作日的营收", 3, None), ("第三个财年的数据", 3, None), ("最后一个季度的营收", 3, None),
    ("第二个公司", 3, None),
    ("第二个新闻", 3, [2]), ("第二个讲了什么", 3, [2]), ("第二个的内容", 3, [2]), ("第二个。", 3, [2]),
    ("第二个", 3, [2]), ("前两个新闻", 3, [1, 2]), ("最后一个新闻", 3, [3]),
    ("first half of the year", 3, None), ("the second half", 3, None), ("那两条新闻", 3, None),
    ("请读取 https://example.com/second-article-one 的内容", 3, None), ("总结一下", 3, None),
    ("请读取 https://example.com/news/第二条 的内容", 3, None),      # a path is not a position
])
def test_ordinal_positions_reads_only_a_position_of_a_record(text, total, expected):
    """The reader itself: a position of a record (any numeral form), never a quarter, a month, a
    half-year or a word inside a URL."""
    from app.agent_workflow.semantic import ordinal_positions
    assert ordinal_positions(text, total) == expected


@pytest.mark.parametrize("text, total", [
    ("第0条", 3), ("前0条", 3), ("第四条", 3), ("第十二条", 3), ("前九条", 3),
    ("第一条和前0条", 3), ("the first article and the first 0 articles", 3),
    ("the fifth article", 4), ("the first six articles", 3), ("2nd one", 1),
])
def test_ordinal_positions_refuses_a_position_the_set_does_not_have(text, total):
    """A position outside the set is an unavailable record (0 and a number the reader cannot
    parse included); it is never clipped to the set."""
    from app.agent_workflow.semantic import SemanticError, ordinal_positions
    with pytest.raises(SemanticError) as refused:
        ordinal_positions(text, total)
    assert str(refused.value) == "reference_item_unavailable"

