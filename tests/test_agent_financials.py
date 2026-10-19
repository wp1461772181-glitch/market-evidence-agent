"""Reported XBRL figures reach the chat as citable rows with their fiscal period."""
import asyncio,copy,hashlib,json,uuid
from datetime import UTC,date,datetime
import pytest
from app.database import Base,engine,SessionLocal
from app.models import SecFilingInventory
from app.agent_chat_stream import _restore_citations
from app.agent_workflow.evidence import number_rows
from app.agent_workflow.financials import period_label,xbrl_metrics_for

def _p(start,end,value,fy,fp):return {'start':start,'end':end,'unit':'USD','value':value,'fiscal_year':fy,'fiscal_period':fp}
TENK=[{'metric':'revenue','label':'Revenue','concept':'RevenueFromContractWithCustomerExcludingAssessedTax','unit':'USD','periods':[
        _p('2025-07-01','2026-06-30',331839000000,2026,'FY'),_p('2024-07-01','2025-06-30',281724000000,2026,'FY'),_p('2023-07-01','2024-06-30',245122000000,2026,'FY')]},
      {'metric':'operating_income','label':'Operating income','concept':'OperatingIncomeLoss','unit':'USD','periods':[
        _p('2025-07-01','2026-06-30',155237000000,2026,'FY'),_p('2024-07-01','2025-06-30',128528000000,2026,'FY')]},
      {'metric':'cash_and_equivalents','label':'Cash and cash equivalents','concept':'CashAndCashEquivalentsAtCarryingValue','unit':'USD','periods':[
        _p(None,'2026-06-30',20935000000,2026,'FY'),_p(None,'2025-06-30',30242000000,2026,'FY')]}]
TENQ=[{'metric':'operating_income','label':'Operating income','concept':'OperatingIncomeLoss','unit':'USD','periods':[
        _p('2026-01-01','2026-03-31',38398000000,2026,'Q3'),_p('2025-07-01','2026-03-31',114634000000,2026,'Q3'),
        _p('2025-01-01','2025-03-31',32000000000,2026,'Q3'),_p('2024-07-01','2025-03-31',94205000000,2026,'Q3')]}]

@pytest.fixture
def saved(disposable_database):
    Base.metadata.create_all(engine)
    symbol='FG'+''.join(chr(65+b%26) for b in uuid.uuid4().bytes[:3])
    now=datetime.now(UTC);ids={}
    with SessionLocal() as db:
        for form,filed,figures in (('10-K',date(2026,7,29),TENK),('10-Q',date(2026,4,29),TENQ)):
            body=f'{form} body for {symbol}'
            row=SecFilingInventory(symbol=symbol,cik='0000789019',accession_number=f'0000789019-26-{uuid.uuid4().int%1000000:06}',form=form,
                filed_at=filed,primary_document=f'{symbol.lower()}-{filed:%Y%m%d}.htm',source_url=f'https://www.sec.gov/{symbol}/{form}.htm',
                source='sec-edgar',review_status='pending_review',observed_at=now,content_status='fetched',content_excerpt=body,
                content_excerpt_sha256=hashlib.sha256(body.encode()).hexdigest(),content_observed_at=now,content_financials=copy.deepcopy(figures))
            db.add(row);db.commit();ids[form]=str(row.id)
    yield symbol,ids
    # Other suites walk every saved filing; leftovers would change what they see.
    with SessionLocal() as db:
        for identity in ids.values():
            row=db.get(SecFilingInventory,uuid.UUID(identity))
            if row:db.delete(row)
        db.commit()

def _query(**args):
    from app.agent_tools import query_financials
    return asyncio.run(query_financials.ainvoke(args))

def test_period_label_annual_comparative():
    label=period_label({'start':'2024-07-01','end':'2025-06-30'},document_end=date(2026,6,30),dei_fiscal_year=2026,dei_fiscal_period='FY')
    assert (label['fiscal_year'],label['fiscal_period'],label['span'])==(2025,'FY','annual') and 'FY2025' in label['label']

def test_period_label_quarter_comparative():
    quarter=period_label({'start':'2025-01-01','end':'2025-03-31'},document_end=date(2026,3,31),dei_fiscal_year=2026,dei_fiscal_period='Q3')
    ytd=period_label({'start':'2024-07-01','end':'2025-03-31'},document_end=date(2026,3,31),dei_fiscal_year=2026,dei_fiscal_period='Q3')
    assert (quarter['fiscal_year'],quarter['fiscal_period'],quarter['span'])==(2025,'Q3','quarter')
    assert (ytd['fiscal_year'],ytd['fiscal_period'],ytd['span'])==(2025,'Q3','ytd9')

def test_period_label_first_quarter_after_year_end_belongs_to_the_next_fiscal_year():
    label=period_label({'start':'2025-07-01','end':'2025-09-30'},document_end=date(2026,6,30),dei_fiscal_year=2026,dei_fiscal_period='FY')
    assert (label['fiscal_year'],label['fiscal_period'])==(2026,'Q1')

def test_period_label_instant():
    label=period_label({'start':None,'end':'2025-06-30'},document_end=date(2026,6,30),dei_fiscal_year=2026,dei_fiscal_period='FY')
    assert label['span']=='instant' and label['fiscal_year']==2025 and '2025-06-30' in label['label']

def test_query_financials_reads_saved_xbrl(saved):
    symbol,_=saved
    result=_query(symbol=symbol,metrics=['revenue'],fiscal_year=2025)
    assert result['ok'] and len(result['data'])==1
    row=result['data'][0]
    assert row['value']==281724000000 and row['fiscal_year']==2025 and row['source_type']=='reported_figure'
    assert '281,724,000,000' in row['excerpt'] and '2,817.24 亿美元' in row['excerpt'] and '营业收入' in row['excerpt']

def test_query_financials_quarter_filter_keeps_only_the_three_month_value(saved):
    symbol,_=saved
    result=_query(symbol=symbol,metrics=['operating_income'],fiscal_year=2025,fiscal_period='Q3')
    assert [r['value'] for r in result['data']]==[32000000000]

def test_query_financials_keeps_the_latest_filing_for_a_repeated_period(saved):
    symbol,ids=saved
    rows=_query(symbol=symbol,metrics=['operating_income'])['data']
    keys=[(r['period_start'],r['period_end']) for r in rows]
    assert len(keys)==len(set(keys))
    annual=[r for r in rows if r['period_end']=='2026-06-30' and r['period_start']=='2025-07-01']
    assert annual and annual[0]['source_id']==ids['10-K']

def test_reported_figure_citation_restores_and_a_changed_value_expires(saved):
    symbol,ids=saved
    rows=_query(symbol=symbol,metrics=['revenue'],fiscal_year=2026)['data']
    citations=[];number_rows('query_financials',rows,citations,{})
    assert citations[0]['source_type']=='reported_figure'
    assert _restore_citations(citations,'guest')[0]['source_type']=='reported_figure'
    with SessionLocal() as db:
        row=db.get(SecFilingInventory,uuid.UUID(ids['10-K']))
        figures=copy.deepcopy(row.content_financials);figures[0]['periods'][0]['value']=1
        row.content_financials=figures;db.commit()
    assert _restore_citations(citations,'guest')[0]['source_type']=='unavailable'

def test_metric_words_map_to_xbrl_metrics_only_when_they_name_one():
    assert xbrl_metrics_for(['全年营业收入'])==['revenue']
    assert xbrl_metrics_for(['营业利润','现金余额'])==['operating_income','cash_and_equivalents']
    assert xbrl_metrics_for(['云业务收入'])==[] and xbrl_metrics_for([])==[]

def test_saved_data_turn_reads_reported_figures_when_the_metric_is_one_of_them():
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    executed=[]
    def execute(name,args):
        executed.append((name,args))
        if name=='query_financials':
            return {'ok':True,'data':[{'source_id':str(uuid.uuid4()),'symbol':'MSFT','form':'10-K','source_type':'reported_figure',
                'title':'MSFT 10-K · 营业收入 FY2026','filed_at':'2026-07-29','excerpt':'营业收入（Revenue）FY2026：331,839,000,000 USD。'}]}
        return {'ok':True,'data':[]}
    async def emit(_):pass
    ctx=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:{'category':'A','symbol':'MSFT','metrics':['营业收入'],'days_back':None},
        decider=lambda *_:{'content':'MSFT FY2026 营业收入为 331,839,000,000 美元 [1]。'},executor=execute)
    result=asyncio.run(direct.run_direct_turn('查 MSFT 最近一期年报的营业收入',ctx,emit=emit))
    assert ('query_financials',{'symbol':'MSFT','metrics':['revenue'],'latest':False,'limit':20}) in [(n,{k:v for k,v in a.items() if v is not None}) for n,a in executed]
    assert result['status']=='succeeded' and '331,839,000,000' in result['answer']
