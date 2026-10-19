"""The tool loop reads the conversation it is in, not only the one-sentence rewrite."""
import asyncio,json
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

async def emit(_):pass
HISTORY=[{'role':'user','content':'查 MSFT 最近一期年报的营业收入'},
         {'role':'assistant','content':'MSFT FY2026 营业收入为 331,839 百万美元 [1]。'},
         {'role':'user','content':'它同比怎么样？'}]

def _capture():
    seen=[]
    def decide(messages,tools):seen.append(messages);return {'content':'好的。'}
    return seen,decide

def test_decider_sees_prior_turns_the_users_words_and_the_rewrite_as_a_hint():
    seen,decide=_capture()
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'followup','canonical_question':'MSFT 最近一期年报营业收入的同比变化','referents':[]},
        classifier=lambda _:'D',decider=decide)
    asyncio.run(direct.run_direct_turn('它同比怎么样？',ctx,emit=emit,messages=HISTORY))
    messages=seen[0]
    users=[m['content'] for m in messages if m['role']=='user']
    assert users[0]=='查 MSFT 最近一期年报的营业收入' and users[-1]=='它同比怎么样？'
    assert any(m['role']=='assistant' and m['content'].startswith('MSFT FY2026') for m in messages)
    assert any(m['role']=='system' and 'MSFT 最近一期年报营业收入的同比变化' in m['content'] for m in messages)

def test_result_catalogue_shows_what_each_earlier_item_said():
    ctx=AgentRuntimeContext('custom','test','dummy',owner_hash='o',conversation_id='c')
    state={'result_sets':[{'id':'s1','owner':'o','conversation':'c','tool':'query_sec_filings','scope':{'symbol':'MSFT'},
        'rows':[{'title':'MSFT 10-K','id':'f1','source_type':'official_filing','published_at':'2026-07-29','excerpt':'INCOME STATEMENTS Total revenue 331,839 281,724'}]}]}
    turn=direct.Turn('它同比怎么样？',ctx,emit,HISTORY,state)
    catalogue=next(m['content'] for m in turn.tool_messages() if m['role']=='system' and m['content'].startswith('Server result catalogue'))
    assert 'Total revenue 331,839' in catalogue and '2026-07-29' in catalogue

def test_a_rewrite_with_an_unfounded_year_falls_back_to_the_users_words():
    seen,decide=_capture()
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'followup','canonical_question':'MSFT 2019 年报营业收入同比','referents':[]},
        classifier=lambda _:'D',decider=decide)
    result=asyncio.run(direct.run_direct_turn('它同比怎么样？',ctx,emit=emit,messages=HISTORY))
    assert result['status']!='waiting_for_input'
    assert result['audit']['stages']['resolver']['validator']['rejected_reason']=='reference_period_unbound'
    assert not any('2019' in m['content'] for m in seen[0] if m['role']=='system' and '仅供参考' in m['content'])

def test_a_failing_resolver_with_history_does_not_stop_the_turn():
    seen,decide=_capture()
    def broken(_):raise ValueError('resolver_down')
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=broken,classifier=lambda _:'D',decider=decide)
    result=asyncio.run(direct.run_direct_turn('它同比怎么样？',ctx,emit=emit,messages=HISTORY))
    assert result['status']!='waiting_for_input' and seen

def test_a_reference_with_nothing_to_point_at_still_asks():
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=lambda *_:{'content':'好的。'})
    result=asyncio.run(direct.run_direct_turn('解释第二个',ctx,emit=emit,messages=[{'role':'user','content':'解释第二个'}]))
    assert result['status']=='waiting_for_input'
