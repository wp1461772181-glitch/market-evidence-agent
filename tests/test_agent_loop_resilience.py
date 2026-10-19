"""A looping model should be redirected, not punished with an empty turn.

Three behaviours are covered here: an identical call hands its own result back instead of ending
the turn, an invalid call is told exactly which arguments are wrong instead of receiving an echo,
and a refused write no longer tells the user to call a tool by name.
"""
import asyncio, json
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

async def emit(_): pass

def call(name, **args):
    return {'tool_calls': [{'id': name, 'function': {'name': name, 'arguments': json.dumps(args)}}]}

PRICES = {'ok': True, 'data': [{'id': 'p', 'symbol': 'AMD', 'close': 100}]}


def run(steps, *, executor=None, seen=None):
    schemas = []

    def decide(messages, tools):
        schemas.append(tools)
        if seen is not None:
            seen.append(json.dumps(messages, ensure_ascii=False, default=str))
        return next(steps)

    ctx = AgentRuntimeContext('custom', 'test', 'dummy', classifier=lambda _: 'D', decider=decide,
                              executor=executor or (lambda *_: PRICES))
    return asyncio.run(direct.run_direct_turn('AMD 收盘价', ctx, emit=emit)), schemas


def test_an_identical_call_reuses_its_result_instead_of_ending_the_turn():
    steps = iter([call('query_stock_prices', symbol='AMD'), call('query_stock_prices', symbol='AMD'),
                  {'content': 'AMD 收盘价为100美元[1]。'}])
    result, _ = run(steps)
    assert result['status'] == 'succeeded' and '100' in result['answer']
    assert result['audit']['replayed_calls'] == 1
    # The repeated call never reached the executor a second time.
    assert result['audit']['tool_calls'] == 1


def test_endless_identical_calls_still_stop_with_no_progress():
    same = call('query_stock_prices', symbol='AMD')
    steps = iter([same] * (direct.MAX_REPLAYED_CALLS + 3))
    result, _ = run(steps)
    assert result['status'] != 'succeeded' and result['audit']['stop_reason'] == 'no_progress'
    assert result['audit']['replayed_calls'] == direct.MAX_REPLAYED_CALLS


def test_an_invalid_call_is_told_its_contract_and_what_it_supplied():
    seen = []
    steps = iter([call('query_stock_prices', symbol='not a symbol', limit=5),
                  call('query_stock_prices', symbol='AMD'), {'content': 'AMD 收盘价为100美元[1]。'}])
    result, _ = run(steps, seen=seen)
    assert result['status'] == 'succeeded'
    replies = [m for m in json.loads(seen[1]) if m.get('role') == 'tool']
    hints = [json.loads(m['content']) for m in replies if 'required_arguments' in m['content']]
    assert hints and 'symbol' in hints[0]['required_arguments']
    assert set(hints[0]['arguments_you_supplied']) == {'symbol', 'limit'}


def test_a_refused_write_never_names_a_tool_the_user_cannot_call():
    turn = direct.Turn('帮我把 AMD 加进研究队列', AgentRuntimeContext('custom', 'test', 'dummy'), emit, [], {})
    turn.route = 'D'
    turn.scope = {'requested_actions': ['scan_official']}
    turn.write_reasons = {'scan_official': 'write_not_proposed'}
    result = asyncio.run(turn.finish_answer('没有可执行的操作。'))
    assert 'request_action' not in result['answer']
    # Product requirement (2026-10-19): the code is audit-only; the user reads the next step.
    assert '确认卡' in result['answer'] and 'write_not_proposed' not in result['answer']
    assert result['audit']['write_outcome'] == 'refused:write_not_proposed'
