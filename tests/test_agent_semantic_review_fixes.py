"""Defects of this draft that the comparison review of 2026-10-14
(docs/superpowers/plans/2026-10-14-codex-comparison-review.zh-CN.md) reproduced, each with the
negative case first:

1. a reused result must still meet the *quarter* the user asked for (it only checked the year);
2. the filing form of a task is part of the scope the saved result set keeps;
3. "summarise the old Apple news, then search NVIDIA" is two tasks, not one refused sentence;
4. a short page is held to the same company/metric rule as a long one;
5. a definition question that names no company, source or period is not refused as "business data";
6. an English pointer ("the second article") is a pointer even when the resolver says "new".

RED   -- fails on the draft before the change, for the reason named in its docstring.
GUARD -- passes before and must keep passing.
"""
import pytest

from test_agent_semantic_regressions import (  # noqa: F401  (helpers of the sibling regression module)
    CHAT, FINANCE_REFUSAL, _answer, _general_turn, _new, _no_variants, _plan, _purposes, _reuse_run, _runtime,
    _saved_data_executor, _saved_web_set, _status_by_id, _task, _turn, _web_rows)

from app.agent_workflow.graph import build_workflow


# --- 1. a reused result must meet the quarter, not only the year --------------------------------

def _period_rows(text):
    rows = _web_rows("AAPL")
    rows["data"][0]["excerpt"] = text
    return rows


@pytest.mark.parametrize("excerpt", [
    "Apple (AAPL) reported revenue for FY 2025, with no quarter stated. " * 2,      # the year, no quarter
    "Apple (AAPL) revenue for the third quarter of fiscal 2024 was strong. " * 2,   # the quarter, the other year
    "Apple (AAPL) revenue for the second quarter of fiscal 2025 was strong. " * 2,  # the year, the other quarter
])
def test_a_quarter_the_user_asked_for_is_not_met_by_other_material(excerpt):
    """RED (review finding 1): the reuse check kept the year when a period held both a year and a
    quarter, so annual material answered a question about one quarter."""
    saved = _saved_web_set([], ["AAPL"], "apple-news", rows=_period_rows(excerpt))
    result, searched, answered = _reuse_run("总结一下刚刚那条新闻里苹果2025年第三季度的营收", saved, pick=0)
    assert answered == [] and searched == []
    assert result["terminal_status"] != "succeeded"
    assert result["gate_reason"] == "requested_period_missing"


@pytest.mark.parametrize("excerpt", [
    "Apple (AAPL) reported revenue of $94 billion for the third quarter of fiscal 2025. " * 2,
    "Apple (AAPL) Q3 2025 revenue reached $94 billion, driven by services. " * 2,
    "苹果（AAPL）2025年第三季度营收达到940亿美元，服务业务创新高。" * 2,
])
def test_the_quarter_in_other_words_still_counts_as_the_quarter(excerpt):
    """RED for the English wording (it was never read as a quarter), GUARD for the others."""
    saved = _saved_web_set([], ["AAPL"], "apple-news", rows=_period_rows(excerpt))
    result, searched, answered = _reuse_run("总结一下刚刚那条新闻里苹果2025年第三季度的营收", saved, pick=0)
    assert result["terminal_status"] == "succeeded", result.get("gate_reason")
    assert len(answered) == 1 and searched == []


def test_a_year_alone_is_still_met_by_a_year():
    """GUARD: a question that names only the year needs only the year."""
    saved = _saved_web_set([], ["AAPL"], "apple-news",
                           rows=_period_rows("Apple (AAPL) reported revenue for FY 2025, up 6%. " * 2))
    result, _, answered = _reuse_run("总结一下刚刚那条新闻里苹果2025年的营收", saved, pick=0)
    assert result["terminal_status"] == "succeeded" and len(answered) == 1


# --- 2. the filing form is part of a saved set's scope ------------------------------------------

def test_the_form_of_a_task_is_kept_in_the_scope_of_its_saved_result_set():
    """RED (review finding 4): the first read filtered by the form, but the scope stored with its
    result set lost it, so a later turn could not tell which report type the set was about."""
    calls = []
    runtime = _runtime(resolver=_new, intent_recognizer=_purposes("source_qa"),
                       planner=_plan(_task("read", "source_qa", "query_sec_filings", ["AAPL"], "AAPL 最新 10-Q 营收")),
                       query_expander=_no_variants, executor=_saved_data_executor(calls),
                       answerer=_answer("Report [1]."))
    result = _turn(build_workflow(), runtime, "AAPL 最新 10-Q 的营收", "turn")
    assert result["terminal_status"] == "succeeded", result.get("gate_reason")
    assert calls and calls[0][1].get("form") == "10-Q"
    assert result["result_sets"][-1]["scope"].get("form") == "10-Q"


# --- 3. an old set and a new company in one sentence ---------------------------------------------

def test_a_summary_of_the_old_set_and_a_search_for_another_company_are_two_tasks():
    """RED (review finding 3): once the Apple set was chosen the validator required every company of
    the sentence to belong to it, so 'summarise the Apple news, then find NVIDIA news' was refused as
    reference_scope_changed before the planner could split it."""
    saved = _saved_web_set([], ["AAPL"], "apple-news")
    calls = []

    async def resolve(payload):
        return {"relationship": "followup", "canonical_question": payload["question"],
                "result_set_id": saved[0]["id"], "item_ids": []}

    async def execute(name, args):
        calls.append((name, args))
        return _web_rows("NVDA")

    runtime = _runtime(
        resolver=resolve, intent_recognizer=_purposes("summarize_existing", "web_research"),
        planner=_plan(_task("sum", "summarize_existing", "summarize_existing_results", ["AAPL"], "summarize the Apple news"),
                      _task("nv", "web_research", "web_research", ["NVDA"], "NVDA latest news")),
        query_expander=_no_variants, executor=execute, answerer=_answer("Apple [1]. NVIDIA [2]."))
    result = _turn(build_workflow(), runtime, "总结苹果刚刚的新闻，再找英伟达最新新闻", "turn", result_sets=saved)
    assert result["terminal_status"] == "succeeded", result.get("gate_reason")
    assert calls == [("web_research", {"query_or_url": "NVDA latest news"})]
    assert _status_by_id(result) == {"sum": "succeeded", "nv": "succeeded"}


def test_a_set_of_another_company_is_still_refused_for_the_clause_that_points_at_it():
    """GUARD (G2 item 5): the clause that points back must name the set's own company."""
    saved = _saved_web_set([], ["AAPL"], "apple-news")
    result, searched, answered = _reuse_run("总结英伟达刚刚的新闻", saved, pick=0)
    assert answered == [] and searched == []
    assert result["terminal_status"] == "waiting_for_input"
    assert result["pending_request"]["reason"] == "reference_scope_changed"


# --- 4. a short page is held to the company/metric rule ------------------------------------------

SHORT_MIXED = ("Apple introduced a new iPhone. Samsung reported strong demand for its phones. "
               "The company revenue rose 20% in 2025.")


def test_a_short_page_is_held_to_the_same_company_and_metric_rule():
    """RED (review finding 6): a page that fits the bound came back unchanged, so a metric sentence
    that followed another company's sentence was handed to the gate as Apple's."""
    from app.agent_web_research import focus_excerpt
    from app.agent_workflow.graph import deterministic_confidence_gate
    out = focus_excerpt(SHORT_MIXED, "Apple revenue news")
    assert "Apple introduced a new iPhone." in out and "revenue rose 20%" not in out
    shown = {"ok": True, "source": "public web search and fetched page excerpts",
             "data": [{"title": "Apple news", "url": "https://example.com/a", "excerpt": out,
                       "published_at": "2026-10-12T00:00:00Z"}]}
    assert deterministic_confidence_gate("Apple revenue news", shown, [], ["AAPL"]) == (
        False, "requested_metric_missing:revenue")


def test_a_short_page_with_the_company_beside_its_metric_sentence_is_unchanged():
    """GUARD."""
    from app.agent_web_research import focus_excerpt
    text = "Apple introduced a new iPhone. The company revenue rose 20% in 2025."
    assert focus_excerpt(text, "Apple revenue news") == text
    assert focus_excerpt("Apple revenue rose 6%.", "Apple revenue") == "Apple revenue rose 6%."
    assert focus_excerpt(SHORT_MIXED, "latest news") == SHORT_MIXED            # no metric asked: no rule


def test_the_title_that_names_the_company_stands_before_the_first_sentence():
    """A snippet of a page whose headline names Apple and whose first sentence only says 'revenue'
    belongs to Apple; the headline is not part of the text, so it is passed along."""
    from app.agent_web_research import _focus_source, focus_excerpt
    text = "Revenue rose 20% in 2025. The company also announced a buyback."
    assert focus_excerpt(text, "Apple revenue news") == "The company also announced a buyback."
    assert focus_excerpt(text, "Apple revenue news", heading="Apple results: services grow") == text
    source = {"excerpt": SHORT_MIXED, "title": "Samsung and Apple at the same show"}
    _focus_source(source, "Apple revenue news")
    assert "revenue rose 20%" not in source["excerpt"] and source["truncated"] is True


# --- 5. a definition is not a business fact -------------------------------------------------------

@pytest.mark.parametrize("question", [
    "什么是营收？", "营收是什么意思", "什么是每股收益", "What is EPS?", "Define free cash flow",
    "what does revenue mean", "每股收益的定义是什么",
])
def test_a_definition_question_with_no_company_source_or_period_is_a_general_answer(question):
    """RED (review finding 5): any metric word ended the turn as 'needs evidence'."""
    result, answered = _general_turn(question)
    assert len(answered) == 1 and result["terminal_status"] == "succeeded"
    assert result.get("gate_reason") != "semantic_general_requires_evidence"


@pytest.mark.parametrize("question", [
    "什么是苹果的营收", "营收是什么意思？苹果2025年的是多少", "What is AAPL revenue", "每股收益是多少",
    "What is the revenue of FY2025", "营收是什么意思，0000320193-26-000020 里写了多少",
])
def test_a_definition_that_asks_for_a_companys_figure_is_still_refused(question):
    """GUARD (G2 item 9): the carve-out is for the word, not for a company's number."""
    result, answered = _general_turn(question)
    assert answered == []
    assert result["answer"] == FINANCE_REFUSAL


# --- 6. English pointers --------------------------------------------------------------------------

@pytest.mark.parametrize("words", ["Explain the second article", "Summarize the first one in detail",
                                   "Tell me more about the last story"])
def test_an_english_pointer_is_a_pointer_even_when_the_resolver_says_new(words):
    """RED (review, 'not new defects' list): the back-reference cues of the 'new' check were
    Chinese-heavy, so an English pointer the resolver mislabelled as new was searched as a topic."""
    saved = _saved_web_set([], ["AAPL"], "apple-news")
    searched = []

    async def resolve(_payload):
        return {"relationship": "new", "canonical_question": words, "result_set_id": None, "item_ids": []}

    async def execute(name, args):
        searched.append((name, args))
        return _web_rows("AAPL")

    runtime = _runtime(resolver=resolve, intent_recognizer=_purposes("web_research"),
                       planner=_plan(_task("w", "web_research", "web_research", [], "second article")),
                       query_expander=_no_variants, executor=execute, answerer=_answer("never"))
    result = _turn(build_workflow(), runtime, words, "turn", result_sets=saved)
    assert searched == []
    assert result["terminal_status"] == "waiting_for_input"
    assert result["pending_request"]["reason"] == "reference_unavailable"
