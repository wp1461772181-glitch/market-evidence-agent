"""The user said how many results they want: the saved result set, the cards and the answer's
evidence are that many (the best ones), not however many the search kept."""
import pytest

from test_agent_semantic_regressions import (CHAT, _answer, _no_variants, _plan, _purposes, _runtime, _task, _turn)
from app.agent_workflow.graph import build_workflow
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


def _rows(symbol, n):
    return {"ok": True, "source": "public web search and fetched page excerpts", "data": [
        {"title": f"{symbol} revenue report {i}", "url": f"https://example.com/{i}",
         "excerpt": f"{symbol} revenue grew with services sales and product demand number {i}. " * 3,
         "published_at": "2026-10-12T00:00:00Z", "fetched_at": "2026-10-13T00:00:00Z",
         "body_provenance": "public_page_fetch"} for i in range(1, n + 1)]}


def _search_turn(words, available):
    async def resolve(payload):
        return {"relationship": "new", "canonical_question": payload["question"], "result_set_id": None, "item_ids": []}

    async def execute(_name, _args):
        return _rows("AAPL", available)

    runtime = _runtime(resolver=resolve, intent_recognizer=_purposes("web_research"),
                       planner=_plan(_task("news", "web_research", "web_research", ["AAPL"], "Apple revenue news")),
                       query_expander=_no_variants, executor=execute, answerer=_answer("Answer [1][2]."))
    return _turn(build_workflow(), runtime, words, "turn")


@pytest.mark.parametrize("words, available, kept", [
    ("帮我找两条跟苹果营收相关的新闻报道，给我标题和来源链接", 3, 2),
    ("找一条苹果营收的新闻", 3, 1),
    ("帮我找苹果营收相关的新闻报道", 3, 3),                    # no count: as many as the search kept
    ("帮我找三条苹果营收的新闻", 2, 2),                        # fewer usable than asked: all of them
])
def test_the_result_set_and_the_cards_match_the_count_asked_for(words, available, kept):
    result = _search_turn(words, available)
    assert result["terminal_status"] == "succeeded", result.get("gate_reason")
    sets = result["result_sets"]
    assert len(sets) == 1 and len(sets[0]["items"]) == kept
    assert len(result["citations"]) == kept
    assert [item["ordinal"] for item in result["citations"]] == list(range(1, kept + 1))
