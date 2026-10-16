import asyncio,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,datetime,timedelta
import pytest
from sqlalchemy import select
from app.database import Base,engine,SessionLocal
from app.agent_workflow import direct,consent,tools,web_sources
from app.agent_workflow.direct import Turn,run_direct_turn
from app.agent_workflow.models import AgentConsent,AgentWebSource
from app.agent_workflow.state import AgentRuntimeContext

@pytest.fixture
def owner(monkeypatch,disposable_database):
    Base.metadata.create_all(engine)
    for m in (direct,consent,tools):monkeypatch.setattr(m,'owner_credential_matches',lambda c:c=='v4-owner')
    from app.agent_chat_store import owner_hash
    return AgentRuntimeContext('custom','test','dummy',owner_hash=owner_hash('v4-owner'),identity_credential='v4-owner',conversation_id=str(uuid.uuid4()))

async def emit(_):pass

def request(owner,name='initialize_prices',args=None):
    turn=Turn('看看AMD',owner,emit,[],{})
    turn.route='D';turn.scope={'symbol':'AMD'}
    if args and args.get('url'):turn.sets=[{'id':'seen-web','owner':owner.owner_hash,'conversation':owner.conversation_id,'scope':{'symbol':'AMD'},'tool':'web_research','rows':[{'url':args['url'],'title':'AMD update'}]}]
    outcome=asyncio.run(turn.request_action({'tool':name,'arguments':args or {'symbol':'AMD'},'reason':'可补齐缺少的历史数据'}))
    return turn,outcome

def confirm(owner,card,word=None):
    return asyncio.run(run_direct_turn(word or card['choices'][0]['value'],owner,emit=emit,state={'pending_request':card,'requested_pending_id':card['id']}))

def test_suggestion_only_mints_capability_and_is_limited_to_one(owner,monkeypatch):
    invoked=[];monkeypatch.setattr(tools,'execute_costly',lambda *a:invoked.append(a) or {'ok':True})
    turn,result=request(owner)
    assert result['ok'] and not invoked
    card=turn.pending
    assert card['display']['agent_suggestion'] is True
    assert '不是你的要求' in card['display']['title']
    second=asyncio.run(turn.request_action({'tool':'initialize_prices','arguments':{'symbol':'AMD'},'reason':'again'}))
    assert second['error_code']=='suggestion_limit' and not invoked
    assert confirm(owner,card,'cancel:'+card['id'])['audit']['write_outcome']=='cancelled'
    assert not invoked

def test_suggestion_arguments_must_match_target_schema(owner):
    turn,result=request(owner,args={'symbol':'AMD','days':-1})
    assert result['error_code']=='invalid_arguments' and turn.pending is None

def test_caller_cannot_assert_confirmation_without_capability(owner):
    effects=[]
    from dataclasses import replace
    context=replace(owner,executor=lambda *a:effects.append(a) or {'ok':True})
    try:result=asyncio.run(tools.invoke('add_to_research_queue',{'symbol':'AMD'},context,confirmed=True))
    except TypeError:result={'ok':False}
    assert not result['ok'] and not effects

def test_approval_button_maps_to_operation_and_replay_is_once(owner,monkeypatch):
    invoked=[];monkeypatch.setattr(tools,'execute_costly',lambda operation,key,context:invoked.append((operation,key)) or {'ok':True})
    turn,_=request(owner);card=turn.pending
    for _ in range(2):
        result=confirm(owner,card,'authorize:'+card['id'])
        assert result['status']=='succeeded' and result['audit']['write_outcome']=='executed'
    assert len(invoked)==1

def test_simultaneous_approval_uses_existing_consent_serialization(owner,monkeypatch):
    invoked=[];monkeypatch.setattr(tools,'execute_costly',lambda operation,key,context:invoked.append(key) or {'ok':True})
    turn,_=request(owner);card=turn.pending
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:confirm(owner,card),range(2)))
    assert len(invoked)==1 and all(r['status']=='succeeded' for r in results)

def test_failed_approved_operation_reports_refusal_and_next_step(owner,monkeypatch):
    monkeypatch.setattr(tools,'execute_costly',lambda *a:{'ok':False,'error_code':'upstream_unavailable'})
    turn,_=request(owner)
    result=confirm(owner,turn.pending)
    assert result['status']=='failed' and result['audit']['write_outcome']=='refused:upstream_unavailable'
    assert '已执行确认的操作' not in result['answer'] and '下一步' in result['answer']

def test_approved_web_without_newer_body_does_not_claim_ingestion(owner,monkeypatch):
    async def prepare(self,operation):return {'ok':True,'confirmation':None,'outcome':{'ok':True,'data':[{'status':'not_newer'}]}}
    monkeypatch.setattr(Turn,'prepare_approved_web',prepare)
    turn,_=request(owner,'ingest_web_source',{'url':'https://example.org/AMD/unchanged','symbol':'AMD'})
    result=confirm(owner,turn.pending)
    assert result['audit']['write_outcome']=='refused:not_newer'
    assert '无需入库' in result['answer'] and '已执行确认的操作' not in result['answer']

def test_web_suggestion_requires_second_frozen_confirmation_and_replays_preparation_once(owner,monkeypatch):
    url='https://example.org/AMD/'+uuid.uuid4().hex
    fetched=[];written=[]
    page={'url':url,'title':'AMD update','excerpt':'AMD released a product.','published_at':(datetime.now(UTC)-timedelta(days=1)).isoformat(),'fetched_at':datetime.now(UTC).isoformat(),'body_provenance':'public_page_fetch'}
    async def capture(*a,**kw):fetched.append(a);return dict(page)
    monkeypatch.setattr(web_sources,'capture',capture)
    monkeypatch.setattr(tools,'execute_costly',lambda operation,key,ctx:written.append(operation) or {'ok':True})
    turn,_=request(owner,'ingest_web_source',{'url':url,'company':'AMD','symbol':'AMD'})
    assert not fetched and not written
    grant=turn.pending;first=confirm(owner,grant)
    final=first['pending_request']
    assert final and final['reason']=='write_confirmation' and len(fetched)==1 and not written
    again=confirm(owner,grant)
    assert again['pending_request']['id']==final['id'] and len(fetched)==1
    with SessionLocal() as db:
        saved=db.get(AgentConsent,uuid.UUID(final['id']))
        assert saved.operations[0]['snapshot']['excerpt']==page['excerpt']
        assert not db.scalar(select(AgentWebSource).where(AgentWebSource.source_url==url))
    assert confirm(owner,final)['status']=='succeeded' and len(written)==1
    assert written[0]['snapshot']['excerpt']==page['excerpt']
