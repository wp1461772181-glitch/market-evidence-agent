import asyncio,json,uuid
import pytest
from sqlalchemy import text
from app.database import Base,engine,SessionLocal
from app.agent_workflow import tools
from app.agent_workflow.state import AgentRuntimeContext
from app.agent_chat_store import owner_hash

@pytest.fixture
def status_db(disposable_database,monkeypatch):
    Base.metadata.create_all(engine)
    from scripts.migrate_agent_actions import apply
    apply()
    monkeypatch.setattr(tools,'owner_credential_matches',lambda value:value=='owner')
    return AgentRuntimeContext('custom','test','dummy',identity_credential='owner',owner_hash=owner_hash('owner'))

def test_job_status_filters_symbol_before_limit_and_remains_owner_scoped(status_db):
    owner=status_db.owner_hash
    with SessionLocal() as db:
        for index,(who,symbol) in enumerate([(owner,'AMD'),(owner,'AAPL'),(owner_hash('other'),'AMD')]):
            db.execute(text("INSERT INTO agent_action_requests(id,owner_hash,request_key,action,payload_digest,status,result,created_at) VALUES(:id,:owner,:key,'fetch_sec','digest','succeeded',CAST(:result AS jsonb),now()+CAST(:offset AS interval))"),{'offset':str(index)+' seconds','id':uuid.uuid4(),'owner':who,'key':uuid.uuid4().hex,'result':json.dumps({'status':'completed','symbol':symbol})})
        db.commit()
    r=asyncio.run(tools.invoke('query_system_status',{'kind':'jobs','symbol':'AMD','limit':1},status_db))
    assert r['ok'] and len(r['data'])==1 and r['data'][0]['result']['symbol']=='AMD'

def test_guest_cannot_read_owner_action_ledger(status_db):
    from dataclasses import replace
    status_db=replace(status_db,identity_credential='guest')
    r=asyncio.run(tools.invoke('query_system_status',{'kind':'jobs'},status_db))
    assert not r['ok'] and r['error_code']=='owner_login_required'
