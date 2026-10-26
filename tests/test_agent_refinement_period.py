"""A follow-up the resolver calls a refinement keeps the task, not a period the user just changed.

"那前年呢？" after a FY2025 answer is a time change: the classifier reads FY2024 out of the rewritten
question, and that must reach the scope, the saved result sets and the evidence gate.  The previous
period only fills in when the turn names none.
"""
from __future__ import annotations

import asyncio

import pytest

from app.agent_workflow import direct
from app.agent_workflow.evidence import gate_row
from app.agent_workflow.state import AgentRuntimeContext

FY2025 = '2025 财年（2024-07-01 至 2025-06-30）'
FY2024 = '2024 财年（2023-07-01 至 2024-06-30）'
PREVIOUS = {'category': 'A', 'company': 'Microsoft Corporation', 'symbol': 'MSFT', 'company_mention': '微软',
            'metrics': ['营收'], 'financial_only': True, 'form': None, 'period': FY2025, '_entity_aliases': ['微软']}
HISTORY = [{'role': 'user', 'content': '去年微软的营收是多少？'},
           {'role': 'assistant', 'content': '微软 FY2025 营收为 281,724,000,000 美元 [1]。'}]


async def emit(_):
    pass


def run_refinement(question, canonical, period):
    ctx = AgentRuntimeContext(
        'custom', 'test', 'dummy',
        resolver=lambda _: {'relationship': 'refinement', 'canonical_question': canonical},
        classifier=lambda _: {'category': 'A', 'company': 'Microsoft Corporation', 'symbol': 'MSFT',
                              'company_mention': '微软', 'metrics': ['营收'], 'financial_only': True, 'period': period},
        decider=lambda *_: {'content': '没有可核验资料。'},
        executor=lambda name, args: {'ok': True, 'data': []})
    return asyncio.run(direct.run_direct_turn(question, ctx, emit=emit, messages=list(HISTORY),
                                              state={'previous_scope': dict(PREVIOUS)}))


def test_a_refinement_that_names_another_fiscal_year_keeps_it():
    result = run_refinement('那前年呢？', f'微软（MSFT）{FY2024}的营收是多少？', FY2024)
    audit = result['audit']
    assert result['previous_scope']['period'] == FY2024
    assert 'period' not in audit.get('refinement_scope_restored', [])
    assert audit['refinement_period_changed'] == [FY2025, FY2024]
    # The rest of the task is still preserved.
    assert audit['preserved_task_scope'] is True and result['previous_scope']['metrics'] == ['营收']


def test_a_refinement_that_names_no_period_still_inherits_the_previous_one():
    result = run_refinement('再核对一遍', '微软（MSFT）的营收', None)
    assert result['previous_scope']['period'] == FY2025
    assert 'period' in result['audit']['refinement_scope_restored']
    assert 'refinement_period_changed' not in result['audit']


def xbrl_row(label, value):
    """A reported-figure row worded the way ``financials._excerpt`` words it."""
    return {'symbol': 'MSFT', 'source_type': 'reported_figure', 'title': 'MSFT 10-K · ' + label.split('（')[0],
            'filed_at': '2026-07-29',
            'excerpt': (f'营业收入（Revenue，RevenueFromContractWithCustomerExcludingAssessedTax）{label}：{value:,} USD，'
                        f'约 {value / 1e8:,.2f} 亿美元，即 {value / 1e6:,.0f} 百万美元。'
                        '来源：MSFT 10-K，档案号 0001193125-26-323660，2026-07-29 提交，XBRL 披露值。')}


XBRL_ROWS = {'FY2026': xbrl_row('FY2026（2025-07-01 至 2026-06-30）', 331_839_000_000),
             'FY2025': xbrl_row('FY2025（2024-07-01 至 2025-06-30）', 281_724_000_000),
             'FY2024': xbrl_row('FY2024（2023-07-01 至 2024-06-30）', 245_122_000_000)}


@pytest.mark.parametrize('period,clean', [(FY2025, 'FY2025'), (FY2024, 'FY2024')])
def test_only_the_row_of_the_scope_period_is_free_of_a_period_note(period, clean):
    """The notes follow the scope's period, so a stale period flags the very row that was asked for."""
    reasons = {name: gate_row('微软营收', dict(row), symbol='MSFT', company='Microsoft Corporation',
                              metrics=['revenue'], period=period, soft=True)[1]
               for name, row in XBRL_ROWS.items()}
    assert {name for name, reason in reasons.items() if reason == 'sufficient_evidence'} == {clean}
    assert all(reason == 'soft:requested_period_missing' for name, reason in reasons.items() if name != clean)
