"""Behavior at the native-model boundary, not claims about model accuracy."""
import asyncio,json
import pytest
from app.agent_workflow.direct import run_direct_turn
from app.agent_workflow.state import AgentRuntimeContext

LIST='1. 远期（Forward）\n2. 期货（Futures）\n3. 期权（Option）\n4. 互换（Swap）'
HISTORY=[{'role':'user','content':'金融衍生品包括哪些？简单列举几个说明一下'}, {'role':'assistant','content':LIST}]

def run(question,*,category='B',resolver=None,answers=(),messages=None,scope=None):
    answers=iter(answers);requests=[]
    async def answerer(payload):
        requests.append(payload)
        value=next(answers,'')
        if isinstance(value,Exception):raise value
        return value
    async def execute(*_):raise AssertionError('Concept answer must not execute business tools')
    context=AgentRuntimeContext('custom','test','dummy',resolver=resolver,
        intent_recognizer=lambda _:{'category':category,'days_back':None,**(scope or {})},answerer=answerer,executor=execute)
    async def emit(_):pass
    result=asyncio.run(run_direct_turn(question,context,emit=emit,messages=messages))
    return result,requests

@pytest.mark.parametrize('bad',['',None,{'content':''},{'content':'<|tool_calls|>bad'},TimeoutError()])
def test_b_missing_or_bad_generation_retries_then_fails(bad):
    result,calls=run('解释期货',answers=[bad,bad])
    assert len(calls)==2
    assert result['status']=='failed' and result['reason']=='empty_b_answer'
    assert result['answer']=='这次没有生成回答，请重试'
    assert result['audit']['stages']['B']=={'independent_call':True,'retried':True}

def test_b_generation_recovers_once_and_does_not_store_answer_in_scope():
    result,calls=run('解释期货',answers=['','期货是标准化合约，采用保证金和每日结算。'])
    assert result['status']=='succeeded' and '保证金' in result['answer']
    assert len(calls)==2 and 'answer' not in result['previous_scope']

@pytest.mark.parametrize('term',['期货（Futures）','期权（Option）','互换（Swap）'])
def test_copied_assistant_concept_is_a_valid_referent_without_company_card(term):
    resolver=lambda _:{'relationship':'followup','canonical_question':'详细解释'+term,
        'referents':[{'mention':'第二个','text':term,'source':'assistant_previous'}]}
    result,calls=run('解释第二个',resolver=resolver,answers=['这是一种金融合约。'],messages=HISTORY)
    assert result['status']=='succeeded' and not result['pending_request']
    assert term in json.dumps(calls[0],ensure_ascii=False)
    audit=result['audit']['stages']['resolver']
    assert audit['validator']['passed'] is True and term in audit['canonical_question']

def test_forged_referent_is_rejected_before_classification():
    resolver=lambda _:{'relationship':'followup','canonical_question':'解释Palantir',
        'referents':[{'mention':'第二个','text':'Palantir','source':'assistant_previous'}]}
    result,calls=run('解释第二个',resolver=resolver,messages=HISTORY)
    assert result['status']=='waiting_for_input' and not calls
    assert result['audit']['stages']['resolver']['validator']['passed'] is False

def test_fullwidth_and_whitespace_copy_is_valid():
    resolver=lambda _:{'relationship':'followup','canonical_question':'解释期货 Futures',
        'referents':[{'mention':'第二个','text':'期货 (Ｆｕｔｕｒｅｓ)','source':'assistant_previous'}]}
    history=[{'role':'user','content':'列举衍生品'},{'role':'assistant','content':'1.远期\n2.期货  (Futures)'}]
    result,_=run('解释第二个',resolver=resolver,answers=['期货是合约。'],messages=history)
    assert result['status']=='succeeded'

def test_b_c_do_not_run_business_company_or_number_grounding():
    for category in ['B','C']:
        resolver=lambda _:{'relationship':'followup','canonical_question':'解释Futures及100美元的例子','referents':[]}
        result,_=run('详细解释',category=category,resolver=resolver,answers=['期货合约的例子。'],messages=HISTORY)
        assert result['status']=='succeeded' and not result['pending_request']

def test_a_rejects_ungrounded_classifier_company_before_tools():
    resolver=lambda _:{'relationship':'followup','canonical_question':'查询营收','referents':[]}
    result,_=run('查它的营收',category='A',resolver=resolver,messages=HISTORY,scope={'company':'Palantir','symbol':'PLTR'})
    assert result['status']=='waiting_for_input' and result['pending_request']

def test_long_reference_audit_is_bounded_and_does_not_include_api_key():
    resolver=lambda _:{'relationship':'followup','canonical_question':'解释'+('期货'*300)+'dummy','referents':[]}
    result,_=run('详细解释',resolver=resolver,answers=['期货是合约。'],messages=HISTORY)
    stages=result['audit']['stages']
    assert all(len(json.dumps(v,ensure_ascii=False,separators=(',',':')))<=400 for v in stages.values())
    assert 'dummy' not in json.dumps(stages)
