"""A confirmed write is reported in the language the user asked in, naming what was written.

Regression: pressing a card sends only ``confirm:<token>``. The execution turn read that value as the
request, so a Chinese request was answered in English and the material had no name to report.
"""
import asyncio, json
from app.agent_workflow.direct import Turn
from app.agent_workflow.state import AgentRuntimeContext

CARD = {'id': 'req-1', 'reason': 'write_confirmation', 'original_question': '去拉取一下博通的 SEC 数据',
        'display': {'title': '确认登记并拉取 SEC 官方原文', 'targets': [{
            'symbol': 'AVGO', 'title': 'Broadcom Inc. 10-Q', 'form': '10-Q', 'web_date': '2026-09-10',
            'accession_number': '0001730168-26-000080',
            'url': 'https://www.sec.gov/Archives/edgar/data/1730168/000173016826000080/avgo-20260802.htm'}]},
        'choices': [{'label': '同意', 'value': 'confirm:tok'}]}

EXECUTED = {'ok': True, 'data': [{'status': 'succeeded', 'source_id': '8c1f0f4e-0000-4000-8000-000000000000'}]}


def confirmation_turn(answer, *, cards=None, raises=False):
    seen = []

    async def model(messages, tool_schemas, **kwargs):
        seen.extend(messages)
        if raises:
            raise RuntimeError('provider unavailable')
        return {'content': answer}

    turn = Turn('confirm:tok', AgentRuntimeContext('custom', 'test', 'dummy'), None, [],
                {'pending_request': CARD if cards is None else cards, 'requested_pending_id': 'req-1'})
    turn.model = model
    return turn, seen


def test_the_report_is_asked_for_in_the_language_of_the_request_not_the_button():
    turn, seen = confirmation_turn('博通（AVGO）的 10-Q 原文已登记并拉取完成。')
    result = asyncio.run(turn.report_execution(EXECUTED))
    instruction = seen[0]['content']
    assert 'in Chinese' in instruction and 'in English' not in instruction
    payload = json.loads(seen[1]['content'])
    assert payload['user_request'] == '去拉取一下博通的 SEC 数据'
    assert payload['what_was_targeted'][0]['title'] == 'Broadcom Inc. 10-Q'
    assert payload['what_was_targeted'][0]['symbol'] == 'AVGO'
    assert result['answer'] == '博通（AVGO）的 10-Q 原文已登记并拉取完成。'


def test_the_report_names_nothing_beyond_the_recorded_facts():
    turn, seen = confirmation_turn('博通（AVGO）的 10-Q 原文已登记并拉取完成。')
    asyncio.run(turn.report_execution(EXECUTED))
    payload = json.loads(seen[1]['content'])
    assert payload['executed_operations'] == [{'status': 'succeeded'}]


def test_the_generated_report_is_normalized_to_simplified_chinese():
    turn, _ = confirmation_turn('博通（AVGO）的 10-Q 原文已登記並拉取完成。')
    result = asyncio.run(turn.report_execution(EXECUTED))
    assert result['answer'] == '博通（AVGO）的 10-Q 原文已登记并拉取完成。'


def test_a_report_the_model_never_wrote_still_names_the_confirmed_material():
    turn, _ = confirmation_turn('', raises=True)
    result = asyncio.run(turn.report_execution(EXECUTED))
    assert result['answer'] == '已完成你确认的操作：\n- Broadcom Inc. 10-Q（已完成）'


def test_a_filing_is_named_by_its_issuer_and_form_not_by_its_accession_number():
    turn = Turn('去拉取一下博通的 SEC 数据', AgentRuntimeContext('custom', 'test', 'dummy'), None, [], {})
    discovery = {'form': '10-Q', 'filed_at': '2026-09-10', 'accession_number': '0001730168-26-000080'}
    assert turn.filing_title(discovery, 'AVGO') == 'AVGO 10-Q'
    turn.scope = {'company': 'Broadcom Inc.', 'symbol': 'AVGO'}
    assert turn.filing_title(discovery, 'AVGO') == 'Broadcom Inc. 10-Q'
    turn.scope = {}
    assert turn.filing_title({'form': '8-K'}, '') == '8-K'


def test_without_a_kept_request_the_button_value_is_all_that_is_known():
    turn, seen = confirmation_turn('The filing was retrieved.')
    turn.state = {'pending_request': {k: v for k, v in CARD.items() if k != 'original_question'},
                  'requested_pending_id': 'req-1'}
    asyncio.run(turn.report_execution(EXECUTED))
    assert 'in English' in seen[0]['content']
    assert json.loads(seen[1]['content'])['user_request'] == 'confirm:tok'
