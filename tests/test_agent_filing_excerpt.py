"""The saved-filing excerpt starts at the primary statements, whatever the issuer calls them."""
import hashlib,uuid
from datetime import UTC,date,datetime
import pytest
from app.database import Base,engine,SessionLocal
from app.models import SecFilingInventory
from app.agent_chat_stream import _restore_citations
from app.agent_tools import CITATION_QUOTE_CHARACTERS,saved_filing_excerpt

CONTENTS="PART II\nItem 8. Financial Statements and Supplementary Data 49\nIncome Statements 50\nBalance Sheets 52\nCash Flows Statements 53\n"
MICROSOFT=(CONTENTS+"\n".join(f"Management discussion paragraph {i}." for i in range(60))+
    "\nITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA\n\nINCOME STATEMENTS\n\n(In millions, except per share amounts)\n\n"
    "Year Ended June 30,\n\n2026\n\n2025\n\nTotal revenue\n\n331,839\n\n281,724\n\nOperating income\n\n155,237\n\n128,528\n\n"
    "BALANCE SHEETS\n\n(In millions)\n\nCash and cash equivalents\n\n20,935\n\n30,242\n"+"Balance sheet line.\n"*800)

def _record(body):
    return {'content_text':body,'content_text_sha256':hashlib.sha256(body.encode()).hexdigest()}

def test_excerpt_starts_at_microsoft_income_statements():
    excerpt,ok=saved_filing_excerpt(_record(MICROSOFT))
    assert ok and excerpt.startswith('INCOME STATEMENTS') and 'Total revenue' in excerpt

def test_excerpt_still_handles_statements_of_operations():
    body=("Item 1. Financial Statements\n"+"Intro line.\n"*20+"CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)\n"
          "Total net sales\n94,036\nCONDENSED CONSOLIDATED BALANCE SHEETS\nTotal assets\n331,495\n")
    excerpt,ok=saved_filing_excerpt(_record(body))
    assert ok and excerpt.startswith('CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS') and 'Total net sales' in excerpt

def test_restore_accepts_a_quote_made_by_the_previous_excerpt_rule(disposable_database):
    Base.metadata.create_all(engine)
    legacy,_=saved_filing_excerpt(_record(MICROSOFT),legacy=True)
    assert not legacy.startswith('INCOME STATEMENTS')
    now=datetime.now(UTC)
    with SessionLocal() as db:
        row=SecFilingInventory(symbol='EXC',cik='0000789019',accession_number=f'0000789019-26-{uuid.uuid4().int%1000000:06}',form='10-K',
            filed_at=date(2026,7,29),primary_document='exc-20260630.htm',source_url='https://www.sec.gov/exc.htm',source='sec-edgar',
            review_status='pending_review',observed_at=now,content_status='fetched',content_excerpt=MICROSOFT[:500],
            content_excerpt_sha256=hashlib.sha256(MICROSOFT[:500].encode()).hexdigest(),content_text=MICROSOFT,
            content_text_sha256=hashlib.sha256(MICROSOFT.encode()).hexdigest(),content_length=len(MICROSOFT),content_observed_at=now)
        db.add(row);db.commit();identity=str(row.id)
    try:
        citation={'ordinal':1,'source_type':'official_filing','source_id':identity,'title':'exc-20260630.htm','quote':legacy[:CITATION_QUOTE_CHARACTERS]}
        assert _restore_citations([citation],'guest')[0]['source_type']=='official_filing'
        forged={**citation,'quote':'INCOME STATEMENTS\nTotal revenue 999,999'}
        assert _restore_citations([forged],'guest')[0]['source_type']=='unavailable'
    finally:
        with SessionLocal() as db:
            row=db.get(SecFilingInventory,uuid.UUID(identity))
            if row:db.delete(row);db.commit()
