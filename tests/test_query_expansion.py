"""Query 扩写 on the retrieval side: the model writes the variants, the rules are only the fallback.

The course's rule is that expansion happens when searching and is thrown away, and that the wording
variants come from the model because that is the only thing that generalises phrasing.  What the
model returns is copy: a variant may not smuggle in a ticker, a figure or a year the question did
not contain, and the user's own wording is always searched first.
"""
import asyncio

import pytest

from app.knowledge import query_understanding as qu


def run(coro):
    return asyncio.run(coro)


def test_an_exact_source_read_is_not_expanded_by_the_search_path():
    """Opening one saved source is not a wording problem; only a text search can be widened."""
    from app import agent_tools
    calls = []
    async def one(symbol, query, limit, source_id=None):
        calls.append(query)
        return {"ok": True, "data": [], "source": "knowledge", "as_of": None}
    def explode(_query):
        raise AssertionError("an exact source read must not expand")
    with qu.use_expander(explode):
        run(agent_tools._expanded_retrieval(one, "AAPL", "AAPL 10-Q", 5, "0f0e6c1e-0000-4000-8000-000000000000"))
    assert calls == ["AAPL 10-Q"]


def test_the_rule_fallback_only_splits_a_compound_financial_question():
    assert qu.expanded_queries("AAPL 最新财报") == ["AAPL 最新财报"]
    queries = qu.expanded_queries("AAPL 2025 营收和现金流")
    assert len(queries) == 3 and "营收" in queries[1] and "现金流" in queries[2]


def test_the_search_path_costs_no_model_call_when_the_plain_query_hit():
    from app import agent_tools
    calls = []
    async def hit(symbol, query, limit, source_id=None):
        calls.append(query)
        return {"ok": True, "data": [{"id": "1"}], "source": "knowledge", "as_of": None}
    def explode(_query):
        raise AssertionError("a hit must not pay for an expansion")
    with qu.use_expander(explode):
        found = run(agent_tools._expanded_retrieval(hit, "AAPL", "AAPL 营收", 5, None))
    assert calls == ["AAPL 营收"] and found["data"] == [{"id": "1"}]


def test_a_miss_is_widened_with_the_models_variants_and_the_rows_are_deduplicated():
    from app import agent_tools
    calls = []
    async def lookup(symbol, query, limit, source_id=None):
        calls.append(query)
        return {"ok": True, "data": [{"id": "shared"}] if query == "宽的说法" else [],
                "source": "knowledge", "as_of": None}
    async def expander(_query):
        return ["宽的说法", "宽的说法二"]
    with qu.use_expander(expander):
        found = run(agent_tools._expanded_retrieval(lookup, "AAPL", "AAPL 营收", 5, None))
    assert calls == ["AAPL 营收", "宽的说法", "宽的说法二"]   # every variant, after the plain miss
    assert found["data"] == [{"id": "shared"}]              # and the rows are still deduplicated


def test_the_model_writes_the_variants_and_the_original_is_searched_first():
    async def expander(query):
        return ["苹果 营业收入", "苹果 现金流", query]
    with qu.use_expander(expander):
        variants = run(qu.query_variants("苹果的营收和现金流"))
    assert variants[0] == "苹果的营收和现金流"
    assert variants[1:] == ["苹果 营业收入", "苹果 现金流"]
    assert len(variants) <= qu.MAX_VARIANTS


def test_a_variant_may_not_add_a_ticker_a_figure_or_a_year():
    kept = qu.clean_variants("AAPL 2024 年营收", [
        "AAPL revenue 2024",         # same names and numbers, different words: kept
        "MSFT 2024 revenue",         # a company the question never named: dropped
        "AAPL 2023 年营收",           # a year the question never named: dropped
        "AAPL 2024 年营收 12%",       # a figure the question never named: dropped
        "",                          # nothing: dropped
        "x" * (qu.MAX_VARIANT_CHARS + 1),  # not a query: dropped
    ])
    assert kept == ["AAPL 2024 年营收", "AAPL revenue 2024"]


def test_variants_are_bounded_deduplicated_and_never_repeat_the_question():
    kept = qu.clean_variants("英伟达 数据中心 营收", [
        "英伟达数据中心营收", "英伟达数据中心营收", " 英伟达 数据中心 收入  ", "数据中心收入", "英伟达营收"])
    assert kept == ["英伟达 数据中心 营收", "英伟达数据中心营收", "英伟达 数据中心 收入"]   # three, deduplicated


def test_a_failed_or_unreadable_expansion_falls_back_instead_of_failing_the_search():
    for broken in ("not json", "{\"queries\": \"one\"}", "{\"answers\": []}", "", None):
        with pytest.raises(ValueError):
            qu.parse_variants(broken)

    async def failing(_query):
        raise RuntimeError("provider is down")
    with qu.use_expander(failing):
        assert run(qu.query_variants("英伟达营收")) == ["英伟达营收"]


def test_without_a_model_the_rule_fallback_still_splits_a_two_topic_question():
    assert run(qu.query_variants("苹果的营收和现金流")) == qu.clean_variants("苹果的营收和现金流", qu.expanded_queries("苹果的营收和现金流"))
    assert run(qu.query_variants("英伟达营收")) == ["英伟达营收"]


def test_the_expander_is_scoped_to_the_block_that_installed_it():
    async def expander(_query):
        return ["别的说法"]
    with qu.use_expander(expander):
        assert run(qu.query_variants("英伟达营收"))[1:] == ["别的说法"]
    assert run(qu.query_variants("英伟达营收")) == ["英伟达营收"]   # no model: the fallback, not a leak


def test_the_prompt_states_the_rules_that_the_filter_enforces():
    assert "at most 3" in qu.EXPANSION_PROMPT or str(qu.MAX_VARIANTS) in qu.EXPANSION_PROMPT
    assert "add none" in qu.EXPANSION_PROMPT and "do not answer" in qu.EXPANSION_PROMPT
    assert "{\"queries\"" in qu.EXPANSION_PROMPT
