"""What the tool loop is told, and when a turn stops gathering and answers."""
import asyncio,json,time
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

async def emit(_):pass
PREVIOUS_INSTRUCTION_CHARS=4505   # the guest instruction before the rewrite

def _instruction(credential='guest',closing=False):
    turn=direct.Turn('q',AgentRuntimeContext('custom','test','dummy',identity_credential=credential),emit,[],{})
    return turn.tool_messages(closing)[0]['content']

def test_instruction_forbids_internal_field_names_in_answers():
    text=_instruction()
    assert '不要在回答里写内部字段名' in text and 'gate_note' in text and 'partial_excerpt' in text

def test_instruction_is_shorter_than_the_patch_list_it_replaces():
    assert len(_instruction())<=int(PREVIOUS_INSTRUCTION_CHARS*0.7)

def test_instruction_asks_when_a_reference_has_two_candidates_instead_of_picking_one():
    text=_instruction()
    assert 'ask_user' in text and 'act on the more recent one by default' not in text

def test_instruction_points_figures_to_reported_xbrl():
    assert 'query_financials' in _instruction()

def test_guest_and_owner_are_told_who_may_write():
    assert '游客' in _instruction('guest') and '所有者' in _instruction('guest')

def test_classifier_prompt_routes_a_process_discussion_to_the_product():
    assert '"不要保存任何网页，我只想讨论流程" → D' in direct.CLASSIFY_PROMPT

def test_turn_soft_cap_answers_from_the_evidence_already_gathered():
    calls=[];closings=[]
    def decide(messages,tools):
        calls.append(1)
        turn.started=time.monotonic()-100
        return {'tool_calls':[{'id':str(len(calls)),'function':{'name':'query_stock_prices','arguments':json.dumps({'symbol':'AMD','limit':len(calls)})}}]}
    def answer(messages):closings.append(1);return 'AMD 已保存收盘价 100 美元 [1]。'
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=decide,answerer=answer,
        executor=lambda *_:{'ok':True,'data':[{'id':'p','symbol':'AMD','close':100}]})
    turn=direct.Turn('AMD 收盘价',ctx,emit,[],{})
    result=asyncio.run(turn.run())
    assert calls==[1] and closings==[1]
    assert result['audit']['stop_reason']=='turn_budget' and result['status']=='succeeded'
    assert result['answer'].startswith('AMD 已保存收盘价 100 美元 [1]')
