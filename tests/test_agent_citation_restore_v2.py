"""Actual source identities must survive citation creation and history restoration."""
import copy,hashlib,json,uuid
from datetime import UTC,date,datetime
import pytest
from app.database import Base,engine,SessionLocal
from app.models import MarketPrice,SecFilingInventory
from app.agent_chat_models import AgentFAQ
from app.agent_chat_stream import _restore_citations
from app.agent_workflow.evidence import number_rows

@pytest.fixture(autouse=True)
def schema(disposable_database):Base.metadata.create_all(engine)

def test_price_citation_round_trips_and_tamper_is_rejected():
    now=datetime.now(UTC)
    trading_date = date(2026, 10, 16)
    with SessionLocal() as db:
        row=MarketPrice(symbol='CIT',trading_date=trading_date,open=10,high=11,low=9,close=10.5,volume=50,source='citation-test',fetched_at=now)
        db.add(row);db.commit();identity=str(row.id)
    try:
        citations=[]
        number_rows('query_stock_prices',[{'id':identity,'symbol':'CIT','close':10.5,'trading_date':trading_date.isoformat(),'source':'citation-test','fetched_at':now.isoformat()}],citations,{})
        assert _restore_citations(citations,'guest')[0]['source_type']=='saved_price'
        bad=copy.deepcopy(citations);bad[0]['quote']='999'
        assert _restore_citations(bad,'guest')[0]['source_type']=='unavailable'
    finally:
        with SessionLocal() as db:
            to_del = db.get(MarketPrice, uuid.UUID(identity) if isinstance(identity, str) and len(identity) == 36 else row.id)
            if to_del: db.delete(to_del); db.commit()

def test_legacy_faq_round_trips_but_answer_change_invalidates_it():
    question='Unique citation '+uuid.uuid4().hex
    with SessionLocal() as db:
        row=AgentFAQ(question=question,answer='Stars describe evidence credibility.',category='test');db.add(row);db.commit()
    citations=[]
    number_rows('query_research_faq',[{'question':question,'answer':'Stars describe evidence credibility.','category':'test'}],citations,{})
    assert _restore_citations(citations,'guest')[0]['source_type']=='research_faq'
    bad=copy.deepcopy(citations);bad[0]['quote']='Stars guarantee returns.'
    assert _restore_citations(bad,'guest')[0]['source_type']=='unavailable'

def test_filing_hash_does_not_authorize_an_unrelated_quote():
    body='CIT annual report. Revenue increased.';now=datetime.now(UTC)
    with SessionLocal() as db:
        row=SecFilingInventory(symbol='CIT',cik='0000320193',accession_number=f'0000320193-26-{uuid.uuid4().int%1000000:06}',form='10-K',filed_at=date.today(),primary_document='a.htm',source_url='https://www.sec.gov/a.htm',source='sec-edgar',review_status='pending_review',observed_at=now,content_status='fetched',content_excerpt=body,content_excerpt_sha256=hashlib.sha256(body.encode()).hexdigest(),content_observed_at=now)
        db.add(row);db.commit();identity=str(row.id)
    citations=[];number_rows('query_sec_filings',[{'id':identity,'symbol':'CIT','excerpt':body}],citations,{})
    assert _restore_citations(citations,'guest')[0]['source_type']=='official_filing'
    citations[0]['quote']='Revenue is 999 billion dollars.'
    assert _restore_citations(citations,'guest')[0]['source_type']=='unavailable'

def test_more_than_ten_source_identities_survive_history_restore():
    now=datetime.now(UTC);citations=[]
    with SessionLocal() as db:
        rows=[MarketPrice(symbol='MANY',trading_date=date(2026,1,n),open=10,high=11,low=9,close=10,volume=50,source='citation-test',fetched_at=now) for n in (5,6,7,8,9,12,13,14,15,16,20,21)]
        db.add_all(rows);db.commit()
        number_rows('query_stock_prices',[{'id':str(r.id),'symbol':r.symbol,'close':r.close,'trading_date':r.trading_date.isoformat(),'source':r.source,'fetched_at':r.fetched_at.isoformat()} for r in rows],citations,{})
    restored=_restore_citations(citations,'guest')
    assert len(restored)==12 and all(c['source_type']=='saved_price' for c in restored)
