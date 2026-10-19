"""What the first end-to-end run of owner writes (2026-10-19, real model) showed the user."""
import asyncio,json
import pytest
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext

async def emit(_):pass

QUEUE={'name':'query_research_queue','result':{'ok':True,'data':[{'symbols':['AAPL','MSFT','IBM']}],
       'source':'owner queue','as_of':'2026-10-19T09:28:13.123456+00:00'}}
REVENUE_ROW={'source_id':'f1','symbol':'MSFT','form':'10-K','source_type':'reported_figure','metric':'revenue',
    'title':'MSFT 10-K · 营业收入 FY2026','filed_at':'2026-07-29','excerpt':'营业收入（Revenue）FY2026：331,839,000,000 USD。'}
QUEUE_ADDED={'ok':True,'status':'complete','data':[{'ok':True,'data':[{'symbols':['AAPL','MSFT']}],'source':'owner queue'}]}
ADD_MSFT={'operations':[{'name':'add_to_research_queue','arguments':{'symbol':'MSFT'}}]}

def _turn(question,state=None,**context):
    return direct.Turn(question,AgentRuntimeContext('custom','test','dummy',**context),emit,[],state or {})


def test_an_answer_about_the_research_queue_is_checked_against_the_queue_itself():
    turn=_turn('研究队列里现在有哪些股票？');turn.route='D';turn.feedback=[QUEUE]
    result=turn.finish('研究队列当前有 3 只股票：AAPL、MSFT、IBM（读取时间 2026-10-19 09:28 UTC）。')
    assert result['status']=='succeeded' and 'AAPL、MSFT、IBM' in result['answer']

def test_a_queue_answer_stating_a_figure_the_queue_does_not_is_still_checked():
    turn=_turn('研究队列里现在有哪些股票？');turn.route='D';turn.feedback=[QUEUE]
    result=turn.finish('研究队列当前有 3 只股票，合计市值 9,876 亿美元。')
    assert '9,876' not in result['answer']

def test_execution_facts_name_the_operation_and_symbol_the_card_carried():
    assert direct.execution_facts(QUEUE_ADDED,ADD_MSFT['operations'])==[{'action':'add_to_research_queue','symbol':'MSFT','ok':True}]

def test_the_plain_report_names_an_operation_without_a_title_and_never_leaves_it_empty():
    text=direct.execution_fallback([{'action':'add_to_research_queue','symbol':'CRM','ok':True}],'把 CRM 加入研究队列')
    assert text=='已完成你确认的操作：\n- 加入研究队列 CRM（已完成）'
    assert 'add_to_research_queue' not in direct.execution_fallback([{'action':'fetch_sec','symbol':'TSLA','status':'succeeded'}],'拉取 TSLA 的 10-Q')

def _resuming(answer,executed):
    def execute(name,args):
        executed.append(name)
        return {'ok':True,'data':[dict(REVENUE_ROW)] if name=='query_financials' else []}
    state={'pending_request':{'original_question':'把 MSFT 加入研究队列，然后告诉我它最近一期营收','display':{'targets':[{'symbol':'MSFT'}]}},
           'previous_category':'D','previous_scope':{'symbol':'MSFT','metrics':['营业收入'],'requested_actions':['add_to_research_queue']}}
    return _turn('confirm:tok',state,decider=lambda *_:{'content':answer},executor=execute)

def test_after_approval_the_turn_reads_the_figure_the_request_also_asked_for():
    executed=[]
    turn=_resuming('MSFT FY2026 营业收入为 331,839,000,000 美元 [1]。',executed)
    result=asyncio.run(turn.resume_after_execution(QUEUE_ADDED,ADD_MSFT))
    assert result and 'query_financials' in executed and '331,839,000,000' in result['answer']
    assert result['answer'].startswith('已完成你确认的操作：\n- 加入研究队列 MSFT（已完成）')

def test_a_continuation_for_a_write_only_request_that_the_check_trims_gives_way_to_the_plain_report():
    executed=[]
    turn=_resuming('已保存。该新闻发布于 2026-10-14，正文 4,321 字 [1]。',executed)
    turn.state['previous_scope']={'symbol':'MSFT','requested_actions':['add_to_research_queue']}
    turn.state['pending_request']['original_question']='把 MSFT 加入研究队列'
    assert asyncio.run(turn.resume_after_execution(QUEUE_ADDED,ADD_MSFT)) is None

def _reporting(answer):
    turn=_turn('confirm:tok',{'pending_request':{'id':'req-1','reason':'write_confirmation','original_question':'把第二条保存到资料库',
        'display':{'targets':[{'symbol':'ORCL','title':'Oracle 新闻'}]}},'requested_pending_id':'req-1'})
    async def model(messages,tools,**kwargs):return {'content':answer}
    turn.model=model
    return turn

@pytest.mark.parametrize('leak',['来源编号 f2d01687-630f-4477-acc7-3f7f27a92a10','状态为 pending','- fetch_sec（已完成）'])
def test_a_report_that_shows_an_identifier_a_status_word_or_a_tool_name_gives_way_to_the_plain_report(leak):
    turn=_reporting('已保存网页《Oracle 新闻》。'+leak+'。')
    result=asyncio.run(turn.report_execution({'ok':True,'data':[{'status':'created','title':'Oracle 新闻','source_id':'f2d01687-630f-4477-acc7-3f7f27a92a10'}]}))
    assert leak not in result['answer'] and 'Oracle 新闻' in result['answer']

def _pressing(status,monkeypatch,pending=None):
    from app.agent_workflow import consent
    monkeypatch.setattr(consent,'lookup',lambda *_:{'id':'old','operations':[{'name':'add_to_research_queue','arguments':{'symbol':'INTC'}}],'display':{},'status':status})
    return asyncio.run(_turn('confirm:tok',{'pending_request':pending,'requested_pending_id':'00000000-0000-4000-8000-000000000001'},
                             identity_credential='owner').consent_reply())

def test_pressing_a_card_that_already_ran_says_so_instead_of_failing(monkeypatch):
    result=_pressing('complete',monkeypatch)
    assert result['status']!='failed' and '已经执行过' in result['answer'] and result['audit']['write_outcome']=='already_complete'

def test_pressing_a_card_a_newer_message_replaced_says_it_expired(monkeypatch):
    result=_pressing('proposed',monkeypatch,pending=None)
    assert result['status']!='failed' and '失效' in result['answer'] and result['audit']['write_outcome']=='stale_card'

def test_typing_consent_while_a_write_card_waits_points_to_the_button_and_keeps_the_card():
    card={'id':'req-9','reason':'write_confirmation','question':'确认加入研究队列？','choices':[{'label':'同意','value':'confirm:tok'}]}
    turn=_turn('同意',{'pending_request':card})
    result=asyncio.run(turn.consent_reply())
    assert result['status']=='waiting_for_input' and '按钮' in result['answer'] and result['pending_request']['id']=='req-9'

def test_task_status_is_reported_in_words_not_in_job_kinds():
    turn=_turn('分析好了吗？');turn.route='D'
    turn.feedback=[{'name':'query_system_status','result':{'ok':True,'data':[{'symbol':'MSFT','job_kind':'analyze','status':'running'}]}}]
    result=turn.finish('[9] 状态见上。')
    assert 'analyze' not in result['answer'] and 'running' not in result['answer'] and 'MSFT' in result['answer'] and '进行中' in result['answer']

def test_a_filing_review_card_names_the_filing(monkeypatch):
    monkeypatch.setattr(direct,'source_display_title',lambda kind,source_id:'msft-20260331.htm')
    turn=_turn('把这份 10-Q 标记为相关');turn.scope={'symbol':'MSFT'}
    rows=asyncio.run(turn.describe_targets('review_filing',{'source_id':'00000000-0000-4000-8000-000000000002','decision':'accepted'}))
    assert rows[0]['title']=='msft-20260331.htm'

def test_a_filing_that_already_has_a_review_says_so_instead_of_failing_vaguely():
    from app.agent_workflow import consent
    from app.sec_filings import SecFilingsError
    out=consent.failure_outcome(SecFilingsError('SEC filing already has an immutable human review decision'))
    assert out['error_code']=='already_reviewed' and '不能修改' in out['reason']
    assert 'already_reviewed' in direct.FAILURE_NEXT_STEPS
