"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import pytest
from app.agent_workflow.actions import requested_action

@pytest.mark.parametrize("words", [
    "请不要导入这两条", "别把那份资料导入", "不要创建预测", "What does import those two mean?",
    "解释一下导入那两条的含义", "导入这些资料需要什么权限？", "Don't import those two",
])
def test_a_sentence_that_refuses_or_asks_about_a_write_is_no_request_for_it(words):
    assert requested_action(words) is None


@pytest.mark.parametrize("words", [
    "请导入那份资料", "把刚刚那两条导入", "别急，把刚刚那两条导入", "能把刚刚那两条导入吗？", "import those two",
    "创建预测", "为 AAPL 创建预测", "导入刚刚那两条，告诉我结果是什么",
])
def test_a_plain_request_is_still_a_request(words):
    assert requested_action(words)


def test_a_headline_does_not_make_another_companys_figure_the_asked_ones():
    from app.agent_web_research import focus_excerpt
    assert "revenue rose 20%" not in focus_excerpt(
        "Samsung revenue rose 20% in 2025.", "Apple revenue news", heading="Apple revenue report")
    assert "Samsung revenue rose 20%" not in focus_excerpt(
        "Apple introduced a phone. Samsung revenue rose 20% in 2025.", "Apple revenue news")
    assert "Microsoft revenue rose" not in focus_excerpt(
        "Apple introduced a phone. Microsoft revenue rose 12% in 2025.", "Apple revenue news")
    text = "Apple introduced a phone. The company revenue rose 20% in 2025."
    assert focus_excerpt(text, "Apple revenue news") == text
