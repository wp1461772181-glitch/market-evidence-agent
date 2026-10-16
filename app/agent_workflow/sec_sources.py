"""Official metadata discovery is read-only; registration requires frozen consent."""
from dataclasses import asdict
from datetime import UTC,datetime,date


def prepare_filing(name,args,*,financial_only=False):
    from ..sec_filings import SecEdgarProvider
    if args.get('accession_number'):
        from sqlalchemy import select
        from ..database import SessionLocal
        from ..models import SecFilingInventory
        with SessionLocal() as db:
            saved=db.scalar(select(SecFilingInventory).where(SecFilingInventory.symbol==args['symbol'],SecFilingInventory.accession_number==args['accession_number']))
            if saved and (not args.get('form') or saved.form==args['form']) and (not financial_only or saved.form in {'10-K','10-Q'}):
                metadata={key:getattr(saved,key) for key in ('cik','accession_number','form','filed_at','accepted_at','primary_document','source_url')}
                metadata['filed_at']=saved.filed_at.isoformat()
                return {'name':name,'arguments':dict(args),'discovery':metadata}
    try:
        rows=SecEdgarProvider().discover(args['symbol'])
    except Exception as exc:raise ValueError('sec_discovery_unavailable') from exc
    rows=[r for r in rows if (not financial_only or r.form in {'10-K','10-Q'}) and (not args.get('form') or r.form==args['form']) and
          (not args.get('accession_number') or r.accession_number==args['accession_number'])]
    if not rows:raise ValueError('sec_filing_not_found')
    selected=max(rows,key=lambda r:(r.filed_at,r.accession_number))
    metadata=asdict(selected);metadata['filed_at']=selected.filed_at.isoformat()
    return {'name':name,'arguments':{**args,'accession_number':selected.accession_number},'discovery':metadata}


def register_confirmed(operation):
    from sqlalchemy.dialects.postgresql import insert
    from ..database import SessionLocal
    from ..models import SecFilingInventory
    from ..sec_filings import canonical_sec_filing_url,SEC_SOURCE
    data=operation['discovery'];args=operation['arguments']
    if args['accession_number']!=data['accession_number']:raise ValueError('confirmation_parameter_mismatch')
    url=canonical_sec_filing_url(cik=data['cik'],accession_number=data['accession_number'],primary_document=data['primary_document'])
    if url!=data['source_url']:raise ValueError('confirmation_parameter_mismatch')
    values={**data,'symbol':args['symbol'],'filed_at':date.fromisoformat(data['filed_at']),
            'source':SEC_SOURCE,'review_status':'pending_review','content_status':'not_fetched','observed_at':datetime.now(UTC)}
    with SessionLocal() as db:
        db.execute(insert(SecFilingInventory).values(**values).on_conflict_do_nothing(constraint='uq_sec_filing_inventory_cik_accession'))
        db.commit()
