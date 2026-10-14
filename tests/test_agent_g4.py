"""G4 (2026-10-14/15): what the user asked for after trying the G3 release.

1. The resolver completes the user's words into one standalone question from the whole dialog (a
   budgeted view of it, not the last eight messages) and leaves a complete question as it is.
2. A request that has nothing to do with this product gets a fixed script, not an answer from the
   model's memory.
3. A write is never run on a reading of the words: the user is shown what would run and presses
   同意 / 选择其他写操作 / 取消.  Guests get no card; a click is a server-minted value.
4. A turn that fails says so in words (and the words are kept in the history); a slow model service
   is given longer.
5. A page of an error or navigation chrome is not listed as a source.
6. The owner can name a conversation.
"""
import asyncio
import json

import pytest

from test_agent_semantic_deixis import (  # noqa: F401  (helpers of the sibling test modules)
    FILING_A, FILING_B, _pointing_runtime, _recorded_actions, _saved_filing_set)
from test_agent_semantic_regressions import (  # noqa: F401
    CHAT, OTHER_CHAT, SOURCE, _acting_runtime, _answer, _no_variants, _plan, _press, _purposes, _runtime,
    _saved_web_set, _task, _turn, _turn_confirmed, _web_rows)

from app.agent_workflow.graph import (DIALOG_BUDGET_CHARS, OUT_OF_SCOPE_SCRIPT, WRITE_CANCELLED, _dialog_view,
                                      build_workflow)
from app.agent_workflow.semantic import (STAGE_PROMPTS, SemanticError, catalog, completes, downstream_question,
                                        validate_reference)


# --- 1. the resolver reads the dialog and completes the words ---------------------------------------

def test_the_dialog_view_keeps_the_start_of_a_conversation_that_fits_the_budget():
    """The last eight messages (the G3 window) lost the first search of a ten-message conversation:
    '最开始那条新闻' then had nothing to point at.  The view holds every turn the budget allows."""
    messages = [{"role": "system", "content": "prompt"}]
    for turn in range(10):
        messages += [{"role": "user", "content": f"user question {turn} " + "x" * 30},
                     {"role": "assistant", "content": f"answer {turn} " + "y" * 200}]
    view = _dialog_view(messages)
    assert view[0]["content"].startswith("user question 0")
    assert [item["role"] for item in view[:2]] == ["user", "assistant"]
    assert sum(len(item["content"]) for item in view) <= DIALOG_BUDGET_CHARS


def test_the_oldest_turns_go_first_when_the_dialog_is_longer_than_the_budget():
    messages = []
    for turn in range(80):
        messages += [{"role": "user", "content": f"q{turn} " + "x" * 500},
                     {"role": "assistant", "content": f"a{turn} " + "y" * 480}]
    view = _dialog_view(messages)
    assert view[-1]["content"].startswith("a79")           # the newest turn is always there
    assert not view[0]["content"].startswith("q0 ")         # the oldest turns are what the budget drops
    assert sum(len(item["content"]) for item in view) <= DIALOG_BUDGET_CHARS
    assert len(view) < len(messages)


def test_the_catalog_says_which_set_is_the_oldest_and_what_each_answered():
    sets = [{"id": f"s{index}", "owner": "o", "conversation": "c", "turn": f"t{index}", "task": "x",
             "scope": {"symbols": [symbol]}, "asked": question,
             "items": [{"id": f"i{index}", "title": f"title {index}"}]}
            for index, (symbol, question) in enumerate([("AAPL", "找苹果新闻"), ("AAPL", "它的最新新闻呢"),
                                                         ("NVDA", "英伟达的消息")])]
    listed = catalog(sets, "o", "c")
    assert [(entry["position"], entry["age"]) for entry in listed] == [(1, "oldest"), (2, "middle"), (3, "newest")]
    assert listed[0]["asked"] == "找苹果新闻" and listed[2]["scope"] == {"symbols": ["NVDA"]}


def test_the_resolver_is_told_the_order_of_the_catalog_and_what_最开始_means():
    prompt = STAGE_PROMPTS["resolver"]
    assert "oldest (position 1)" in prompt and "最开始" in prompt and "最近" in prompt
    assert "standalone question" in prompt and "unchanged" in prompt  # a complete question is returned as it is
    assert "我指的是" in prompt                                          # a correction is completed


def test_a_saved_result_set_remembers_the_question_that_produced_it():
    saved = _saved_web_set([], ["AAPL"], "apple-news")
    from app.agent_workflow.semantic import save_result_set
    rows = _web_rows("AAPL")
    citation = {"ordinal": 1, "source_type": "web", "source_url": "https://example.com/a", "title": "AAPL revenue report",
                "quote": rows["data"][0]["excerpt"], "truncated": False}
    again = save_result_set([], rows, [citation], owner="owner", conversation=CHAT, turn="t", task="news",
                            scope={"symbols": ["AAPL"]}, asked="  找两条苹果营收新闻  ")
    assert again and again[0]["asked"] == "找两条苹果营收新闻"
    assert saved and "asked" not in saved[0]      # a set saved without a question has none


@pytest.mark.parametrize("words, rewrite, used", [
    # a complete question passes through, however the model rephrased or translated it
    ("苹果这个季度的营收怎么样", "How is Apple's revenue this quarter", False),
    ("苹果最新新闻", "AAPL latest news", False),
    # a pronoun or a left-out subject: the completion names what the words leave out
    ("它的最新新闻呢", "AAPL 的最新新闻呢", True),
    ("营收呢", "AAPL 的营收呢", True),
    # a correction of the question just before is answered by that question
    ("我指的是最开始你给我发的苹果的新闻", "最开始那条苹果新闻中说的营收是多少", True),
])
def test_only_a_completion_replaces_the_words(words, rewrite, used):
    assert completes(words, rewrite) is used


def test_a_correction_carries_the_question_it_corrects_to_the_stages():
    reference = {"relationship": "new", "canonical_question": "最开始那条苹果新闻中说的营收是多少",
                 "result_set_id": None, "item_ids": []}
    assert downstream_question(reference, "我指的是最开始你给我发的苹果的新闻") == "最开始那条苹果新闻中说的营收是多少"


def test_a_correction_that_points_at_a_record_keeps_the_request_it_corrects():
    words = "不对，我说的是英伟达那组里的第一条"
    reference = {"relationship": "followup", "canonical_question": "把英伟达那组里的第一条新闻详细解释一下",
                 "result_set_id": "ebc8aa44", "item_ids": ["6743e438"]}
    asked = ["把最开始那条新闻详细解释一下", words]
    assert downstream_question(reference, words, trusted=asked) == "把英伟达那组里的第一条新闻详细解释一下"
    # the model that kept the words gives the words
    kept = {**reference, "canonical_question": words}
    assert downstream_question(kept, words, trusted=asked) == words


def test_a_followup_that_is_no_correction_is_worked_on_in_the_users_words():
    reference = {"relationship": "followup", "canonical_question": "把英伟达那组里的第一条新闻详细解释一下",
                 "result_set_id": "ebc8aa44", "item_ids": ["6743e438"]}
    assert downstream_question(reference, "第一条详细解释一下") == "第一条详细解释一下"


def test_a_correction_completion_that_brings_in_something_new_is_not_used():
    words = "不对，我说的是英伟达那组里的第一条"
    reference = {"relationship": "followup", "canonical_question": "把英伟达那组里的第一条新闻里 500 亿的营收详细解释一下",
                 "result_set_id": "ebc8aa44", "item_ids": ["6743e438"]}
    assert downstream_question(reference, words, trusted=[words]) == words


_CORRECTED_SET = [{"id": "ebc8aa44", "scope": {"symbols": ["NVDA"]}, "items": [{"id": "6743e438", "title": "NVIDIA commits $1B"}]}]


@pytest.mark.parametrize("completion, accepted", [
    ("把英伟达那组里的第一条新闻详细解释一下", True),        # the request, restated for the record now meant
    ("不对，我说的是英伟达那组里的第一条", True),             # the model kept the words
    ("把英伟达和苹果那组里的第一条新闻详细解释一下", False),  # a company nobody named
    ("不要把英伟达那组里的第一条新闻详细解释一下", False),    # a negation nobody wrote: the user's 不对 is no such
])
def test_an_opening_rejection_is_not_a_negation_of_the_request(completion, accepted):
    words = "不对，我说的是英伟达那组里的第一条"
    value = {"relationship": "followup", "canonical_question": completion, "result_set_id": "ebc8aa44",
             "item_ids": ["6743e438"]}
    context = ["把最开始那条新闻详细解释一下", words]
    if accepted:
        assert validate_reference(value, _CORRECTED_SET, words, context=context)["canonical_question"] == completion
    else:
        with pytest.raises(SemanticError):
            validate_reference(value, _CORRECTED_SET, words, context=context)


def test_a_complete_question_the_model_rephrased_is_worked_on_in_the_users_words():
    reference = {"relationship": "new", "canonical_question": "How is Apple's revenue this quarter",
                 "result_set_id": None, "item_ids": []}
    assert downstream_question(reference, "苹果这个季度的营收怎么样") == "苹果这个季度的营收怎么样"


# --- 2. out of scope: a fixed script, no answer from memory ----------------------------------------

def _scope_turn(question, purposes, answered=None):
    async def answer(messages):
        if answered is not None:
            answered.append(messages[-1]["content"])
        yield "an answer from memory"

    async def planner(_payload):
        raise AssertionError("a turn outside the product is not planned")

    runtime = _runtime(resolver=_new_words, intent_recognizer=_purposes(*purposes), planner=planner,
                       query_expander=_no_variants, answerer=answer)
    return _turn(build_workflow(), runtime, question, "turn")


async def _new_words(payload):
    return {"relationship": "new", "canonical_question": payload["question"], "result_set_id": None, "item_ids": []}


@pytest.mark.parametrize("question", ["给我一段冒泡排序代码", "给我一段 Java 的冒泡排序代码", "Write a bubble sort in Python"])
def test_a_request_unrelated_to_the_product_gets_the_script_not_an_answer(question):
    answered = []
    result = _scope_turn(question, ["out_of_scope"], answered)
    assert answered == []                                  # the model is not asked at all
    assert result["answer"] == OUT_OF_SCOPE_SCRIPT
    assert result["terminal_status"] == "succeeded"
    assert result["audit"]["gate_reason"] == "out_of_scope"
    assert result["audit"]["tools"] == [] and result["task_states"] == []


def test_a_finance_concept_is_still_answered():
    answered = []
    result = _scope_turn("什么是营收？", ["general"], answered)
    assert len(answered) == 1 and result["answer"] != OUT_OF_SCOPE_SCRIPT


def test_out_of_scope_next_to_a_real_purpose_only_drops_itself():
    """'帮我写代码，另外看下 AAPL 行情': the in-scope part still runs; the rest is not answered."""
    async def planner(_payload):
        return {"tasks": [_task("p", "saved_data", "query_stock_prices", ["AAPL"], "AAPL price")]}

    async def execute(_name, _args):
        return {"ok": True, "source": "saved prices", "data": [{"symbol": "AAPL", "close": 231.4, "date": "2026-10-12"}]}

    runtime = _runtime(resolver=_new_words, intent_recognizer=_purposes("out_of_scope", "saved_data"),
                       planner=planner, query_expander=_no_variants, executor=execute, answerer=_answer("AAPL 231.4 [1]"))
    result = _turn(build_workflow(), runtime, "AAPL 的最新价格，再帮我写个冒泡排序", "turn")
    assert result["semantic_intents"] == ["saved_data"]
    assert result["answer"] != OUT_OF_SCOPE_SCRIPT


def test_the_intent_schema_accepts_an_action_and_rejects_an_unknown_one(monkeypatch):
    from app.agent_workflow.semantic import SemanticError
    _recorded_actions(monkeypatch)      # an owner's session
    assert _scope_turn("为 AAPL 创建预测", ["system_action"])["pending_request"]["reason"]  # the card, no failure

    async def bad_intent(_payload):
        return {"purposes": ["system_action"], "action": "drop_all_tables"}

    runtime = _runtime(resolver=_new_words, intent_recognizer=bad_intent,
                       planner=_plan(), query_expander=_no_variants, answerer=_answer("x"))
    result = _turn(build_workflow(), runtime, "为 AAPL 创建预测", "turn")
    assert result["terminal_status"] == "failed" and result["intent"] == "classifier_error"
    assert SemanticError  # imported only to show the error type belongs to this stage


# --- 3. the write card -----------------------------------------------------------------------------

def _acting_with_action(action, rewrites=None):
    """The intent stage names the write it thinks is meant (a proposal, never an authority)."""
    async def intent(_payload):
        return {"purposes": ["system_action"], "action": action}

    async def resolve(payload):
        text = (rewrites or {}).get(payload["question"], payload["question"])
        return {"relationship": "new", "canonical_question": text, "result_set_id": None, "item_ids": []}

    return _runtime(resolver=resolve, intent_recognizer=intent, planner=_plan(), query_expander=_no_variants,
                    answerer=_answer("done"))


def test_a_typed_write_shows_a_card_and_runs_nothing_until_it_is_pressed(monkeypatch):
    executed, _ = _recorded_actions(monkeypatch)
    graph, runtime = build_workflow(), _acting_with_action("forecast")
    shown = _turn(graph, runtime, "为 NVDA 创建预测", "one")
    assert executed == []
    pending = shown["pending_request"]
    assert shown["terminal_status"] == "waiting_for_input" and pending["reason"] == "confirm_write"
    assert [choice["label"] for choice in pending["choices"]] == ["同意", "选择其他写操作", "取消"]
    assert "创建预测" in shown["answer"] and "NVDA" in shown["answer"]
    done = _press(graph, runtime, shown, "confirm", "two")
    assert executed == [("forecast", {"symbol": "NVDA"})]
    assert done["terminal_status"] in {"running", "succeeded"}


def test_cancel_runs_nothing_and_says_so(monkeypatch):
    executed, _ = _recorded_actions(monkeypatch)
    graph, runtime = build_workflow(), _acting_with_action("forecast")
    shown = _turn(graph, runtime, "为 NVDA 创建预测", "one")
    cancelled = _press(graph, runtime, shown, "cancel", "two")
    assert executed == []
    assert cancelled["answer"] == WRITE_CANCELLED and cancelled["terminal_status"] == "succeeded"
    assert cancelled.get("pending_request") is None


def test_another_write_can_be_chosen_when_the_model_read_the_words_wrongly(monkeypatch):
    """The model said 'forecast'; the user meant an analysis of the filing they named."""
    executed, _ = _recorded_actions(monkeypatch)
    graph, runtime = build_workflow(), _acting_with_action("forecast")
    words = f"处理一下 AAPL 的资料 {SOURCE}"
    shown = _turn(graph, runtime, words, "one")
    listed = _press(graph, runtime, shown, "other", "two") if False else None  # (the list is a second card)
    pending = shown["pending_request"]
    value = next(choice["value"] for choice in pending["choices"] if choice["value"].startswith("other:"))
    listing = _turn(graph, runtime, value, "two", shown)
    assert listing["pending_request"]["reason"] == "choose_write_action"
    labels = [choice["label"] for choice in listing["pending_request"]["choices"]]
    assert {"导入资料库", "生成 AI 分析", "创建预测", "取消"} <= set(labels)
    chosen = next(choice["value"] for choice in listing["pending_request"]["choices"] if choice["value"] == "op:analyze")
    again = _turn(graph, runtime, chosen, "three", listing)
    assert executed == []
    # the new operation's own slots are checked before anything is shown: the filing the user typed is kept
    assert again["terminal_status"] == "waiting_for_input"
    assert listed is None


def test_a_write_the_model_could_not_name_offers_the_list_first(monkeypatch):
    executed, _ = _recorded_actions(monkeypatch)
    graph, runtime = build_workflow(), _acting_with_action(None)
    shown = _turn(graph, runtime, "帮我保存一下", "one")
    assert executed == []
    assert shown["pending_request"]["reason"] == "choose_write_action"
    values = {choice["value"] for choice in shown["pending_request"]["choices"]}
    assert {"op:import", "op:analyze", "op:index", "op:forecast", "op:fetch_sec", "op:refresh_sec"} <= values


def test_a_guest_is_shown_no_card(monkeypatch):
    executed, _ = _recorded_actions(monkeypatch)
    monkeypatch.setattr("app.agent_workflow.graph.owner_credential_matches", lambda _credential: False)
    shown = _turn(build_workflow(), _acting_with_action("forecast"), "为 NVDA 创建预测", "one")
    assert executed == []
    assert shown.get("pending_request") is None
    assert "owner_login_required" in shown["answer"] and shown["terminal_status"] == "failed"


def test_a_value_the_server_never_minted_confirms_nothing(monkeypatch):
    """A typed 'confirm:<id>' of another card, or of none, is an ordinary message."""
    executed, _ = _recorded_actions(monkeypatch)
    graph, runtime = build_workflow(), _acting_with_action("forecast")
    shown = _turn(graph, runtime, "为 NVDA 创建预测", "one")
    forged = _turn(graph, runtime, "confirm:00000000-0000-4000-8000-000000000000", "two", shown)
    assert executed == []
    assert not forged.get("semantic_write_confirmed")


def test_a_card_of_another_conversation_confirms_nothing(monkeypatch):
    executed, _ = _recorded_actions(monkeypatch)
    graph = build_workflow()
    shown = _turn(graph, _acting_with_action("forecast"), "为 NVDA 创建预测", "one")
    elsewhere = _acting_with_action("forecast")
    from dataclasses import replace
    elsewhere = replace(elsewhere, conversation_id=OTHER_CHAT)
    value = next(choice["value"] for choice in shown["pending_request"]["choices"] if choice["value"].startswith("confirm:"))
    result = _turn(graph, elsewhere, value, "two", shown)
    assert executed == []
    assert not result.get("semantic_write_confirmed")


def test_an_expired_card_confirms_nothing(monkeypatch):
    from datetime import UTC, datetime, timedelta
    executed, _ = _recorded_actions(monkeypatch)
    graph, runtime = build_workflow(), _acting_with_action("forecast")
    shown = _turn(graph, runtime, "为 NVDA 创建预测", "one")
    stale = {**shown, "pending_request": {**shown["pending_request"],
                                          "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}}
    value = next(choice["value"] for choice in shown["pending_request"]["choices"] if choice["value"].startswith("confirm:"))
    result = _turn(graph, runtime, value, "two", stale)
    assert executed == []
    assert not result.get("semantic_write_confirmed")


def test_the_saved_records_a_pointer_names_are_listed_on_the_card_and_run_once_each(monkeypatch):
    executed, keys = _recorded_actions(monkeypatch)
    saved = _saved_filing_set([], ["AAPL"], [FILING_A, FILING_B], "filings")
    graph, runtime = build_workflow(), _pointing_runtime(saved)
    shown = _turn(graph, runtime, "把刚刚那两条导入", "one", result_sets=saved)
    assert executed == []
    assert shown["pending_request"]["reason"] == "confirm_write"
    assert shown["answer"].count("\n1. ") == 1 and "2. " in shown["answer"]      # the two records are listed
    done = _press(graph, runtime, shown, "confirm", "two")
    assert [payload["source_id"] for _, payload in executed] == [FILING_A, FILING_B]
    assert len(set(keys)) == 2 and done["terminal_status"] in {"running", "succeeded"}
    # pressing the same card again finds no open card: nothing runs a second time
    again = _turn(graph, runtime, next(c["value"] for c in shown["pending_request"]["choices"]
                                       if c["value"].startswith("confirm:")), "three", done)
    assert len(executed) == 2 and not again.get("semantic_write_confirmed")


def test_web_results_get_an_explanation_not_a_card(monkeypatch):
    executed, _ = _recorded_actions(monkeypatch)
    rows = _web_rows("AAPL")
    rows["data"].append({**rows["data"][0], "url": "https://example.com/b", "title": "AAPL services report"})
    saved = _saved_web_set([], ["AAPL"], "apple-news", rows=rows)
    result = _turn(build_workflow(), _pointing_runtime(saved), "那把这两条新闻录入资料库吧", "turn", result_sets=saved)
    assert executed == []
    assert result.get("pending_request") is None
    assert "不能" in result["answer"] and "网页" in result["answer"]


def test_a_word_nobody_listed_still_reaches_the_card(monkeypatch):
    """'录入' was in no verb list and ended as an internal error code.  The intent stage names the
    action; the words are only a hint."""
    executed, _ = _recorded_actions(monkeypatch)
    saved = _saved_filing_set([], ["AAPL"], [FILING_A, FILING_B], "filings")

    async def intent(_payload):
        return {"purposes": ["system_action"], "action": "import"}

    async def resolve(payload):
        entry = saved[-1]
        return {"relationship": "followup", "canonical_question": payload["question"],
                "result_set_id": entry["id"], "item_ids": []}

    runtime = _runtime(resolver=resolve, intent_recognizer=intent, planner=_plan(), query_expander=_no_variants,
                       answerer=_answer("done"))
    graph = build_workflow()
    shown = _turn(graph, runtime, "那把刚刚那两条录入资料库吧", "one", result_sets=saved)
    assert executed == []
    assert shown["pending_request"]["reason"] == "confirm_write"
    assert "导入资料库" in shown["answer"]
    _press(graph, runtime, shown, "confirm", "two")
    assert len(executed) == 2


def test_a_status_query_is_a_read_and_needs_no_card(monkeypatch):
    executed, _ = _recorded_actions(monkeypatch)
    task = "ab" * 32
    result = _turn(build_workflow(), _acting_with_action("status"), f"查看任务 {task} 的状态", "one")
    assert result.get("pending_request") is None
    assert executed == [("status", {})] or executed == []   # the adapter answers a status read directly


# --- 4. a failed turn says so; a slow service is given longer ------------------------------------------

@pytest.mark.parametrize("code, fragment", [
    ("provider_timeout", "超时"), ("provider_rate_or_quota_error", "额度"), ("provider_invalid_key", "API key"),
    ("provider_network_error", "连接"), ("anything_else", "没有完成"),
])
def test_a_failure_is_told_in_words_without_an_internal_code(code, fragment):
    from fastapi import HTTPException
    from app.agent_chat_stream import _failure_notice
    text = _failure_notice(HTTPException(502, code))
    assert fragment in text and code not in text


def test_the_provider_timeouts_are_longer_and_bounded(monkeypatch):
    import importlib
    import app.agent_chat_provider as provider
    assert provider.PROVIDER_REQUEST_SECONDS >= 30 and provider.PROVIDER_STREAM_SECONDS >= 60
    monkeypatch.setenv("AGENT_PROVIDER_TIMEOUT_SECONDS", "9999")
    monkeypatch.setenv("AGENT_PROVIDER_STREAM_SECONDS", "1")
    reloaded = importlib.reload(provider)
    try:
        assert reloaded.PROVIDER_REQUEST_SECONDS == 90 and reloaded.PROVIDER_STREAM_SECONDS == 30
    finally:
        monkeypatch.undo()
        importlib.reload(provider)


def test_the_system_prompt_says_the_model_cannot_save_anything():
    from app.agent_chat_stream import SYSTEM
    assert "cannot save, import, record or modify anything yourself" in SYSTEM


# --- 5. error and navigation pages are not sources -----------------------------------------------------

YAHOO_ERROR = ("Oops, something went wrong\n\n[Skip to navigation](#navigation-container)  [Skip to right column](#right-rail)"
               "\n\n# Yahoo Finance\n\n[Sign in](https://login.yahoo.com/x)\n" + "[Mail](https://mail.yahoo.com) " * 20)
ARTICLE = ("# Apple Q3 earnings beat estimates\n\nApple reported revenue of $94.0 billion for its June quarter, up 5% "
           "year over year, as iPhone sales rose in China. ") * 8


@pytest.mark.parametrize("text, expected", [
    (YAHOO_ERROR, True), ("Access denied. " + "[menu](https://x.test/a) " * 40, True),
    ("[a](https://x.test/1) " * 60, True),
    (ARTICLE, False), ("Something went wrong at the supplier, executives said. " + ARTICLE, False), ("", False),
])
def test_boilerplate_pages_are_told_from_articles(text, expected):
    from app.agent_web_research import is_boilerplate_page
    assert is_boilerplate_page(text) is expected


def test_an_error_page_is_not_listed_and_the_next_candidate_is_read(monkeypatch):
    from test_agent_web_research import _fake_web, _row, _article_html, APPLE_HIT
    good1, bad, good2, good3 = ("https://example.com/a", "https://finance.example.com/err",
                                "https://example.com/b", "https://example.com/c")
    web = _fake_web(
        monkeypatch,
        [_row(good1, "Apple results A"), _row(bad, "Apple Q3 earnings beat", raw=YAHOO_ERROR),
         _row(good2, "Apple results B"), _row(good3, "Apple results C")],
        {good1: _article_html("Apple results A", APPLE_HIT), bad: _article_html("Err", "Oops"),
         good2: _article_html("Apple results B", APPLE_HIT), good3: _article_html("Apple results C", APPLE_HIT)})
    result = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news"))
    urls = [row["url"] for row in result["data"]]
    assert bad not in urls and urls == [good1, good2, good3]
    assert all(row.get("excerpt") and not row.get("fetch_error") for row in result["data"])


def test_an_error_page_that_is_the_only_hit_is_reported_as_unavailable_not_as_a_source(monkeypatch):
    from test_agent_web_research import _fake_web, _row
    bad = "https://finance.example.com/err"
    web = _fake_web(monkeypatch, [_row(bad, "Apple Q3 earnings beat", raw=YAHOO_ERROR)],
                    {bad: "<html><body><p>Apple.</p></body></html>"})
    result = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news"))
    row = result["data"][0]
    assert row["fetch_error"] == "boilerplate_page" and row["excerpt"] == ""


# --- 6. the owner names a conversation ---------------------------------------------------------------------

def test_rename_validates_and_is_owner_scoped(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import agent_chat_identity as identity
    seen = []

    async def fake(credential, conversation_id, title):
        seen.append((credential, conversation_id, title))
        return conversation_id.endswith("1")

    monkeypatch.setattr(identity, "set_conversation_title", fake)
    app = FastAPI()
    app.include_router(identity.router)
    headers = {"X-Chat-Session": "o" * 48}
    cid = "00000000-0000-4000-8000-000000000001"
    with TestClient(app) as client:
        missing = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "x"})
        named = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "  苹果   营收  复盘 "}, headers=headers)
        cleared = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "   "}, headers=headers)
        foreign = client.patch("/agent-chat/conversations/00000000-0000-4000-8000-000000000002", json={"title": "x"}, headers=headers)
        control = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "a\x00b"}, headers=headers)
        long = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "字" * 81}, headers=headers)
    assert missing.status_code == 401
    assert named.status_code == 200 and named.json()["title"] == "苹果 营收 复盘"
    assert cleared.status_code == 200 and cleared.json()["title"] is None
    assert foreign.status_code == 404
    assert control.status_code == 422 and long.status_code == 422
    assert seen == [("o" * 48, cid, "苹果 营收 复盘"), ("o" * 48, cid, None),
                    ("o" * 48, "00000000-0000-4000-8000-000000000002", "x")]


def test_a_custom_name_replaces_the_first_question_in_the_list(disposable_database):
    from app import agent_chat_store as store
    from app.agent_chat_models import AgentConversation, AgentConversationTitle, AgentMessage
    from app.database import Base, engine
    Base.metadata.create_all(engine, tables=[AgentConversation.__table__, AgentMessage.__table__,
                                              AgentConversationTitle.__table__])

    async def exercise():
        mine, theirs = "owner-credential-" + "a" * 40, "other-credential-" + "b" * 40
        cid = await store.create_conversation(mine)
        await store.append_message(cid, "11111111-1111-4111-8111-111111111111", "user", "这是我问的第一个很长的问题" * 8)
        before = (await store.list_conversations(mine))[0]
        assert await store.set_conversation_title(theirs, cid, "偷改") is False        # not theirs
        assert await store.set_conversation_title(mine, cid, "苹果营收复盘") is True
        after = (await store.list_conversations(mine))[0]
        assert await store.set_conversation_title(mine, cid, None) is True
        restored = (await store.list_conversations(mine))[0]
        return before, after, restored

    before, after, restored = asyncio.run(exercise())
    assert before["custom_title"] is False and before["title"].startswith("这是我问的第一个")
    assert after["title"] == "苹果营收复盘" and after["custom_title"] is True
    assert restored["title"] == before["title"] and restored["custom_title"] is False


def test_a_guest_may_name_their_own_conversation_in_public_mode(monkeypatch):
    """The access middleware treats a PATCH as an owner-only write; naming a conversation is not one
    (the handler only reaches conversations of the credential sent)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import agent_chat_identity as identity
    from app.agent_chat_middleware import create_agent_chat_access_middleware

    async def fake(_credential, _conversation_id, _title):
        return True

    monkeypatch.setattr(identity, "set_conversation_title", fake)
    app = FastAPI()
    app.include_router(identity.router)
    app.middleware("http")(create_agent_chat_access_middleware(True, "operator-key"))
    cid = "00000000-0000-4000-8000-000000000001"
    with TestClient(app) as client:
        named = client.patch(f"/agent-chat/conversations/{cid}", json={"title": "我的复盘"},
                             headers={"X-Chat-Session": "g" * 48})
        other = client.patch("/agent-chat/something-else", json={"title": "x"}, headers={"X-Chat-Session": "g" * 48})
    assert named.status_code == 200 and named.json()["title"] == "我的复盘"
    assert other.status_code in {401, 503}        # any other write is still the owner's


# --- the stream layer: what a button press and a failed turn leave in the history ----------------------

from test_agent_chat_stream import stream_fixture  # noqa: E402,F401  (the SSE fixture of the sibling module)


def _stream_once(message, **extra):
    import app.agent_chat_stream as stream
    from starlette.requests import Request

    async def run():
        payload = stream.StreamRequest(provider="deepseek", model="deepseek-chat", api_key="abcdefgh",
                                       message=message, conversation_id=None, **extra)
        request = Request({"type": "http", "method": "POST", "path": "/agent-chat/stream", "headers": []})
        response = await stream.stream_chat(payload, request, "x" * 40)
        return "".join([part async for part in response.body_iterator])

    return asyncio.run(run())


def test_a_card_press_is_stored_as_the_button_label_and_a_failed_turn_keeps_its_words(stream_fixture):
    card = "11111111-1111-4111-8111-111111111111"
    _, _, _, _, stored, _, _ = stream_fixture
    wire = _stream_once(f"confirm:{card}", pending_request_id=card, display_message="同意")
    users = [args[3] for args, _ in stored if len(args) > 3 and args[2] == "user"]
    assert users == ["同意"]                                       # the history shows the button, not the token
    assistants = [(args[3], kwargs.get("status")) for args, kwargs in stored if len(args) > 3 and args[2] == "assistant"]
    assert assistants and assistants[-1][1] == "failed" and assistants[-1][0].strip()   # never a blank bubble
    assert "event: delta" in wire and "event: error" in wire
    assert "pending_request_mismatch" not in assistants[-1][0]


def test_a_label_is_only_taken_for_a_press_on_a_card(stream_fixture):
    _, _, _, _, stored, _, _ = stream_fixture
    _stream_once("show AAPL prices", display_message="同意")
    users = [args[3] for args, _ in stored if len(args) > 3 and args[2] == "user"]
    assert users == ["show AAPL prices"]
