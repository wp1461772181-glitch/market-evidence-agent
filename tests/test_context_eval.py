"""The retention evaluation's data and arms (no model): the benchmark is only worth quoting if the
conversation is long enough, every planted fact sits where it is claimed to, and the numbers it
prints follow from the code a turn runs."""
import json

import pytest

from app import agent_context as layers
from scripts import eval_context_retention as ev


@pytest.fixture(scope="module")
def built():
    rows, tokens = ev.build_conversation(40)
    return rows, tokens


def test_the_conversation_is_far_longer_than_any_budget(built):
    rows, _ = built
    total = sum(layers.row_bytes(r) for r in rows)
    assert len(layers.complete_turns(rows)) == 40
    assert total > 2 * ev.budget_parts(64)["total"] and total > 2 * ev.budget_parts(128)["budget"] - 1
    assert [r["sequence"] for r in rows] == list(range(1, len(rows) + 1))


def test_every_fact_is_planted_once_in_the_turn_and_speaker_it_claims(built):
    rows, tokens = built
    for key, fact in ev.FACTS.items():
        carriers = [r for r in rows if tokens[key] in (r["content"] or "")]
        assert len(carriers) == 1, (key, len(carriers))                       # nothing else says it
        (row,) = carriers
        assert row["role"] == fact["who"] and row["turn_id"] == str(__import__("uuid").UUID(int=fact["turn"]))
        assert tokens[key] in fact["text"]
    assert len({fact["turn"] for fact in ev.FACTS.values()}) == len(ev.FACTS)


def test_the_arms_follow_the_budgets(built):
    rows, tokens = built
    today = ev.arm_today(rows)
    assert ev.message_bytes(today) <= ev.TODAY_WINDOW + 200 and sum(ev.facts_in(ev.transcript(today), tokens).values()) == 0
    parts = ev.budget_parts(64)
    window = ev.arm_window(rows, parts["total"])
    assert ev.message_bytes(window) <= parts["total"] + 200
    messages, context = ev.arm_layers(rows, 64, [])
    assert context["l1_turns"] >= 1 and context["half_turns"]
    present = ev.facts_in(ev.transcript(messages), tokens)
    assert all(present.values()), [k for k, v in present.items() if not v]    # 40 turns fit the half-compressed layer
    assert ev.message_bytes(messages) <= parts["total"] + 1000
    # the same conversation in a small budget needs a summary for what overflowed
    small, small_context = ev.arm_layers(rows, 32, [])
    assert small_context["overflow"] and not all(ev.facts_in(ev.transcript(small), tokens).values())
    with_note, _ = ev.arm_layers(rows, 32, [{"seq": 1, "from_sequence": 1, "upto_sequence": small_context["overflow"][-1].last_sequence,
                                              "content": "用户要求只看 10-K，周三下午三点前交付。"}])
    assert with_note[-1]["role"] == "user" and with_note[-1]["content"].startswith(layers.MEMORY_MARKER)


def test_the_size_rule_is_the_one_the_history_window_has_always_used(built):
    from app import agent_chat_store as store

    class Row:                       # an ORM-shaped stored message
        def __init__(self, row):
            self.role, self.content, self.tool_calls = row["role"], row["content"], row["tool_calls"]
            self.tool_call_id, self.status, self.turn_id = row["tool_call_id"], row["status"], row["turn_id"]

    rows, _ = built
    for row in rows[:12]:
        assert layers.row_bytes(row) == store._message_bytes(Row(row))


def test_the_report_names_what_it_measured_and_what_it_does_not_claim(built):
    import asyncio
    import argparse
    args = argparse.Namespace(turns=40, budgets=[64], offline=True, runs=0, provider="deepseek", model="m",
                              concurrency=1, recall_arms=[], recall_budgets=[], env_file=None)
    result = asyncio.run(ev.run(args))
    report = ev.render_report([result])
    assert "构造的" in report and "不是线上数据" in report and "10/10" in report
    assert json.dumps(result, ensure_ascii=False)                              # plain data, nothing to hide
