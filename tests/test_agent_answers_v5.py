import asyncio
import pytest
from app.agent_workflow.direct import Turn
from app.agent_workflow.state import AgentRuntimeContext

def turn():
    t=Turn('AMD 股价',AgentRuntimeContext('custom','test','dummy'),None,[],{})
    t.route='A'
    t.citations=[{'ordinal':1,'quote':'100','source_type':'saved_price','symbol':'AMD'}, {'ordinal':2,'quote':'80','source_type':'saved_price','symbol':'AMD'}]
    return t

@pytest.mark.parametrize('bad',['AMD 收盘价为999美元[1]。','公司CEO已辞职[1]。','不存在的事实[99]。'])
def test_bad_passage_does_not_erase_supported_passage(bad):
    result=turn().finish('AMD 收盘价为100美元[1]。\n'+bad)
    assert '100美元' in result['answer'] and '[1]' in result['answer']
    assert '999' not in result['answer'] and '[99]' not in result['answer'] and 'CEO' not in result['answer']
    assert result['status']=='partially_succeeded'

def test_stock_source_does_not_require_a_stock_keyword_in_every_sentence():
    result=turn().finish('AMD 收盘价为100美元[1]。\n以上为历史记录[1]。')
    assert result['status']=='succeeded' and '以上为历史记录' in result['answer']

def test_ordinary_uncited_fact_cannot_survive_a_supported_price_sentence():
    t=turn();t.observations=[{'accepted_evidence':True}]
    result=t.finish('AMD 收盘价为100美元[1]。\nAMD revenue declined materially.')
    assert result['status']=='partially_succeeded' and '100美元' in result['answer']
    assert 'declined' not in result['answer'] and result['reason']=='missing_citation'

def test_bare_factual_number_is_checked_against_the_attached_source():
    result=turn().finish('AMD revenue was 999 [1].')
    assert '999' not in result['answer'] and result['reason']=='unsupported_numeric_claim'

def test_source_bound_deterministic_calculation_exposes_formula_and_inputs():
    from app.agent_workflow.calculation import calculate
    result=calculate({'expression':'(a-b)/b*100','inputs':[{'name':'a','value':'100','source_ordinal':1},{'name':'b','value':'80','source_ordinal':2}],'decimals':2},turn().citations)
    assert result['value']=='25.00'
    assert result['parents']==[1,2] and result['expression']=='(a-b)/b*100'

@pytest.mark.parametrize('expression,value',[('a+1','999'),('__import__("os")','100'),('a/0','100'),('a**100000','100')])
def test_calculation_rejects_ungrounded_inputs_and_unsafe_or_invalid_arithmetic(expression,value):
    from app.agent_workflow.calculation import calculate
    with pytest.raises(ValueError):
        calculate({'expression':expression,'inputs':[{'name':'a','value':value,'source_ordinal':1}],'decimals':2},turn().citations)


def test_a_refused_calculation_hands_back_the_numbers_the_source_holds():
    """'calculation_input_unbound' alone makes the model guess again; the candidates end the loop."""
    import asyncio, json
    from app.agent_workflow import direct
    from app.agent_workflow.calculation import bindable_values
    citations = [{'ordinal': 1, 'quote': 'Total revenue was 331,839 million for the year.'}]
    # The unit is part of the number: '331,839 million' binds as 331839000000, which is exactly the
    # value the model has to supply, and exactly what it could not guess.
    assert bindable_values(citations, {'inputs': [{'name': 'a', 'source_ordinal': 1}]}) == {'1': ['331839000000.0']}
    steps = iter([{'tool_calls': [{'id': 'c1', 'function': {'name': 'calculate', 'arguments': json.dumps(
        {'expression': 'a*2', 'inputs': [{'name': 'a', 'value': '123', 'source_ordinal': 1}]})}}]}])
    from app.agent_workflow.state import AgentRuntimeContext
    async def emit(_): pass
    turn = direct.Turn('算一下', AgentRuntimeContext('custom', 'test', 'dummy'), emit, [], {})
    turn.route = 'A'
    turn.citations = citations
    turn.observations = [{'accepted_evidence': True}]
    outcome = asyncio.run(turn.execute_read('calculate', {'expression': 'a*2', 'inputs': [
        {'name': 'a', 'value': '123', 'source_ordinal': 1}], 'decimals': 2}))
    assert outcome['ok'] is False and outcome['error_code'] == 'calculation_input_unbound'
    assert outcome['bindable_values'] == {'1': ['331839000000.0']}


def test_a_chinese_search_always_considers_model_written_variants(monkeypatch):
    """An English filing answers a Chinese metric under English wording only."""
    import asyncio
    from app import agent_tools
    calls = []

    async def fake_retrieve(symbol, keyword, limit, source_id=None):
        calls.append(keyword)
        return {'ok': True, 'data': [] if keyword != 'cloud revenue' else [{'id': 'c1'}], 'as_of': None}

    async def fake_variants(keyword):
        return [keyword, 'cloud revenue']

    monkeypatch.setattr('app.knowledge.query_understanding.query_variants', fake_variants)
    result = asyncio.run(agent_tools._expanded_retrieval(fake_retrieve, 'MSFT', '云业务收入', 3, None))
    assert 'cloud revenue' in calls and len(result['data']) == 1
