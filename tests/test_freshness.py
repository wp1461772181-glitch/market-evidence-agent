"""How recent a question wants what it finds.

Found by a user asking "帮我上网搜一下最近美光的新闻" and getting March: "最近" was a freshness word
nowhere, so the search had no time range.  A follow-up "我要最近的行情，也就是半个月之内的" was read as
a price question; the intent stage now knows what the previous request was.
"""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_workflow.graph import build_workflow, deterministic_confidence_gate
from app.freshness import published_within, search_time_range, wants_recent, window_days
from test_agent_semantic_regressions import _answer, _new, _no_variants, _plan, _runtime, _task, _turn, _web_rows

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("words, days", [
    ("我要最近半个月的美光的新闻", 15), ("我要最近的行情，也就是半个月之内的", 15), ("近三天的新闻", 3),
    ("最近两周", 14), ("一个月内", 30), ("last 3 days of Micron news", 3), ("within the past two weeks", 14),
    ("美光 最近 新闻", None), ("latest Micron news", None), ("Micron news", None),
    ("3月份的新闻", None), ("第二周的业绩", None), ("3月5日的公告", None), ("两周年纪念", None), ("三个月前的报道", None),
])
def test_a_span_is_only_what_the_words_name_as_one(words, days):
    assert window_days(words) == days


@pytest.mark.parametrize("words, time_range, recent", [
    ("美光 最近 新闻", "week", True),                    # the user's first message: no range at all before
    ("美光 新闻 近日", "week", True),
    ("我要最近半个月的美光的新闻", "month", True),        # half a month is held by the month range, then cut exactly
    ("last 3 days Micron news", "week", True),
    ("latest Micron news", "week", True),
    ("美光 新闻", None, False),
    ("苹果 2025 财年第三季度 营收", None, False),
])
def test_the_search_and_the_gate_read_the_same_words(words, time_range, recent):
    from app.agent_web_research import _tavily_body
    assert search_time_range(words) == time_range
    assert wants_recent(words) is recent
    body = _tavily_body(words, True)
    assert body.get("time_range") == time_range and body["topic"] == "news"


def test_a_source_must_be_dated_and_inside_the_span():
    assert published_within("2026-10-10T08:00:00Z", 15, NOW) is True
    assert published_within("2026-09-20T08:00:00+00:00", 15, NOW) is False
    assert published_within("", 15, NOW) is False and published_within("not a date", 15, NOW) is False


def _fresh(days_ago):
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _fake_search(monkeypatch, rows):
    import app.agent_web_research as web

    async def search(_query, include_raw_content=True):
        return rows

    async def fetch(url, limit):
        raise ValueError("page_unavailable")          # the search snippet's raw content is what is used

    monkeypatch.setattr(web, "_search", search)
    monkeypatch.setattr(web, "_get_public", fetch)
    return web


def _row(name, published):
    return {"url": f"https://example.com/{name}", "title": name, "snippet": "snippet",
            "raw_content": f"Micron news {name}: " + "text " * 40, "published_at": published}


def test_a_named_span_leaves_out_what_is_older_or_undated(monkeypatch):
    web = _fake_search(monkeypatch, [_row("fresh", _fresh(3)), _row("old", _fresh(40)), _row("undated", None)])
    result = asyncio.run(web._research("美光 最近半个月 新闻"))
    assert [row["title"] for row in result["data"]] == ["fresh"]
    plain = asyncio.run(web._research("美光 新闻"))                    # no span named: nothing is cut here
    assert {row["title"] for row in plain["data"]} == {"fresh", "old", "undated"}


def test_nothing_inside_the_span_is_an_empty_answer_not_an_outage(monkeypatch):
    web = _fake_search(monkeypatch, [_row("old", _fresh(40))])
    result = asyncio.run(web._research("美光 最近半个月 新闻"))
    assert result["ok"] is True and result["data"] == [] and result["error_code"] is None
    passed, reason = deterministic_confidence_gate("美光 最近半个月 新闻", result)
    assert (passed, reason) == (False, "zero_evidence")


def test_a_recent_question_needs_dated_sources_whichever_word_asked_for_it():
    undated = {"ok": True, "source": "public web search and fetched page excerpts", "source_type": "web",
               "data": [{"title": "Micron news", "url": "https://example.com/a",
                         "excerpt": "Micron news with enough words to be an excerpt.", "published_at": None}]}
    assert deterministic_confidence_gate("美光 最近 新闻", undated) == (False, "publisher_date_unavailable")
    undated["data"][0]["published_at"] = _fresh(2)
    assert deterministic_confidence_gate("美光 最近 新闻", undated)[0] is True


# --- the follow-up that only refines the previous request ---------------------------------------------------

def test_the_intent_stage_is_told_what_the_previous_request_was():
    seen = []

    async def intent(payload):
        seen.append(payload)
        return {"purposes": ["web_research"], "action": None}

    async def execute(name, args):
        return _web_rows("MU")

    runtime = _runtime(resolver=_new, intent_recognizer=intent,
                       planner=_plan(_task("news", "web_research", "web_research", ["MU"], "美光 MU 新闻 最近半个月")),
                       query_expander=_no_variants, executor=execute, answerer=_answer("美光的新闻 [1]。"))
    graph = build_workflow()
    first = _turn(graph, runtime, "帮我上网搜一下最近美光 MU 的新闻", "one")
    _turn(graph, runtime, "我要最近的行情，也就是半个月之内的 MU", "two", first)
    assert "previous_purposes" not in seen[0]
    assert seen[1]["previous_purposes"] == ["web_research"]


def test_a_chinese_only_search_gets_the_english_name_and_ticker_beside_it():
    from app.agent_workflow.graph import _with_latin_name
    assert _with_latin_name("美光 最近半个月 新闻", ["MU"]) == "美光 最近半个月 新闻 Micron MU"
    assert _with_latin_name("苹果 新闻", ["AAPL"]) == "苹果 新闻 Apple AAPL"
    assert _with_latin_name("超微 新闻", ["AMD"]) == "超微 新闻 Advanced Micro Devices AMD"
    assert _with_latin_name("MU 新闻", ["MU"]) == "MU 新闻"                    # a ticker is already there
    assert _with_latin_name("Micron news", ["MU"]) == "Micron news"            # so is the English name
    assert _with_latin_name("FBRX news", ["FBRX"]) == "FBRX news"              # not in the directory: untouched
    assert _with_latin_name("美光 新闻", []) == "美光 新闻"
