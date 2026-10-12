from app.agent_workflow.source_selection import choose_filing


def test_latest_financial_report_ignores_newer_8k():
    rows=[{"id":"event","form":"8-K","filed_at":"2026-05-01","accession_number":"event"},
          {"id":"report","form":"10-Q","filed_at":"2026-04-30","accession_number":"report"}]
    assert choose_filing(rows, financial_only=True)["id"] == "report"


def test_explicit_accession_never_falls_back():
    rows=[{"id":"other","form":"10-Q","filed_at":"2026-04-30","accession_number":"other"}]
    assert choose_filing(rows, accession_number="missing") is None


def test_postgres_saved_filing_filters_before_date_order(disposable_database):
    import asyncio
    import hashlib
    from datetime import UTC, date, datetime
    from uuid import uuid4
    from app.database import engine, SessionLocal
    from app.models import SecFilingInventory
    from app.agent_tools import query_sec_filings

    SecFilingInventory.__table__.create(engine, checkfirst=True)
    now=datetime.now(UTC)
    with SessionLocal() as db:
        for form, filed, accession in (("10-Q", date(2026,4,30), "0000320193-26-000020"),
                                        ("8-K", date(2026,5,1), "0000320193-26-000021")):
            body=f"{form} verified content"
            db.add(SecFilingInventory(id=uuid4(),symbol="AAPL",cik="0000320193",accession_number=accession,
                form=form,filed_at=filed,primary_document="report.htm",source_url="https://www.sec.gov/test",
                source="sec",review_status="pending_review",observed_at=now,content_status="fetched",
                content_observed_at=now,content_excerpt=body,
                content_excerpt_sha256=hashlib.sha256(body.encode()).hexdigest(),content_truncated=False))
        db.commit()
    result=asyncio.run(query_sec_filings.ainvoke({"symbol":"AAPL","financial_only":True,"limit":1}))
    assert [row["accession_number"] for row in result["data"]]==["0000320193-26-000020"]
    exact=asyncio.run(query_sec_filings.ainvoke({"symbol":"AAPL","accession_number":"0000320193-26-000021"}))
    assert [row["form"] for row in exact["data"]]==["8-K"]
