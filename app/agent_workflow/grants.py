"""Prepare typed agent suggestions without external fetch or business mutation."""
import copy
from ..agent_tool_catalog import validate,write_tool_names

def suggestion_operation(name,arguments):
    if name not in write_tool_names():raise ValueError('invalid_action_tool')
    args=validate(name,arguments)
    if name in {'fetch_sec','refresh_sec'} and not args.get('accession_number'):
        from sqlalchemy import select
        from ..database import SessionLocal
        from ..models import SecFilingInventory
        with SessionLocal() as db:
            statement=select(SecFilingInventory).where(SecFilingInventory.symbol==args['symbol'])
            if args.get('form'):statement=statement.where(SecFilingInventory.form==args['form'])
            row=db.scalar(statement.order_by(SecFilingInventory.filed_at.desc()).limit(1))
            if not row:raise ValueError('scan_official_first')
            args['accession_number']=row.accession_number
    operation={'name':name,'arguments':copy.deepcopy(args)}
    if name=='ingest_web_source':
        from .web_sources import canonical_url
        operation['arguments']['url']=canonical_url(args['url'])
        operation['grant_only']=True
    return operation
