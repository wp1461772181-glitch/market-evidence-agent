"""Independent reviewer acceptance against real disposable PostgreSQL and provider transport."""
import asyncio
import json
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base, SessionLocal, engine
import app.main as main
import app.agent_tools as tools
import app.agent_chat_store as store
import app.agent_chat_provider as provider
from app.agent_chat_store import owner_hash
from app.agent_chat_models import AgentMessage, AgentConversation
from app.agent_faq_seed import seed_faq
from app.models import MarketPrice, SecFilingInventory, UploadedEvidence

KEY = 'demo-not-a-real-api-key'
SESSION = 'parent-session-' + 'c' * 48

@pytest.fixture(autouse=True)
def setup(disposable_database, monkeypatch):
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed_faq(s)
        if not s.query(MarketPrice).filter(MarketPrice.symbol == 'AAPL', MarketPrice.source == 'agent-fixture').first():
            s.add(MarketPrice(symbol='AAPL',trading_date=date(2026,10,9),open=100,high=102,low=99,
                              close=101,volume=1000,source='agent-fixture',fetched_at=datetime.now(UTC)))
        s.commit()
    async_engine = create_async_engine(engine.url, poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(tools, '_async_session', factory)
    monkeypatch.setattr(store, 'Session', factory)
    from app.agent_workflow.checkpoints import open_postgres_checkpointer
    from app.agent_workflow.settings import checkpoint_database_url
    async def setup_saver():
        async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
            await saver.setup()
    asyncio.run(setup_saver())
    yield
    asyncio.run(async_engine.dispose())


def run(coro):
    return asyncio.run(coro)


def filing(symbol='TSTA'):
    now = datetime.now(UTC)
    excerpt='UniqueParentKeyword official research facts'
    import hashlib
    obj = SecFilingInventory(symbol=symbol, cik='0000320193', accession_number=f'0000320193-26-{uuid4().int % 1000000:06d}', form='10-Q', filed_at=date(2026, 10, 9), primary_document='q.htm', source_url='https://www.sec.gov/Archives/acceptance-q.htm', source='sec-edgar', review_status='pending_review', observed_at=now, content_status='fetched', content_excerpt=excerpt,content_excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),content_observed_at=now)
    with SessionLocal() as s:
        s.add(obj); s.commit()
    return obj


def call(name='query_research_faq', args=None, ident='call-parent'):
    return {'id':ident, 'type':'function', 'function':{'name':name, 'arguments':json.dumps(args or {'keyword':'可信度'}, ensure_ascii=False)}}


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, body): self.body = body
    async def __aiter__(self):
        for chunk in self.body:
            await asyncio.sleep(0)
            yield chunk


def provider_fixture(monkeypatch, decision=None, chunks=None):
    """C6: exercise native function-call decisions instead of the removed stage/stream split."""
    requests=[]
    decision=decision or {'role':'assistant','content':None,'tool_calls':[call()]}
    chunks=chunks or [b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n', b'data: {"choices":[{"delta":{"content":" second"},"finish_reason":"stop"}]}\n\n', b'data: [DONE]\n\n']
    text=[];reasoning=[];details=[];finished=False;invalid=False
    for raw in chunks:
        for line in raw.decode().splitlines():
            if not line.startswith('data:') or line[5:].strip()=='[DONE]':continue
            try:
                event=json.loads(line[5:]);choice=event['choices'][0];delta=choice['delta']
                text.append(delta.get('content') or '')
                if delta.get('reasoning_content'):reasoning.append(delta['reasoning_content'])
                if delta.get('reasoning_details'):details.extend(delta['reasoning_details'])
                if choice.get('finish_reason')=='stop':finished=True
            except Exception:invalid=True
    final={'role':'assistant','content':''.join(text)}
    if reasoning:final['reasoning_content']=''.join(reasoning)
    if details:final['reasoning_details']=details
    for field in ('reasoning_content','reasoning_details'):
        if field in decision:final[field]=decision[field]
    async def handler(request):
        body=json.loads(request.content);requests.append(body)
        names={item['function']['name'] for item in body.get('tools',[])}
        if 'resolve_reference' in names:
            payload=json.loads(body['messages'][-1]['content'])
            message={'role':'assistant','content':json.dumps({'relationship':'new','canonical_question':payload['question']})}
        elif 'classify_request' in names:
            payload=json.loads(body['messages'][-1]['content']);question=payload['question'].lower()
            category='A' if 'aapl' in question else 'B'
            message={'role':'assistant','content':json.dumps({'category':category,'symbol':'AAPL' if category=='A' else None,'days_back':None})}
            for field in ('reasoning_content','reasoning_details'):
                if field in final:message[field]=final[field]
        elif body.get('tools') and not any(m.get('role')=='tool' for m in body['messages']):message=decision
        else:message=final
        if invalid:return httpx.Response(200,json={'choices':[]})
        return httpx.Response(200,json={'model':'test','choices':[{'message':message,'finish_reason':'stop' if finished else 'length'}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(provider.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    return requests


def frames(response):
    output=[]
    for block in response.text.split('\n\n'):
        if not block.strip():continue
        lines=block.splitlines()
        output.append((lines[0][7:], json.loads(next(x[6:] for x in lines if x.startswith('data: ')))))
    return output


def send(client, message='what is credibility', cid=None):
    return client.post('/agent-chat/stream', headers={'X-Chat-Session':SESSION}, json={'provider':'deepseek', 'model':'deepseek-chat','api_key':KEY,'message':message,'conversation_id':cid})


def test_real_saved_prices_keep_numeric_values():
    with SessionLocal() as s:
        s.add(MarketPrice(symbol='TSTP', trading_date=date(2026,10,9),open=101,high=104,low=99,close=102.5,volume=1200,source='parent-fixture',fetched_at=datetime.now(UTC)));s.commit()
    result=run(tools.query_stock_prices.ainvoke({'symbol':'TSTP'}))
    assert result['ok'] and result['data']
    assert result['data'][0]['close'] == 102.5
    assert result['data'][0]['volume'] == 1200


def test_saved_price_rows_name_the_symbol_they_belong_to():
    """S5 live: the rows had no symbol, so the model wrote that it could not confirm the data was
    about the requested company.  Every row now names its symbol; no citation item is added."""
    with SessionLocal() as s:
        for day, close in ((date(2026,10,9), 102.5), (date(2026,10,8), 101.0)):
            s.add(MarketPrice(symbol='TSYM', trading_date=day, open=101, high=104, low=99, close=close,
                              volume=1200, source='parent-fixture', fetched_at=datetime.now(UTC)))
        s.commit()
    result = run(tools.query_stock_prices.ainvoke({'symbol': 'TSYM', 'limit': 5}))
    assert result['ok'] and len(result['data']) == 2
    assert [row.get('symbol') for row in result['data']] == ['TSYM', 'TSYM']
    assert result['data'][0]['close'] == 102.5 and set(result) == {'ok', 'data', 'source', 'as_of'}


def test_real_sec_query_without_form():
    filing('TSTS')
    result=run(tools.query_sec_filings.ainvoke({'symbol':'TSTS'}))
    assert result['ok'] and result['data'][0]['form']=='10-Q'


def test_material_search_without_dense_runtime_is_explicitly_unavailable(monkeypatch):
    monkeypatch.setenv('RAG_ENABLED','true')
    filing('TSTM')
    result=run(tools.query_materials.ainvoke({'symbol':'TSTM','keyword':'UniqueParentKeyword'}))
    assert result=={'ok':False,'data':None,'error_code':'knowledge_unavailable','source':'knowledge_chunks','as_of':None}


def test_faq_without_dense_runtime_is_explicitly_unavailable(monkeypatch):
    monkeypatch.setenv('RAG_ENABLED','true')
    result=run(tools.query_research_faq.ainvoke({'keyword':'费用'}))
    empty=run(tools.query_stock_prices.ainvoke({'symbol':'TSTE'}))
    assert result['error_code']=='knowledge_unavailable' and result['data'] is None
    assert empty['ok'] and empty['data']==[]


def test_disabled_rag_uses_saved_faq_and_material_sql_without_retriever(monkeypatch):
    monkeypatch.setenv('RAG_ENABLED','false')
    async def forbidden(*args,**kwargs): raise AssertionError('dense retriever must not run while RAG is disabled')
    monkeypatch.setattr(tools,'retrieve_faq',forbidden);monkeypatch.setattr(tools,'retrieve_materials',forbidden)
    faq=run(tools.query_research_faq.ainvoke({'keyword':'可信度'}))
    assert faq['ok'] and faq['_retrieval_mode']=='legacy' and faq['data']
    now=datetime.now(UTC)
    upload_id=uuid4()
    from app.knowledge.models import KnowledgeUploadOwner
    with SessionLocal() as s:
        s.add(UploadedEvidence(id=upload_id,symbol='TSTL',title='UniqueLegacyRevenue',source_url='https://example.test/source',published_at=now,observed_at=now,credibility_stars=3,credibility_reason='fixture',impact_severity='low',filename='fixture.txt',content_sha256='a'*64,raw_content=b'x',content_text='UniqueLegacyRevenue revenue fact',status='unconfirmed'))
        s.add(KnowledgeUploadOwner(upload_id=upload_id,owner_hash=owner_hash(SESSION)))
        s.commit()
    from app.knowledge.runtime import research_context
    with research_context(owner_hash(SESSION)):
        material=run(tools.query_materials.ainvoke({'symbol':'TSTL','keyword':'UniqueLegacyRevenue'}))
    assert material['ok'] and material['_retrieval_mode']=='legacy' and any(row['id']==str(upload_id) for row in material['data'])
    with research_context(owner_hash('guest-session-'+'g'*40)):
        guest=run(tools.query_materials.ainvoke({'symbol':'TSTL','keyword':'UniqueLegacyRevenue'}))
    assert guest['data']==[]


def test_disabled_rag_materials_allow_approved_public_sec_only(monkeypatch):
    monkeypatch.setenv('RAG_ENABLED','false')
    now=datetime.now(UTC)
    content='UniqueAcceptedSECExcerpt revenue fact'
    import hashlib
    obj=SecFilingInventory(symbol='TSTU',cik='0000320193',accession_number=f'0000320193-26-{uuid4().int % 1000000:06d}',form='10-Q',filed_at=date.today(),primary_document='q.htm',source_url='https://www.sec.gov/Archives/accepted-q.htm',source='sec-edgar',review_status='accepted',reviewed_at=now,observed_at=now,content_status='fetched',content_observed_at=now,content_excerpt=content,content_excerpt_sha256=hashlib.sha256(content.encode()).hexdigest())
    with SessionLocal() as s:s.add(obj);s.commit()
    result=run(tools.query_materials.ainvoke({'symbol':'TSTU','keyword':'UniqueAcceptedSECExcerpt'}))
    assert result['ok'] and len(result['data'])==1 and result['data'][0]['material_type']=='official_filing'


def test_legacy_unmapped_upload_is_visible_only_to_configured_site_owner(monkeypatch,tmp_path):
    import base64,hashlib,json
    from app.agent_chat_identity import owner_credential_matches
    from app.knowledge.runtime import research_context
    monkeypatch.setenv('RAG_ENABLED','false')
    credential='configured-site-owner-'+'o'*64
    salt=b'test-only-generated-salt'
    config=tmp_path/'generated-login.json'
    config.write_text(json.dumps({'salt':base64.b64encode(salt).decode(),'hash':base64.b64encode(b'x'*32).decode(),'iterations':100000,'chat_credential':credential}))
    monkeypatch.setenv('PERSONAL_CHAT_LOGIN_PATH',str(config))
    now=datetime.now(UTC);upload_id=uuid4()
    with SessionLocal() as s:
        s.add(UploadedEvidence(id=upload_id,symbol='TSTV',title='UnmappedLegacyPrivate',source_url='https://example.test/private',published_at=now,observed_at=now,credibility_stars=3,credibility_reason='fixture',impact_severity='low',filename='fixture.txt',content_sha256='b'*64,raw_content=b'y',content_text='UnmappedLegacyPrivate private fact',status='unconfirmed'));s.commit()
    assert owner_credential_matches(credential)
    with research_context(owner_hash(credential)) as context:
        context.legacy_unmapped_owner=owner_credential_matches(credential)
        owner_result=run(tools.query_materials.ainvoke({'symbol':'TSTV','keyword':'UnmappedLegacyPrivate'}))
    with research_context(owner_hash('guest-session-'+'z'*40)):
        guest_result=run(tools.query_materials.ainvoke({'symbol':'TSTV','keyword':'UnmappedLegacyPrivate'}))
    assert any(row['id']==str(upload_id) for row in owner_result['data'])
    assert guest_result['data']==[]


def test_stream_ownership_persistence_and_tool_disable(monkeypatch):
    requests=provider_fixture(monkeypatch,decision={'role':'assistant','content':None,'tool_calls':[call('query_stock_prices',{'symbol':'AAPL'})]},chunks=[b'data: {"choices":[{"delta":{"content":"AAPL close 101 dollars [1]"},"finish_reason":"stop"}]}\n\n',b'data: [DONE]\n\n'])
    with TestClient(main.app) as client:
        response=send(client,message='show AAPL prices'); events=frames(response)
        assert response.status_code==200 and events[-1][0]=='done'
        assert ''.join(e[1]['text'] for e in events if e[0]=='delta')=='AAPL close 101 dollars [1]'
        assert [e[0] for e in events].index('tool_result')<[e[0] for e in events].index('delta')
        cid=events[0][1]['conversation_id']
        owned=client.get(f'/agent-chat/conversations/{cid}/messages',headers={'X-Chat-Session':SESSION})
        assert owned.status_code==200
        assert client.get(f'/agent-chat/conversations/{cid}/messages',headers={'X-Chat-Session':'other-browser-'+'d'*48}).status_code==404
        assert send(client,message='show AAPL prices',cid=cid).status_code==200
        assert len(requests)>=4
        assert any('classify_request' in str(body.get('tools')) for body in requests)
    with SessionLocal() as s:
        rows=s.query(AgentMessage).filter(AgentMessage.conversation_id == cid).all()
        assert KEY not in json.dumps([{'content':r.content,'tools':r.tool_calls} for r in rows])
        assert {'user','assistant'}<={r.role for r in rows}


def test_bounded_provider_reasoning_is_persisted_for_continuation_but_not_streamed(monkeypatch):
    thought='r'*8100
    decision={'role':'assistant','content':'ready', 'reasoning_content':thought}
    requests=provider_fixture(monkeypatch,decision)
    with TestClient(main.app) as client:
        events=frames(send(client,message='explain this concept'))
        assert events[-1][0]=='done'
        cid=events[0][1]['conversation_id']
        send(client,message='and explain further',cid=cid)
    with SessionLocal() as db:
        assert any((r.tool_calls or {}).get('reasoning_content')==thought for r in db.query(AgentMessage).filter(AgentMessage.conversation_id==cid))
    assert all(thought not in json.dumps(body['messages']) for body in requests)
    assert all(len(json.dumps(body).encode()) < 100_000 for body in requests)


def test_incomplete_provider_stream_cannot_report_success(monkeypatch):
    provider_fixture(monkeypatch,decision={'role':'assistant','content':None,'tool_calls':[call('query_stock_prices',{'symbol':'AAPL'})]},chunks=[b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'])
    with TestClient(main.app) as client:
        events=frames(send(client,message='show AAPL prices'))
    assert events[-1][0]=='done' and events[-1][1]['status']=='failed'
    assert events[-1][1]['reason']=='provider_incomplete_response'


def test_failed_turn_remains_visible_on_restore(monkeypatch):
    provider_fixture(monkeypatch,decision={'role':'assistant','content':None,'tool_calls':[call('query_stock_prices',{'symbol':'AAPL'})]},chunks=[b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',b'data: {"error":{"message":"provider failure"}}\n\n'])
    with TestClient(main.app) as client:
        events=frames(send(client,message='show AAPL prices'));cid=events[0][1]['conversation_id']
        assert events[-1][0]=='done' and events[-1][1]['status']=='failed'
        rows=client.get(f'/agent-chat/conversations/{cid}/messages',headers={'X-Chat-Session':SESSION}).json()['messages']
        assert any(r.get('status')=='failed' for r in rows)
        assert any(r['role']=='assistant' and r['content'].strip() for r in rows)
        assert all('provider failure' not in r['content'] for r in rows)


def test_stream_validates_bounded_key_and_nonblank_message(monkeypatch):
    provider_fixture(monkeypatch)
    with TestClient(main.app) as client:
        for key,message in [('x'*2049,'valid'), (KEY,'   ')]:
            response=client.post('/agent-chat/stream',headers={'X-Chat-Session':SESSION},json={'provider':'deepseek','model':'deepseek-chat','api_key':key,'message':message})
            assert response.status_code==422
            assert key not in response.text


def test_current_message_is_sent_and_no_tool_path_streams(monkeypatch):
    requests=provider_fixture(monkeypatch,decision={'role':'assistant','content':'decision only'})
    with TestClient(main.app) as client:
        events=frames(send(client,message='parent-current-message'))
    assert events[-1][0]=='done'
    assert len(requests)==2  # Classification and independent tool-free B generation.
    assert requests[0]['messages'][-1]['role']=='user'
    assert json.loads(requests[0]['messages'][-1]['content'])['question']=='parent-current-message'
    assert not any(e[0]=='tool_start' for e in events)


def test_duplicate_native_tool_requests_execute_once_and_stop(monkeypatch):
    requests=provider_fixture(monkeypatch,decision={'role':'assistant','content':None,'tool_calls':[call('query_stock_prices',{'symbol':'AAPL'},ident='call-1'),call('query_stock_prices',{'symbol':'AAPL'},ident='call-2')]})
    with TestClient(main.app) as client:
        events=frames(send(client,message='show AAPL prices'))
    results=[e[1] for e in events if e[0]=='tool_result' and e[1]['name']=='query_stock_prices']
    assert len(results)==1 and results[0]['call_id']=='call-1'
    assert events[-1][0]=='done' and events[-1][1]['reason']=='no_progress'
    assert any(e[0]=='citations' for e in events)


def test_chat_capacity_applies_to_entire_stream(monkeypatch):
    import app.agent_chat_api as legacy
    monkeypatch.setattr(legacy.REQUEST_SLOTS,'_value',0)
    provider_fixture(monkeypatch)
    with TestClient(main.app) as client:
        response=send(client)
    assert response.status_code==503


def test_provider_redirect_is_rejected_safely(monkeypatch):
    async def handler(request):
        return httpx.Response(302,headers={'location':'https://untrusted.invalid/anything'},json={'choices':[{'message':{'role':'assistant','content':'redirect'}}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(provider.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    with TestClient(main.app) as client:
        events=frames(send(client))
    assert events[-1][0]=='done' and events[-1][1]['status']=='failed'
    assert events[-1][1]['reason']=='provider_redirect_rejected'
    assert 'untrusted.invalid' not in json.dumps(events)


def test_final_reasoning_preserved_for_next_turn_but_hidden_from_browser(monkeypatch):
    chunks=[b'data: {"choices":[{"delta":{"reasoning_content":"private-final-reasoning"}}]}\n\n',b'data: {"choices":[{"delta":{"content":"public answer"},"finish_reason":"stop"}]}\n\n',b'data: [DONE]\n\n']
    requests=provider_fixture(monkeypatch,decision={'role':'assistant','content':None,'tool_calls':[call('query_stock_prices',{'symbol':'AAPL'})]},chunks=chunks)
    with TestClient(main.app) as client:
        response=send(client,message='show AAPL prices');events=frames(response);cid=events[0][1]['conversation_id']
        assert events[-1][0]=='done'
        assert 'private-final-reasoning' not in response.text
        restored=client.get(f'/agent-chat/conversations/{cid}/messages',headers={'X-Chat-Session':SESSION})
        assert 'private-final-reasoning' not in restored.text
        assert send(client,message='show AAPL prices',cid=cid).status_code==200
    with SessionLocal() as db:
        assert any((r.tool_calls or {}).get('reasoning_content')=='private-final-reasoning' for r in db.query(AgentMessage).filter(AgentMessage.conversation_id==cid))
    assert all('private-final-reasoning' not in json.dumps(body['messages']) for body in requests)


def test_forecast_uses_current_workspace_version_and_structured_probabilities():
    from app.forecast_v2_models import ForecastJobV2, ForecastVersionV2
    now=datetime.now(UTC)
    with SessionLocal() as s:
        job=ForecastJobV2(symbol='TSTF',kind='new',status='succeeded',idempotency_key=f'parent-{uuid4()}',request_fingerprint='b'*64)
        s.add(job);s.flush()
        ident=uuid4()
        root=ForecastVersionV2(id=ident,root_id=ident,job_id=job.id,version_no=1,symbol='TSTF',target_contract={'target_end_date':'2027-01-29'},target_contract_hash='a'*64,decision_at=now,market_cutoff_at=now,price_input_manifest={},evidence_version_manifest=[],feature_snapshot={},baseline_probabilities={'bearish':0.2,'neutral':0.3,'bullish':0.5},joint_probabilities=None,model_status='research_only',model_manifest={},trigger_type='manual')
        s.add(root);s.commit()
    result=run(tools.query_latest_forecast.ainvoke({'symbol':'TSTF'}))
    assert result['ok'] and result['data']
    row=result['data'][0]
    assert row['version_no']==1
    assert row['probabilities']=={'bearish':0.2,'neutral':0.3,'bullish':0.5}
    assert 'forecast_versions_v2' in result['source']


def test_provider_truncation_finish_cannot_be_marked_success(monkeypatch):
    provider_fixture(monkeypatch,chunks=[b'data: {"choices":[{"delta":{"content":"truncated"},"finish_reason":"length"}]}\n\n',b'data: [DONE]\n\n'])
    with TestClient(main.app) as client:
        events=frames(send(client,message='show AAPL prices'))
    assert events[-1][0]=='done' and events[-1][1]['status']=='failed'
    assert events[-1][1]['reason']=='provider_incomplete_response'


def test_disconnect_before_body_iteration_releases_capacity(monkeypatch):
    import app.agent_chat_stream as stream
    import app.agent_chat_api as legacy
    from starlette.requests import Request
    before=legacy.REQUEST_SLOTS._value
    monkeypatch.setattr(legacy.REQUEST_SLOTS,'_value',before)
    async def exercise():
        scope={'type':'http','method':'POST','path':'/agent-chat/stream','headers':[],'asgi':{'spec_version':'2.4'}}
        response=await stream.stream_chat(stream.StreamRequest(provider='deepseek',model='deepseek-chat',api_key=KEY,message='never started'),Request(scope),SESSION)
        async def disconnected_send(message): raise OSError('peer disconnected before headers')
        async def receive():return {'type':'http.disconnect'}
        try:await response(scope,receive,disconnected_send)
        except Exception:pass
    run(exercise())
    assert legacy.REQUEST_SLOTS._value==before


def test_final_openrouter_reasoning_details_round_trip_without_exposure(monkeypatch):
    details=[{'type':'reasoning.encrypted','data':'encrypted-fixture','id':'reason-1','index':0}]
    piece=json.dumps({'choices':[{'delta':{'reasoning_details':details}}]}).encode()
    chunks=[b'data: '+piece+b'\n\n',b'data: {"choices":[{"delta":{"content":"public answer"},"finish_reason":"stop"}]}\n\n',b'data: [DONE]\n\n']
    requests=provider_fixture(monkeypatch,decision={'role':'assistant','content':None,'tool_calls':[call('query_stock_prices',{'symbol':'AAPL'})]},chunks=chunks)
    with TestClient(main.app) as client:
        events=frames(send(client,message='show AAPL prices'));cid=events[0][1]['conversation_id']
        assert events[-1][0]=='done'
        assert 'encrypted-fixture' not in client.get(f'/agent-chat/conversations/{cid}/messages',headers={'X-Chat-Session':SESSION}).text
        send(client,message='show AAPL prices',cid=cid)
    with SessionLocal() as db:
        assert any((r.tool_calls or {}).get('reasoning_details')==details for r in db.query(AgentMessage).filter(AgentMessage.conversation_id==cid))
    assert all('encrypted-fixture' not in json.dumps(body['messages']) for body in requests)
