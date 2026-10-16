"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import pytest

@pytest.mark.parametrize("question", [
    "What is the most important risk in Apple's report?",       # 'import' inside 'important'
    "important material for the 10-K",                           # ... even with an object nearby
    "Nasdaq-100 index",                                          # a market index, not an index job
    "Which stocks beat the S&P 500 index this year?",
    "run any analysis",                                          # no object: nothing to analyse
    "Is the forerunner analysis of the 10-K finished?",          # 'run' inside 'forerunner'
    "Is the thread job status healthy?",                         # 'read' inside 'thread'
    "Does the page refresh every second?",                       # 'sec' inside 'second'
    "fetch the second column of the table",                      # ... and after 'fetch'
    "Dow Jones indexes for materials",                           # 'indexes' is not the verb 'index'
    "please import AAPL",                                        # an import names what it imports
    "导入",
    "加入资料库",                                                 # ... also when the library is the only noun
    "run analysis of the market mood on social media and later the 10-K",   # the object is far away
    "run a psychoanalysis of the report",                        # 'analysis' inside 'psychoanalysis'
    "he created a forecast last week",                           # 'create' inside 'created'
])
def test_ordinary_words_are_not_taken_for_a_write_request(question):
    """RED (G2 blocker 2, action words): the substring regex made 'most important' an import,
    'Nasdaq-100 index' an index job (which needs no slot and runs globally) and 'run any
    analysis' an analysis."""
    from app.agent_workflow.actions import requested_action
    assert requested_action(question) is None


@pytest.mark.parametrize("question, action", [
    ("导入 AAPL 的资料", "import"),
    ("导入已保存资料", "import"),
    ("将资料加入资料库", "import"),
    ("import source 11111111-1111-4111-8111-111111111111", "import"),
    ("please import the AAPL filing 0000320193-26-000020", "import"),
    ("建立索引", "index"),
    ("重建索引", "index"),
    ("re-index the source", "index"),
    ("index this filing 0000320193-26-000020", "index"),
    ("生成 AAPL 10-Q 的 AI 分析", "analyze"),
    ("生成 AI 分析", "analyze"),
    ("生成 MSFT AI 分析", "analyze"),
    ("为这份财报生成分析", "analyze"),
    ("分析 accession 0000320193-26-000020", "analyze"),
    ("run an analysis of the AAPL 10-Q", "analyze"),
    ("request analysis for 11111111-1111-4111-8111-111111111111", "analyze"),
    ("创建 AAPL 预测", "forecast"),
    ("创建预测任务", "forecast"),
    ("create a forecast for AAPL", "forecast"),
    ("查看任务状态", "status"),
    ("check job status", "status"),
    ("刷新 AAPL 原文", "refresh_sec"),
    ("refresh the SEC filing", "refresh_sec"),
    ("抓取 SEC 原文", "fetch_sec"),
    ("fetch the SEC filing", "fetch_sec"),
])
def test_explicit_action_phrases_are_still_recognised(question, action):
    """GUARD: the phrases the suite and the product documents use keep their action."""
    from app.agent_workflow.actions import requested_action
    assert requested_action(question) == action


@pytest.mark.parametrize("question", [
    "上传证据到资料库", "请帮我处理这份资料", "请处理 AAPL 资料", "总结 AAPL 最新财报", "buy AAPL",
    "那你去看下资料库中苹果最新的资料说了什么", "查看资料库中 AAPL 原文和 AI 分析", "查看 AAPL 已保存的 AI 分析摘要",
    "苹果资料库最新资料说了什么", "AAPL revenue in 2025", "compare AMD and AAPL saved closes",
])
def test_read_questions_stay_reads(question):
    """GUARD: read questions of the existing suites are not actions."""
    from app.agent_workflow.actions import requested_action
    assert requested_action(question) is None


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


@pytest.mark.parametrize("answer, ordinals, expected", [
    ("第二条讲的是 iPhone 销量 [2]。", [1], [2]),                          # the live defect
    ("Services grew [1].", [1], []),
    ("Services grew [1] and margins [3]; see [2].", [1, 2], [3]),
    ("Services grew [3][1][3], then [3] again.", [1], [3]),               # once each, ascending
    ("[5] [4] [4] [6]", [], [4, 5, 6]),                                   # a turn without cards
    ("No markers at all.", [1, 2], []),
    ("[0] is not a card", [1], [0]),
    ("Not markers: [1, 2] [a] [] [ 1 ] [1.5] [-1] 【3】 [^3] (3) [1a]", [], []),
    ("Not what the front end links: [１] [٣] [١٢]", [], []),               # only ASCII digits are a marker
    ("A [link](https://example.com/3) and [note]", [], []),
])
def test_markers_of_the_answer_that_match_no_card_are_listed(answer, ordinals, expected):
    """RED: a pure reading, the same ``[n]`` the front end links; matched markers are not listed."""
    from app.agent_workflow.semantic import unmatched_citation_markers
    citations = [{"ordinal": number, "source_type": "web", "source_url": f"https://example.com/{number}"}
                 for number in ordinals]
    assert unmatched_citation_markers(answer, citations) == expected


def test_the_marker_reader_is_total_over_whatever_the_state_holds():
    """RED: nothing in a state or an answer can make the end of a turn raise (an answer is at most 16 kB,
    so a run of digits as long as the integer conversion limit is possible)."""
    from app.agent_workflow.semantic import unmatched_citation_markers
    junk = [None, "x", 3, {}, {"ordinal": None}, {"ordinal": "two"}, {"ordinal": 1.5}, {"ordinal": True},
            {"ordinal": 1}]
    assert unmatched_citation_markers("a [1] b [2]", junk) == [2]
    assert unmatched_citation_markers("a [1] b [2]", [*junk, {"ordinal": "2"}]) == []
    assert unmatched_citation_markers("a [1] b [2]", [{"ordinal": 1.5}, {"ordinal": True}, {"ordinal": 2}]) == [1]
    assert unmatched_citation_markers("a [1]", [{"ordinal": "9" * 5000}]) == [1]
    assert unmatched_citation_markers(None, junk) == []
    assert unmatched_citation_markers("see [2]", None) == [2]
    assert unmatched_citation_markers("x [" + "9" * 5000 + "] y [4]", []) == [4]
