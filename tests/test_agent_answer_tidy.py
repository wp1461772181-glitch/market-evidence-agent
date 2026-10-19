"""Dropping an unsupported line must not leave its frame behind."""
from app.agent_workflow.answer_tidy import tidy_answer

def test_tidy_drops_a_table_left_with_only_its_header():
    assert tidy_answer('| 项目 | FY2026 |\n|---|---|\n\n结论 [1]')=='结论 [1]'

def test_tidy_keeps_a_table_that_still_has_rows():
    text='| 项目 | FY2026 |\n|---|---|\n| 营业收入 | 331,839 [1] |'
    assert tidy_answer(text)==text

def test_tidy_drops_an_empty_heading_and_a_lead_in_with_nothing_after_it():
    text='## 二、对比\n\n## 三、公开报道\n公开报道披露：\n\n## 四、结论\n营收 331,839 [1]'
    assert tidy_answer(text)=='## 四、结论\n营收 331,839 [1]'

def test_tidy_keeps_a_lead_in_that_introduces_content():
    text='口径如下：\n- 营收 331,839 [1]'
    assert tidy_answer(text)==text

def test_tidy_drops_a_bold_heading_with_nothing_under_it():
    assert tidy_answer('**结构口径参考**（同一利润表）[2]：\n\n**结论**\n营收 331,839 [1]')=='**结论**\n营收 331,839 [1]'

def test_tidy_closes_a_bold_marker_cut_in_half():
    assert tidy_answer('** 没有可核验资料')=='没有可核验资料'

def test_tidy_collapses_runs_of_blank_lines():
    assert tidy_answer('甲 [1]\n\n\n\n乙 [1]')=='甲 [1]\n\n乙 [1]'
