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

def test_decider_sees_prior_turns_and_only_the_canonical_question():
    seen,decide=_capture()
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'followup','canonical_question':'MSFT 最近一期年报营业收入的同比变化','referents':[]},
        classifier=lambda _:'D',decider=decide)
    asyncio.run(direct.run_direct_turn('它同比怎么样？',ctx,emit=emit,messages=HISTORY))
    messages=seen[0]
    users=[m['content'] for m in messages if m['role']=='user']
    assert users[0]=='查 MSFT 最近一期年报的营业收入' and users[-1]=='MSFT 最近一期年报营业收入的同比变化'
    assert any(m['role']=='assistant' and m['content'].startswith('MSFT FY2026') for m in messages)
    assert not any(m['role']=='system' and ('用户本轮原话' in m['content'] or '它同比怎么样？' in m['content']) for m in messages)
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
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'followup','canonical_question':'MSFT 2031 年报营业收入同比','referents':[]},
        classifier=lambda _:'D',decider=decide)
    result=asyncio.run(direct.run_direct_turn('它同比怎么样？',ctx,emit=emit,messages=HISTORY))
    assert result['status']!='waiting_for_input'
    assert result['audit']['stages']['resolver']['validator']['rejected_reason']=='reference_period_unbound'
    assert not any('2031' in m['content'] for m in seen[0] if m['role']=='system')

def test_a_failing_resolver_with_history_does_not_stop_the_turn():
    seen,decide=_capture()
    def broken(_):raise ValueError('resolver_down')
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=broken,classifier=lambda _:'D',decider=decide)
    result=asyncio.run(direct.run_direct_turn('它同比怎么样？',ctx,emit=emit,messages=HISTORY))
    assert result['status']!='waiting_for_input' and seen
    assert [m['content'] for m in seen[0] if m['role']=='user'][-1]=='它同比怎么样？'
    assert not any(m['role']=='system' and m['content'].startswith('用户本轮原话') for m in seen[0])

def test_a_reference_with_nothing_to_point_at_still_asks():
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:'D',decider=lambda *_:{'content':'好的。'})
    result=asyncio.run(direct.run_direct_turn('解释第二个',ctx,emit=emit,messages=[{'role':'user','content':'解释第二个'}]))
    assert result['status']=='waiting_for_input'

def test_a_clarify_without_choices_continues_with_the_rewrite_as_a_hint():
    seen,decide=_capture()
    history=[{'role':'user','content':'先同时看一下微软（MSFT）和谷歌（GOOGL）最近一期年报的营收。'},
             {'role':'assistant','content':'MSFT 营收 331,839 百万美元 [1]；GOOGL 营收 402,836 百万美元 [2]。'},
             {'role':'user','content':'把它的营业利润列出来。'},{'role':'assistant','content':'需要补充信息：请问是微软还是谷歌？'},
             {'role':'user','content':'微软。'}]
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'clarify','canonical_question':'列出微软（MSFT）最近一期年报的营业利润','referents':[]},
        classifier=lambda _:'D',decider=decide)
    result=asyncio.run(direct.run_direct_turn('微软。',ctx,emit=emit,messages=history))
    assert result['status']!='waiting_for_input' and seen
    assert any(m['role']=='system' and '列出微软（MSFT）最近一期年报的营业利润' in m['content'] for m in seen[0])

def test_a_clarify_with_choices_still_asks_with_buttons():
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'clarify','question':'要看微软还是谷歌？','choices':['微软','谷歌']},
        classifier=lambda _:'D',decider=lambda *_:{'content':'好的。'})
    result=asyncio.run(direct.run_direct_turn('把它的营业利润列出来。',ctx,emit=emit,messages=HISTORY))
    assert result['status']=='waiting_for_input' and [c['label'] for c in result['pending_request']['choices']]==['微软','谷歌']

def test_two_companies_split_across_symbol_and_company_are_not_asked_about(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'AAPL','title':'APPLE INC'},{'ticker':'NVDA','title':'NVIDIA CORP'}])
    seen,decide=_capture()
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:{'category':'D','symbol':'AAPL','company':'NVIDIA','company_mention':'英伟达','days_back':7},decider=decide)
    result=asyncio.run(direct.run_direct_turn('查一下 AAPL 最近的已保存收盘价，再搜一条英伟达的最新新闻。',ctx,emit=emit))
    assert result['status']!='waiting_for_input' and seen
    assert result['audit']['multi_entity_scope']==['AAPL','NVDA']

def test_an_unfounded_company_is_still_asked_about(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'AAPL','title':'APPLE INC'},{'ticker':'NVDA','title':'NVIDIA CORP'}])
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:{'category':'D','symbol':'AAPL','company':'NVIDIA','company_mention':'英伟达','days_back':7},decider=lambda *_:{'content':'好的。'})
    result=asyncio.run(direct.run_direct_turn('查一下 AAPL 最近的已保存收盘价。',ctx,emit=emit))
    assert result['status']=='waiting_for_input'

def test_a_clarify_that_only_repeats_the_users_words_continues_instead_of_asking():
    seen,decide=_capture()
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'clarify','question':'总结一下你刚刚找到的两条新闻。',
        'choices':['第一条','第二条'],'canonical_question':'总结上一条回复中列出的两条苹果营收新闻','referents':[]},
        classifier=lambda _:'D',decider=decide)
    result=asyncio.run(direct.run_direct_turn('总结一下你刚刚找到的两条新闻。',ctx,emit=emit,messages=HISTORY))
    assert result['status']!='waiting_for_input' and seen
