"""Behaviours the first production smoke of the fluent agent got wrong (2026-10-19, RAG on)."""
import asyncio,json,time
from app.agent_workflow import direct
from app.agent_workflow.evidence import reviewed_answer,unsupported_number_values
from app.agent_workflow.state import AgentRuntimeContext

async def emit(_):pass

MARKUP=('<｜DSML｜ calls>\n<｜DSML｜ invoke name="use_result_set">\n'
        '<｜DSML｜ parameter name="positions" string="false">[1]</｜DSML｜ parameter>\n'
        '</｜DSML｜ invoke>\n</｜DSML｜ calls>')
FILINGS=[{'ordinal':1,'source_type':'official_filing','quote':'Our Internet address is www.microsoft.com.','title':'msft-20260630.htm','source_url':None},
         {'ordinal':2,'source_type':'official_filing','quote':'INCOME STATEMENTS','title':'0001193125-26-323660',
          'source_url':'https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm','published_at':'2026-07-29'}]
SEGMENTS=[{'ordinal':1,'source_type':'official_filing','quote':'(In millions) iPhone 54,252 44,582 Mac 10,352 8,046 Total net sales 109,417','title':'AAPL 10-Q'}]
TABLE=('苹果 10-Q 披露的分产品营收（百万美元）[1]：\n\n| 产品 | 本期 | 上年同期 |\n|---|---|---|\n'
       '| iPhone | 54,252 | 44,582 |\n| Mac | 10,352 | 8,046 |')


def test_digits_inside_a_link_the_evidence_shows_are_not_figures():
    row=('| 2026-07-29 | 10-K | 年度报告正文 msft-20260630.htm [1] | '
         'sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm [1] |')
    assert unsupported_number_values(row,FILINGS)==[]

def test_digits_inside_a_link_no_source_shows_are_still_checked():
    assert unsupported_number_values('详见 example.com/data/123456/report [2]。',FILINGS)==[123456.0]

def test_a_table_row_is_checked_against_the_sources_its_table_cites():
    kept,issues=reviewed_answer(TABLE,SEGMENTS,require_citations=True)
    assert issues==[] and '| Mac | 10,352 | 8,046 |' in kept

def test_a_table_row_stating_a_figure_its_sources_do_not_is_still_dropped():
    kept,issues=reviewed_answer(TABLE.replace('10,352','99,999'),SEGMENTS,require_citations=True)
    assert issues==['unsupported_numeric_claim'] and '99,999' not in kept and '| iPhone | 54,252 | 44,582 |' in kept

def test_a_heading_that_states_no_amount_needs_no_citation():
    text='## 作为替代：本轮可核验的苹果营收官方数字\n\n**单季营收（FY2026 Q3，2026-03-29 至 2026-06-27）**\n\n营收 109,417 百万美元 [1]。'
    assert reviewed_answer(text,SEGMENTS,require_citations=True)[1]==[]

def test_a_heading_that_states_an_amount_still_needs_its_source():
    assert reviewed_answer('## 营收 999 亿美元\n\n营收 109,417 百万美元 [1]。',SEGMENTS,require_citations=True)[1]

def test_a_list_item_keeps_its_sentences_that_passed():
    text='- AI 分析并未给出营收的具体金额。营收为 109,417 百万美元 [1]。'
    kept,issues=reviewed_answer(text,SEGMENTS,require_citations=True)
    assert issues==['missing_citation'] and kept.startswith('- ') and '109,417 百万美元 [1]' in kept and '并未给出' not in kept

def _turn(question='AMD 收盘价',**context):
    return direct.Turn(question,AgentRuntimeContext('custom','test','dummy',**context),emit,[],{})

def test_tool_call_markup_never_reaches_the_answer():
    turn=_turn();turn.route='A';turn.citations=[{'ordinal':1,'quote':'100','source_type':'saved_price'}]
    result=turn.finish('AMD 收盘价为 100 美元 [1]。\n\n'+MARKUP)
    assert 'DSML' not in result['answer'] and result['answer']=='AMD 收盘价为 100 美元 [1]。'

def _capped(answers,closings):
    def decide(messages,tools):
        turn.started=time.monotonic()-100
        return {'tool_calls':[{'id':'1','function':{'name':'query_stock_prices','arguments':json.dumps({'symbol':'AMD'})}}]}
    def answer(messages):
        closings.append(messages);return answers[len(closings)-1]
    turn=_turn(classifier=lambda _:'D',decider=decide,answerer=answer,
               executor=lambda *_:{'ok':True,'data':[{'id':'p','symbol':'AMD','close':100}]})
    return turn

def test_a_closing_that_only_calls_a_tool_is_asked_once_more():
    closings=[]
    turn=_capped([MARKUP,'AMD 已保存收盘价 100 美元 [1]。'],closings)
    result=asyncio.run(turn.run())
    assert len(closings)==2 and closings[1][-1]['content']==direct.CLOSING_RETRY_NOTE
    assert result['answer'].startswith('AMD 已保存收盘价 100 美元 [1]') and 'DSML' not in result['answer']

def test_a_turn_that_stops_finding_new_evidence_answers_from_what_it_has():
    calls=[]
    def decide(messages,tools):
        calls.append(1)
        return {'tool_calls':[{'id':str(len(calls)),'function':{'name':'query_stock_prices','arguments':json.dumps({'symbol':'AMD','limit':len(calls)})}}]}
    turn=_turn(classifier=lambda _:'D',decider=decide,answerer=lambda messages:'AMD 已保存收盘价 100 美元 [1]。',
               executor=lambda *_:{'ok':True,'data':[{'id':'p','symbol':'AMD','close':100}]})
    result=asyncio.run(turn.run())
    assert result['audit']['stop_reason']=='no_progress' and result['status']=='succeeded'
    assert result['answer']=='AMD 已保存收盘价 100 美元 [1]。'

def test_a_closing_the_check_rejects_lists_the_sources_by_name():
    closings=[]
    turn=_capped(['AMD 收盘价为 999 美元 [1]。']*2,closings)
    result=asyncio.run(turn.run())
    assert '999' not in result['answer'] and '已取得可核验来源' not in result['answer'] and '请拆成单独的一条再发' not in result['answer']
    assert 'AMD 已保存收盘价：100 [1]' in result['answer']

def test_a_repair_that_calls_a_tool_is_asked_once_more_without_tools():
    seen=[];replies=iter([{'tool_calls':[{'id':'x','function':{'name':'calculate','arguments':'{}'}}]},{'content':'AMD 收盘价为 100 美元 [1]。'}])
    def decide(messages,tools):seen.append(messages);return next(replies)
    turn=_turn(decider=decide);turn.route='A'
    turn.citations=[{'ordinal':1,'quote':'100','source_type':'saved_price'}];turn.observations=[{'name':'query_stock_prices','accepted_evidence':True}]
    result=asyncio.run(turn.finish_answer('AMD 收盘价为 999 美元 [1]。'))
    assert result['status']=='succeeded' and result['answer']=='AMD 收盘价为 100 美元 [1]。'
    assert len(seen)==2 and seen[1][-1]['content']==direct.REPAIR_RETRY_NOTE

def test_the_audit_lists_the_lines_removed_from_the_answer_it_returns():
    replies=iter([{'content':'AMD 收盘价为 100 美元 [1]。\nAMD 开盘价为 999 美元 [1]。'},{'content':'AMD 开盘价为 999 美元 [1]。\nAMD 最高价为 888 美元 [1]。\nAMD 最低价为 777 美元 [1]。'}])
    turn=_turn(decider=lambda messages,tools:next(replies));turn.route='A'
    turn.citations=[{'ordinal':1,'quote':'100','source_type':'saved_price'}];turn.observations=[{'name':'query_stock_prices','accepted_evidence':True}]
    result=asyncio.run(turn.finish_answer('AMD 收盘价为 999 美元 [1]。\nAMD 开盘价为 998 美元 [1]。'))
    assert result['answer'].startswith('AMD 收盘价为 100 美元 [1]')
    assert result['audit']['rejected_lines']==[{'line':'AMD 开盘价为 999 美元 [1]。','reason':'unsupported_numeric_claim'}]

def test_a_position_mismatch_names_the_positions_the_users_words_point_to():
    turn=_turn('详细解释一下第二条。')
    turn.sets=[{'id':'s1','tool':'web_research','scope':{},'rows':[{'title':'A','url':'https://a'},{'title':'B','url':'https://b'}]}]
    out=asyncio.run(turn.previous_results({'result_set_id':'s1','positions':[1],'action':'summarize'}))
    assert out['error_code']=='result_position_mismatch' and out['expected_positions']==[2]

def test_selecting_from_this_turns_own_results_says_they_are_already_evidence():
    turn=_turn('列出微软的申报')
    saved={'id':'s1','tool':'query_sec_filings','scope':{},'rows':[{'id':'f1','title':'10-K'},{'id':'f2','title':'8-K'}]}
    turn.sets=[saved];turn.new_sets=[saved];before=turn.repeated_evidence
    out=asyncio.run(turn.previous_results({'result_set_id':'s1','positions':[1,2],'action':'summarize'}))
    assert out['ok'] and out['data'][0]['status']=='already_in_evidence' and turn.repeated_evidence==before and not turn.citations

def test_the_closing_instruction_asks_for_an_answer_not_a_progress_report():
    text=_turn().tool_messages(True)[0]['content']
    assert '明确列出已完成和未完成的部分' not in text and '不要写出任何工具调用' in text

def test_a_date_written_with_spaces_is_a_date_not_three_figures():
    filing=[{'ordinal':1,'source_type':'official_filing','quote':'Quarterly report 10-Q','title':'MSFT 10-Q'}]
    assert unsupported_number_values('| 2026-01-28 | 10-Q | 截至 2025 年 12 月 31 日的季度报告 [1] |',filing)==[]
    assert unsupported_number_values('交易日为 2026 年 10 月 16 日，统计到 2025 年 12 月 [1]。',filing)==[]

def test_a_question_mark_inside_a_title_does_not_split_the_cited_line():
    text='1. **《Nvidia 与 Apple 股票：2026 年哪一只更值得买入？》（KuCoin，2026-10-18）** [1]'
    news=[{'ordinal':1,'source_type':'web','quote':'Nvidia vs Apple stock: which one to buy?','title':'Nvidia 与 Apple 股票：2026 年哪一只更值得买入？'}]
    kept,issues=reviewed_answer(text,news,require_citations=True)
    assert issues==[] and kept==text

def test_a_numbered_item_that_loses_a_sentence_keeps_a_clean_marker():
    kept,issues=reviewed_answer('1. 营收为 109,417 百万美元 [1]。另有 999 亿美元的说法。',SEGMENTS,require_citations=True)
    assert issues and kept=='1. 营收为 109,417 百万美元 [1]'
