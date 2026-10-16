import asyncio
import uuid
from datetime import date
import pytest
from app.agent_workflow import consent, grants
from app.agent_workflow.models import AgentConsent
from app.database import Base,engine,SessionLocal

@pytest.fixture
def owner(monkeypatch,disposable_database):
    Base.metadata.create_all(engine,tables=[AgentConsent.__table__])
    monkeypatch.setattr(consent,'owner_credential_matches',lambda c:c=='owner')
    return 'owner'

def test_independent_batch_continues_and_replays_without_reexecuting(owner):
    ops=[{'name':'index','arguments':{'max_tasks':n}} for n in (1,2,3)]
    card=consent.propose(owner,'batch',ops,{})
    token=card['choices'][0]['value'].split(':',1)[1];calls=[]
    def handler(op,key):
        calls.append(key)
        if len(calls)==1:raise ValueError('private-service-error')
        return {'ok':True,'effect':op['arguments']['max_tasks']}
    first=consent.execute(owner,'batch',token,ops,handler)
    assert len(calls)==3 and not first['ok'] and len(first['data'])==3
    assert first['data'][0]['error_code']=='write_service_failed' and first['data'][1]['effect']==2
    assert 'private-service-error' not in str(first)
    assert consent.execute(owner,'batch',token,ops,handler)==first and len(calls)==3

def test_permission_failure_stops_remaining_targets(owner):
    ops=[{'name':'index','arguments':{'max_tasks':n}} for n in (1,2)]
    card=consent.propose(owner,'revoked',ops,{})
    calls=[]
    result=consent.execute(owner,'revoked',card['choices'][0]['value'].split(':',1)[1],ops,lambda *_:calls.append(1) or {'ok':False,'error_code':'permission_revoked'})
    assert calls==[1] and result['data'][1]['error_code']=='batch_stopped'

def test_cancellation_during_one_effect_stops_later_items_and_preserves_completed_results(owner):
    ops=[{'name':'index','arguments':{'max_tasks':n}} for n in (1,2,3)]
    card=consent.propose(owner,'cancel-running',ops,{})
    calls=[]
    def handler(op,key):
        calls.append(key)
        assert consent.cancel(owner,'cancel-running',card['id'])['status']=='cancelling'
        return {'ok':True,'effect':1}
    token=card['choices'][0]['value'].split(':',1)[1]
    result=consent.execute(owner,'cancel-running',token,ops,handler)
    assert len(calls)==1 and result['data'][0]['ok']
    assert all(item['error_code']=='batch_stopped' for item in result['data'][1:])
    assert consent.execute(owner,'cancel-running',token,ops,handler)==result

def test_cancelled_agent_await_requests_batch_stop_without_repeating_started_effect(owner,monkeypatch):
    import threading
    from app.agent_workflow import direct
    from app.agent_workflow.state import AgentRuntimeContext
    monkeypatch.setattr(direct,'owner_credential_matches',lambda c:c=='owner')
    ops=[{'name':'index','arguments':{'max_tasks':n}} for n in (1,2)]
    card=consent.propose(owner,'cancel-await',ops,{})
    started=threading.Event();release=threading.Event();completed=threading.Event();calls=[]
    def effect(self,op,key):
        calls.append(key);started.set();release.wait(2)
        return {'ok':True}
    monkeypatch.setattr(direct.Turn,'execute_approved_operation',effect)
    original=consent.execute
    def execute(*args,**kwargs):
        try:return original(*args,**kwargs)
        finally:completed.set()
    monkeypatch.setattr(consent,'execute',execute)
    ctx=AgentRuntimeContext('custom','test','dummy',identity_credential='owner',conversation_id='cancel-await')
    async def emit(_):pass
    async def scenario():
        task=asyncio.create_task(direct.run_direct_turn(card['choices'][0]['value'],ctx,emit=emit,state={'pending_request':card,'requested_pending_id':card['id']}))
        assert await asyncio.to_thread(started.wait,2)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):await task
            assert consent.lookup(owner,'cancel-await',card['id'])['status']=='cancelling'
        finally:release.set()
        assert await asyncio.to_thread(completed.wait,2)
    asyncio.run(scenario())
    assert len(calls)==1

def test_cancel_committed_before_next_durable_claim_stops_that_item(owner,monkeypatch):
    from sqlalchemy.orm import Session
    ops=[{'name':'index','arguments':{'max_tasks':n}} for n in (1,2,3)]
    card=consent.propose(owner,'cancel-claim-race',ops,{})
    original_refresh=Session.refresh;original_scalar=Session.scalar
    reads={'refresh':0,'claim':0};injected=[]
    def cancel_once():
        if injected:return
        injected.append(True)
        assert consent.cancel(owner,'cancel-claim-race',card['id'])['status']=='cancelling'
    def refresh(self,instance,*args,**kwargs):
        result=original_refresh(self,instance,*args,**kwargs)
        if isinstance(instance,AgentConsent):
            reads['refresh']+=1
            if reads['refresh']==2:cancel_once()
        return result
    def scalar(self,statement,*args,**kwargs):
        # Schedule the competing commit before the next atomic row claim;
        # the refresh hook also exercises the former stale-read race.
        where=str(getattr(statement,'whereclause',''))
        if getattr(statement,'_for_update_arg',None) is not None and 'agent_consents.id =' in where and not injected:
            reads['claim']+=1
            if reads['claim']==2:cancel_once()
        return original_scalar(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,'refresh',refresh);monkeypatch.setattr(Session,'scalar',scalar)
    calls=[]
    result=consent.execute(owner,'cancel-claim-race',card['choices'][0]['value'].split(':',1)[1],ops,lambda _,key:calls.append(key) or {'ok':True})
    assert injected and len(calls)==1
    assert all(item.get('error_code')=='batch_stopped' for item in result['data'][1:])

def test_item_in_flight_is_journaled_before_effect_and_crash_is_not_replayed(owner):
    ops=[{'name':'index','arguments':{}}];card=consent.propose(owner,'crash-v5',ops,{})
    def handler(*_):
        with SessionLocal() as db:
            row=db.get(AgentConsent,uuid.UUID(card['id']))
            assert row.result['items'][0]['status']=='executing'
        raise KeyboardInterrupt()
    token=card['choices'][0]['value'].split(':',1)[1]
    with pytest.raises(KeyboardInterrupt):consent.execute(owner,'crash-v5',token,ops,handler)
    with pytest.raises(consent.ConsentError,match='consumed'):consent.execute(owner,'crash-v5',token,ops,lambda *_:pytest.fail('repeat'))

def test_sec_suggestion_can_be_approved_without_prior_inventory_and_does_no_discovery(monkeypatch):
    from app.sec_filings import SecEdgarProvider
    monkeypatch.setattr(SecEdgarProvider,'discover',lambda *_:pytest.fail('no network before first approval'))
    op=grants.suggestion_operation('fetch_sec',{'symbol':'INTC'})
    assert op['grant_only'] is True and not op['arguments']['accession_number']

def test_official_discovery_selects_latest_metadata_without_business_writes(monkeypatch):
    from app.agent_workflow.sec_sources import prepare_filing
    from app.sec_filings import DiscoveredSecFiling,SecEdgarProvider
    def discovered(*_):return [DiscoveredSecFiling('0000050863','0000050863-26-000001','10-Q',date(2026,8,1),None,'report.htm','https://www.sec.gov/Archives/edgar/data/50863/000005086326000001/report.htm')]
    monkeypatch.setattr(SecEdgarProvider,'__init__',lambda *_:None)
    monkeypatch.setattr(SecEdgarProvider,'discover',discovered)
    op=prepare_filing('fetch_sec',{'symbol':'INTC','form':'10-Q','accession_number':None})
    assert op['arguments']['accession_number']=='0000050863-26-000001' and op['discovery']['form']=='10-Q'

def test_explicit_saved_historical_filing_does_not_depend_on_recent_official_list(owner,monkeypatch):
    from app.agent_workflow.sec_sources import prepare_filing
    from app.sec_filings import SecEdgarProvider
    from app.models import SecFilingInventory
    from datetime import datetime,UTC
    Base.metadata.create_all(engine,tables=[SecFilingInventory.__table__])
    accession='0000050863-20-000001'
    with SessionLocal() as db:
        db.add(SecFilingInventory(symbol='INTC',cik='0000050863',accession_number=accession,form='10-K',filed_at=date(2020,2,1),primary_document='report.htm',
            source_url='https://www.sec.gov/Archives/edgar/data/50863/000005086320000001/report.htm',source='sec-edgar',review_status='pending_review',content_status='not_fetched',observed_at=datetime.now(UTC)))
        db.commit()
    monkeypatch.setattr(SecEdgarProvider,'discover',lambda *_:pytest.fail('explicit saved accession needs no recent listing'))
    op=prepare_filing('fetch_sec',{'symbol':'INTC','form':'10-K','accession_number':accession})
    assert op['discovery']['filed_at']=='2020-02-01' and op['arguments']['accession_number']==accession


def test_registration_occurs_only_inside_confirmed_execution_and_is_idempotent(owner,monkeypatch):
    from app.agent_workflow import tools,actions
    from app.agent_workflow.state import AgentRuntimeContext
    from app.models import SecFilingInventory
    from sqlalchemy import select
    Base.metadata.create_all(engine,tables=[SecFilingInventory.__table__])
    monkeypatch.setattr(tools,'owner_credential_matches',lambda c:c=='owner')
    op={'name':'fetch_sec','arguments':{'symbol':'INTC','accession_number':'0000050863-26-000001'},'discovery':{
        'cik':'0000050863','accession_number':'0000050863-26-000001','form':'10-Q','filed_at':'2026-08-01','accepted_at':None,'primary_document':'report.htm',
        'source_url':'https://www.sec.gov/Archives/edgar/data/50863/000005086326000001/report.htm'}}
    calls=[]
    def action(**_):
        with SessionLocal() as db:assert db.scalar(select(SecFilingInventory).where(SecFilingInventory.accession_number==op['arguments']['accession_number']))
        calls.append(1);return {'status':'succeeded'}
    monkeypatch.setattr(actions,'execute_action',action)
    card=consent.propose(owner,'register',[op],{})
    with SessionLocal() as db:assert not db.scalar(select(SecFilingInventory).where(SecFilingInventory.accession_number==op['arguments']['accession_number']))
    ctx=AgentRuntimeContext('custom','test','dummy',identity_credential='owner')
    token=card['choices'][0]['value'].split(':',1)[1]
    for _ in range(2):
        assert consent.execute(owner,'register',token,[op],lambda operation,key:tools.execute_costly(operation,key,ctx))['ok']
    assert calls==[1]


def test_suggested_sec_discovery_waits_for_initial_approval_and_returns_frozen_second_card(owner,monkeypatch):
    from app.agent_workflow import direct,sec_sources,tools
    from app.agent_workflow.state import AgentRuntimeContext
    for module in (direct,tools):monkeypatch.setattr(module,'owner_credential_matches',lambda c:c=='owner')
    calls=[]
    def discover(name,args,**_):
        calls.append('metadata')
        return {'name':name,'arguments':{**args,'accession_number':'0000050863-26-000001'},'discovery':{'filed_at':'2026-08-01','form':'10-Q','source_url':'https://www.sec.gov/Archives/example.htm'}}
    monkeypatch.setattr(sec_sources,'prepare_filing',discover)
    monkeypatch.setattr(tools,'execute_costly',lambda *_:pytest.fail('no download before second confirmation'))
    async def emit(_):pass
    ctx=AgentRuntimeContext('custom','test','dummy',identity_credential='owner',conversation_id='two-stage',classifier=lambda _:'D',
        decider=lambda *_:{'tool_calls':[{'id':'suggest','function':{'name':'request_action','arguments':'{"tool":"fetch_sec","arguments":{"symbol":"INTC"},"reason":"缺少原文"}'}}]})
    initial=asyncio.run(direct.run_direct_turn('INTC 的资料还缺什么？',ctx,emit=emit))
    assert calls==[] and initial['pending_request']['display']['agent_suggestion']
    first_card=initial['pending_request']
    state={'pending_request':first_card,'requested_pending_id':first_card['id']}
    first=asyncio.run(direct.run_direct_turn(first_card['choices'][0]['value'],ctx,emit=emit,state=state))
    again=asyncio.run(direct.run_direct_turn(first_card['choices'][0]['value'],ctx,emit=emit,state=state))
    assert calls==['metadata'] and first['pending_request']['id']==again['pending_request']['id']
    saved=consent.lookup('owner','two-stage',first['pending_request']['id'])
    assert saved['operations'][0]['arguments']['accession_number']=='0000050863-26-000001'
