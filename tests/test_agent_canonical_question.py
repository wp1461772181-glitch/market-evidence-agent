"""The resolver writes the canonical question every turn, and every later stage reads it."""
import asyncio
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext
from app.freshness import requested_years

CANONICAL='微软（MSFT）2025 财年（截至 2025-06-30）的营业收入是多少？'
HISTORY=[{'role':'user','content':'先看一下微软（MSFT）最近一期年报的营收。'},
         {'role':'assistant','content':'微软（MSFT）FY2025 营业收入为 281,724 百万美元 [1]。'}]

async def emit(_):pass

def run(question,*,resolver,classifier,history=None):
    seen=[]
    def decide(messages,tools):seen.append(messages);return {'content':'好的。'}
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=resolver,classifier=classifier,decider=decide)
    result=asyncio.run(direct.run_direct_turn(question,ctx,emit=emit,messages=history))
    return result,seen[0] if seen else []

def hint(messages):
    return next(m['content'] for m in messages if m['role']=='system' and m['content'].startswith('对当前问题的理解'))

def test_the_first_message_is_rewritten_before_classification_and_the_tool_loop():
    classified=[]
    def classify(question):classified.append(question);return {'category':'D'}
    result,messages=run('去年微软的营收是多少？',resolver=lambda _:{'relationship':'new','canonical_question':CANONICAL},classifier=classify)
    assert classified==[CANONICAL]
    assert result['audit']['stages']['resolver']['skipped'] is False
    assert CANONICAL in hint(messages) and result['audit']['stages']['resolver']['validator']['passed'] is True

def test_a_first_turn_clarify_is_deferred_to_the_tool_loop():
    result,messages=run('帮我修订一下苹果（AAPL）的预测。',
        resolver=lambda _:{'relationship':'clarify','question':'要修订哪一项预测？','choices':['业绩预测','目标价预测']},
        classifier=lambda _:{'category':'D'})
    assert result['pending_request'] is None and result['status']!='waiting_for_input'
    assert result['audit'].get('resolver_clarify_deferred') is True
    assert [m['content'] for m in messages if m['role']=='user'][-1]=='帮我修订一下苹果（AAPL）的预测。'

def test_a_clarify_with_history_still_asks_with_buttons():
    history=[{'role':'user','content':'先看一下微软（MSFT）和谷歌（GOOGL）最近一期年报的营收。'},
             {'role':'assistant','content':'微软（MSFT）FY2025 营业收入为 281,724 百万美元 [1]；谷歌（GOOGL）营业收入为 402,836 百万美元 [2]。'}]
    result,_=run('把它的营业利润列出来。',
        resolver=lambda _:{'relationship':'clarify','question':'要看微软还是谷歌？','choices':['微软','谷歌']},
        classifier=lambda _:{'category':'D'},history=history)
    assert result['status']=='waiting_for_input'
    assert [c['label'] for c in result['pending_request']['choices']]==['微软','谷歌']

def test_the_tool_loop_uses_only_the_canonical_question():
    result,messages=run('去年微软的营收是多少？',resolver=lambda _:{'relationship':'new','canonical_question':CANONICAL},
        classifier=lambda _:{'category':'D'})
    users=[m['content'] for m in messages if m['role']=='user']
    notes=[m['content'] for m in messages if m['role']=='system']
    assert users[-1]==CANONICAL and '去年微软的营收是多少？' not in users
    assert not any('用户本轮原话' in note or '去年微软的营收是多少？' in note for note in notes)
    understanding=next(note for note in notes if note.startswith('对当前问题的理解'))
    assert '以改写后的问题为准' in understanding and 'days_back' in understanding and 'max_results' in understanding

def test_the_reference_prompt_binds_relative_years_to_the_ending_fiscal_year():
    assert '结束在该自然年里的那个财年' in direct.REFERENCE_PROMPT
    assert '上一财年' in direct.REFERENCE_PROMPT and 'most recent completed fiscal year' in direct.REFERENCE_PROMPT
    assert 'calendar year or exact dates' in direct.REFERENCE_PROMPT
    assert 'output-form instruction' in direct.REFERENCE_PROMPT
    assert '"去年" is the previous calendar year' not in direct.REFERENCE_PROMPT

def test_the_reference_prompt_forbids_counter_questions_and_product_guessing():
    prompt=direct.REFERENCE_PROMPT
    assert '绝不是反问' in prompt
    assert '看不到产品里保存的东西' in prompt
    assert '产品本身的请求' in prompt and '不需要公司' in prompt
    assert '不添加用户没提的要求' in prompt
    assert '预测' in prompt and 'SEC' in prompt and '研究队列' in prompt
    assert '不要追问是哪一份或哪一个' in prompt

def test_requested_years_reads_only_literal_years():
    assert requested_years('去年微软的营收')==set()
    assert requested_years('2025 财年')=={'2025'}

def test_a_rewritten_past_year_survives_but_a_classifier_year_absent_from_it_is_dropped():
    kept,_=run('去年微软的营收是多少？',resolver=lambda _:{'relationship':'new','canonical_question':CANONICAL},
        classifier=lambda _:{'category':'D','period':'FY2025'})
    assert kept['audit']['stages']['resolver']['validator']['passed'] is True
    assert kept['audit']['stages']['classifier']['scope']['period']=='FY2025'
    dropped,_=run('去年微软的营收是多少？',resolver=lambda _:{'relationship':'new','canonical_question':CANONICAL},
        classifier=lambda _:{'category':'D','period':'FY2030'})
    assert dropped['audit']['stages']['classifier']['scope']['period'] is None

def test_a_selected_referent_extends_the_rewrite_not_the_users_words():
    canonical='列出微软最近一期年报的营业利润。'
    resolver=lambda _:{'relationship':'followup','canonical_question':canonical,
        'referents':[{'mention':'它','text':'微软（MSFT）','source':'assistant_previous'}]}
    result,messages=run('它的营业利润呢？',resolver=resolver,classifier=lambda _:{'category':'D'},history=HISTORY)
    assert result['audit']['stages']['resolver']['validator']['passed'] is True
    assert canonical+' 微软（MSFT）' in hint(messages) and '它的营业利润呢？' not in hint(messages)

def test_a_rewrite_that_adds_an_unfounded_future_year_is_dropped():
    result,messages=run('它的营收呢？',history=[{'role':'user','content':'Micron FY2025 report'}],
        resolver=lambda _:{'relationship':'followup','canonical_question':'Micron FY2031 revenue'},
        classifier=lambda _:{'category':'D','period':'FY2031'})
    assert result['audit']['stages']['resolver']['validator']['rejected_reason']=='reference_period_unbound'
    assert 'FY2031' not in hint(messages) and '它的营收呢？' in hint(messages)

def test_the_canonical_question_grounds_the_classifier_company():
    canonical='请找出两条与苹果公司（Apple Inc.，股票代码 AAPL）营收相关的新闻报道。'
    result,_=run('帮我找两条跟苹果营收相关的新闻报道。',
        resolver=lambda _:{'relationship':'new','canonical_question':canonical},
        classifier=lambda _:{'category':'D','company':'Apple Inc.','symbol':'AAPL'})
    assert result['pending_request'] is None and result['status']!='waiting_for_input'

def test_a_classifier_company_absent_from_canonical_and_original_still_asks(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'resolve_ticker',lambda _:None)
    result,_=run('最近有什么新闻？',
        resolver=lambda _:{'relationship':'new','canonical_question':'最近有什么新闻？'},
        classifier=lambda _:{'category':'D','company':'Microsoft Corporation','symbol':'MSFT'})
    assert result['status']=='waiting_for_input'
    assert result['pending_request']['question']=='请确认这次指代的公司。'

def test_a_company_mention_with_ticker_and_exchange_is_grounded_by_a_written_part(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'verified_pair',lambda *_:True)
    mention='微软（Microsoft，NASDAQ: MSFT）'
    result,_=run('微软上一财年的净利润是多少？',
        resolver=lambda _:{'relationship':'new','canonical_question':mention+'2026 财年（2025-07-01 至 2026-06-30）的净利润是多少？'},
        classifier=lambda _:{'category':'D','company':'Microsoft Corporation','symbol':'MSFT','company_mention':mention})
    assert result['pending_request'] is None and result['status']!='waiting_for_input'
    assert result['audit'].get('entity_mapping_verified') is True
    assert result['previous_scope']['_entity_aliases']==['微软','Microsoft','NASDAQ','MSFT']

def test_a_company_mention_written_nowhere_still_asks(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'verified_pair',lambda *_:True)
    monkeypatch.setattr(entities,'resolve_ticker',lambda _:None)
    mention='微软（Microsoft，NASDAQ: MSFT）'
    result,_=run('最近有什么新闻？',
        resolver=lambda _:{'relationship':'new','canonical_question':'最近有什么新闻？'},
        classifier=lambda _:{'category':'D','company':'Microsoft Corporation','symbol':'MSFT','company_mention':mention})
    assert result['status']=='waiting_for_input'
    assert result['pending_request']['question']=='请确认这次指代的公司。'

def test_a_mention_from_another_company_does_not_ground_the_classifier_pair(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'verified_pair',lambda company,symbol:company=='Microsoft Corporation' and symbol=='MSFT')
    monkeypatch.setattr(entities,'resolve_ticker',lambda _:None)
    result,_=run('AAPL 的营收',
        resolver=lambda _:{'relationship':'new','canonical_question':'微软（Microsoft，NASDAQ: MSFT）2025 财年（2024-07-01 至 2025-06-30）的营业收入是多少？'},
        classifier=lambda _:{'category':'D','company':'Microsoft Corporation','symbol':'MSFT','company_mention':'AAPL'})
    assert result['status']=='waiting_for_input'
    assert result['pending_request']['question']=='请确认这次指代的公司。'

def test_a_different_company_in_the_canonical_does_not_ground_the_classifier_pair(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'resolve_ticker',lambda _:None)
    result,_=run('AAPL 的营收',
        resolver=lambda _:{'relationship':'new','canonical_question':'苹果公司（AAPL）的营收是多少？'},
        classifier=lambda _:{'category':'D','company':'Microsoft Corporation','symbol':'MSFT'})
    assert result['status']=='waiting_for_input'
    assert result['pending_request']['question']=='请确认这次指代的公司。'