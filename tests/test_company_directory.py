"""The company directory: one table of the companies the product knows by name, read by the slot
reader, web research and the evidence gate.

Found by asking the agent for "美光的新闻": the slot reader knew eight companies, the planner (a
model) knew the ticker, and the gate refused a ticker the user's words did not carry.
"""
import re

import pytest

from app.agent_web_research import ENTITY_ALIASES
from app.agent_workflow.graph import _names_alias, build_workflow, deterministic_confidence_gate
from app.agent_workflow.request_resolution import ALIASES, extract_slots
from app.agent_workflow.semantic import CHAT_ACRONYMS, NON_TICKERS
from app.company_directory import COMPANIES
from app.symbols import normalize_market_symbol
from test_agent_semantic_regressions import (_answer, _new, _no_variants, _plan, _purposes, _runtime, _task, _turn,
                                             _web_rows)

# What the three old tables held: these names and their order must not change.
OLD_ALIASES = {"苹果": "AAPL", "蘋果": "AAPL", "apple": "AAPL", "微软": "MSFT", "microsoft": "MSFT",
               "英伟达": "NVDA", "nvidia": "NVDA", "超微": "AMD", "amd": "AMD", "谷歌": "GOOGL", "google": "GOOGL",
               "亚马逊": "AMZN", "amazon": "AMZN", "特斯拉": "TSLA", "tesla": "TSLA", "meta": "META"}
OLD_WEB_ALIASES = {"AAPL": ("apple", "苹果", "蘋果"), "AMD": ("advanced micro devices", "超微", "amd"),
                   "MSFT": ("microsoft", "微软"), "NVDA": ("nvidia", "英伟达"),
                   "GOOGL": ("google", "alphabet", "谷歌"), "AMZN": ("amazon", "亚马逊"),
                   "TSLA": ("tesla", "特斯拉"), "META": ("meta", "脸书")}


# --- the table -----------------------------------------------------------------------------------------

def test_the_directory_keeps_what_the_old_tables_held():
    for alias, symbol in OLD_ALIASES.items():
        assert ALIASES[alias] == symbol
    for symbol, names in OLD_WEB_ALIASES.items():
        assert set(names) <= set(COMPANIES[symbol])
    assert list(COMPANIES)[:8] == ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN", "TSLA", "META"]  # symbol order
    assert ENTITY_ALIASES is COMPANIES


def test_every_entry_is_a_valid_ticker_and_every_name_names_one_company():
    seen: dict[str, str] = {}
    for symbol, names in COMPANIES.items():
        assert normalize_market_symbol(symbol) == symbol, symbol
        assert symbol not in NON_TICKERS and symbol not in CHAT_ACRONYMS, symbol
        assert names, symbol
        for name in names:
            assert name == name.strip() and (not name.isascii() or name == name.lower()), name
            assert name not in seen, f"{name!r} names both {seen[name]} and {symbol}"
            seen[name] = symbol
    assert ALIASES == seen


def test_a_chinese_name_is_never_a_piece_of_an_ordinary_phrase():
    """Chinese has no word boundaries; each of these phrases holds a listed name inside it."""
    for phrase in ("高通胀对股市的影响", "京东方的新闻", "美国银行业的新闻", "百事通", "应用材料科学"):
        assert extract_slots(phrase)["symbols"] == [], phrase


# --- the slot reader -----------------------------------------------------------------------------------

@pytest.mark.parametrize("words, symbols", [
    ("帮我搜一下美光的新闻", ["MU"]),
    ("搜一下 Micron 的新闻", ["MU"]),
    ("search the latest news about Micron Technology", ["MU"]),
    ("英特尔和高通公司的营收", ["INTC", "QCOM"]),
    ("Snowflake 的净收入留存率", ["SNOW"]),
    ("alphabet 营收", ["GOOGL"]),                      # the web table's name, now read everywhere
    ("苹果和美光的营收", ["AAPL", "MU"]),               # the old companies keep their place
    ("帮我搜一下 MU 的新闻", ["MU"]),                   # a typed ticker still works
    ("我想看看 community 的看法", []),                  # "mu" is not Micron
    ("什么是 intelligence", []),
])
def test_the_slot_reader_names_the_company_in_the_words(words, symbols):
    assert extract_slots(words)["symbols"] == symbols


# --- the evidence gate ---------------------------------------------------------------------------------

def _result(text, title="Market news"):
    return {"ok": True, "source": "public web search and fetched page excerpts", "source_type": "web",
            "data": [{"title": title, "url": "https://example.com/a", "excerpt": text,
                      "published_at": "2026-10-12T00:00:00Z"}]}


def test_a_latin_name_counts_as_a_whole_word_only():
    assert _names_alias("mu", "micron (mu) shares fell") is True
    assert _names_alias("mu", "the community reacted") is False
    assert _names_alias("meta", "metadata is not a company") is False
    assert _names_alias("美光", "美光公布了新的财报") is True


def test_the_gate_wants_the_asked_company_stated_by_the_page():
    question = "latest news about Micron"
    about = _result("Micron (MU) shares fell after the results; the company guided lower.")
    other = _result("The community reacted to the music festival results with strong sales.")
    assert deterministic_confidence_gate(question, about, verified_symbols=["MU"])[0] is True
    passed, reason = deterministic_confidence_gate(question, other, verified_symbols=["MU"])
    assert (passed, reason) == (False, "requested_entity_missing")


# --- the planner gate ----------------------------------------------------------------------------------

def test_a_company_in_the_directory_is_searched_without_a_ticker_typed():
    calls = []

    async def execute(name, args):
        calls.append((name, args))
        return _web_rows("MU")

    runtime = _runtime(resolver=_new, intent_recognizer=_purposes("web_research"),
                       planner=_plan(_task("news", "web_research", "web_research", ["MU"], "美光 新闻")),
                       query_expander=_no_variants, executor=execute, answerer=_answer("美光的新闻 [1]。"))
    final = _turn(build_workflow(), runtime, "帮我搜一下美光的新闻", "turn")
    assert calls == [("web_research", {"query_or_url": "美光 新闻"})]
    assert final["terminal_status"] == "succeeded"


def test_a_ticker_the_words_do_not_carry_is_asked_for_not_guessed():
    calls = []

    async def execute(name, args):
        calls.append((name, args))
        return _web_rows("FBRX")

    runtime = _runtime(resolver=_new, intent_recognizer=_purposes("web_research"),
                       planner=_plan(_task("news", "web_research", "web_research", ["FBRX"], "Foobar Robotics news")),
                       query_expander=_no_variants, executor=execute, answerer=_answer("never reached"))
    final = _turn(build_workflow(), runtime, "帮我搜一下 Foobar Robotics 的新闻", "turn")
    assert calls == []                                              # nothing was searched on the model's say-so
    assert final["gate_reason"] == "plan_company_unconfirmed"
    assert "FBRX" in final["answer"] and "股票代码" in final["answer"]
    assert final["terminal_status"] == "failed"


def test_the_user_writing_the_code_grounds_it():
    calls = []

    async def execute(name, args):
        calls.append((name, args))
        return _web_rows("FBRX")

    runtime = _runtime(resolver=_new, intent_recognizer=_purposes("web_research"),
                       planner=_plan(_task("news", "web_research", "web_research", ["FBRX"], "FBRX news")),
                       query_expander=_no_variants, executor=execute, answerer=_answer("FBRX news [1]."))
    final = _turn(build_workflow(), runtime, "搜一下 FBRX 的新闻", "turn")
    assert calls == [("web_research", {"query_or_url": "FBRX news"})]
    assert final["terminal_status"] == "succeeded"


def test_a_plan_for_another_purpose_is_still_a_scope_mismatch_not_a_company_question():
    runtime = _runtime(resolver=_new, intent_recognizer=_purposes("saved_data"),
                       planner=_plan(_task("news", "web_research", "web_research", ["AAPL"], "AAPL news")),
                       query_expander=_no_variants, answerer=_answer("never reached"))
    final = _turn(build_workflow(), runtime, "AAPL 的最新行情", "turn")
    assert final["gate_reason"] == "plan_scope_mismatch"
    assert not re.search(r"股票代码", final["answer"])
