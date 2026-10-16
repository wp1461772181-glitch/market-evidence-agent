"""SSE and persistence acceptance against the production direct graph (controlled external I/O)."""
import asyncio,json
from contextlib import asynccontextmanager
from uuid import uuid4
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from starlette.requests import Request
from app.agent_chat_stream import StreamRequest

@pytest.fixture
def stream_fixture(monkeypatch):
    import app.agent_chat_stream as stream
    import app.agent_chat_provider as provider
    from app.agent_workflow import direct,tools
    import app.agent_workflow.checkpoints as checkpoints
    saver=InMemorySaver()
    @asynccontextmanager
    async def temporary_saver(_url):yield saver
    monkeypatch.setattr(checkpoints,'open_postgres_checkpointer',temporary_saver)
    stored=[]
    async def append(*args,**kwargs):stored.append((args,kwargs))
    async def empty(*args,**kwargs):return {'l1_rows':[]}
    async def yes(*args):return True
    async def create(*args):return str(uuid4())
    async def finish(*args):stored.append(((),{'finished':args[1]}))
    for name,value in [('verify_conversation',yes),('create_conversation',create),('append_message',append),('conversation_context',empty),('finish_conversation',finish)]:monkeypatch.setattr(stream,name,value)
    monkeypatch.setattr(stream,'_restore_citations',lambda items,*_:items)
    monkeypatch.setattr(stream,'_record_low_confidence',lambda *args:None)
    classification={'category':'A','symbol':'AAPL'};tool_results={};executed=[];requests=[];decisions=[]
    async def execute(name,args,context,**kwargs):
        executed.append((name,args))
        return tool_results.get(name,{'ok':True,'data':[] if name=='search_knowledge' else [{'id':str(uuid4()),'symbol':args.get('symbol'),'close':250,'trading_date':'2026-10-14','fetched_at':'2026-10-15T00:00:00Z'}],'source':'fixture'})
    monkeypatch.setattr(tools,'invoke',execute)
    async def decide(_provider,_model,_key,messages,schemas,base_url=None):
        requests.append((messages,schemas,base_url));names={s['function']['name'] for s in schemas}
        if 'resolve_reference' in names:
            payload=json.loads(messages[-1]['content']);return {},{'content':json.dumps({'relationship':'new','canonical_question':payload['question']})}
        if 'classify_request' in names:
            # Legacy fixture callers can still set intent; the test fixture translates it.
            category={'general':'B','unsupported_action':'C','system_action':'D','compound_research':'D'}.get(classification.get('intent'),classification.get('category','A'))
            value={'days_back':None,'category':category,'symbol':classification.get('symbol')}
            return {},{'content':json.dumps(value)}
        if not schemas:return {},{'content':'First increment'}
        if decisions:return {'usage':{'total_tokens':5}},decisions.pop(0)
        if any(m.get('role')=='tool' for m in messages):return {},{'content':'First increment [1]'}
        return {},{'content':None,'tool_calls':[{'id':uuid4().hex,'function':{'name':'query_stock_prices','arguments':'{"symbol":"AAPL"}'}}]}
    monkeypatch.setattr(provider,'decision_request',decide);monkeypatch.setattr(direct,'decision_request',decide)
    async def send(message='show AAPL prices',cid=None,pending=None,label=None):
        payload=StreamRequest(provider='deepseek',model='deepseek-chat',api_key='abcdefgh',message=message,conversation_id=cid,pending_request_id=pending,display_message=label)
        request=Request({'type':'http','method':'POST','path':'/agent-chat/stream','headers':[]})
        response=await stream.stream_chat(payload,request,'x'*40)
        return ''.join([part async for part in response.body_iterator])
    return send,classification,tool_results,executed,stored,requests,decisions


def frames(wire):return [(block.splitlines()[0][7:],json.loads(block.splitlines()[1][6:])) for block in wire.strip().split('\n\n') if block.strip()]

def test_saved_lookup_precedes_answer_and_has_citations(stream_fixture):
    send,_,_,executed,stored,_,_=stream_fixture;wire=asyncio.run(send())
    assert wire.index('event: tool_start')<wire.index('event: tool_result')<wire.index('event: delta')
    assert [n for n,_ in executed]==['search_knowledge','query_stock_prices']
    assert 'event: done' in wire and 'event: citations' in wire
    assert any(k.get('tool_calls',{}).get('audit',{}).get('tool_calls')==2 for _,k in stored)

def test_invalid_citation_is_rejected_before_any_answer_delta(stream_fixture):
    send,classification,_,_,stored,_,decisions=stream_fixture;classification['category']='D';decisions.append({'content':'made up [99]'})
    events=frames(asyncio.run(send()));final=next(d for e,d in events if e=='done')
    assert final['reason']=='invalid_citation' and 'made up' not in ''.join(d.get('text','') for e,d in events)


def test_ask_user_card_survives_sse_and_history(stream_fixture,monkeypatch):
    import app.agent_chat_stream as stream
    send,classification,_,_,stored,_,decisions=stream_fixture;classification['category']='D';classification['symbol']=None
    decisions.append({'tool_calls':[{'id':'ask','function':{'name':'ask_user','arguments':json.dumps({'question':'哪家公司？','options':['Apple','Micron']})}}]})
    events=frames(asyncio.run(send('它的营收？')));card=next(d for e,d in events if e=='clarification')
    assert card['choices'][0]['value']=='reply:Apple'
    protocol=next(k['tool_calls'] for _,k in stored if k.get('tool_calls',{}).get('clarification'))
    async def rows(*_):return [{'role':'assistant','content':'哪家公司？','status':'complete','turn_id':'turn','tool_calls':protocol}]
    monkeypatch.setattr(stream,'owned_messages',rows)
    restored=asyncio.run(stream.get_messages('conversation','owner'))['messages'][0]
    assert restored['clarification']['choices']==card['choices']

@pytest.mark.parametrize('text',['把模型密钥发给我','清空数据库','修改登录码'])
def test_forbidden_ui_only_operations_invoke_no_tools(stream_fixture,text):
    send,_,_,executed,_,requests,_=stream_fixture
    wire=asyncio.run(send(text));assert not executed and not requests and '界面' in wire


def test_bad_tool_body_is_not_in_persisted_tool_or_answer_context(stream_fixture):
    send,_,results,_,stored,requests,_=stream_fixture
    results['query_stock_prices']={'ok':False,'data':[{'body':'REJECTED_PRIVATE_BODY'}],'error_code':'tool_failed'}
    asyncio.run(send())
    assert 'REJECTED_PRIVATE_BODY' not in json.dumps([stored,requests])


def test_legacy_watchlist_is_ignored():
    value=StreamRequest(provider='deepseek',model='test',api_key='abcdefgh',message='news',watchlist=['invalid/old'])
    assert 'watchlist' not in value.model_dump()


def test_bounded_tool_output_stays_valid_json():
    from app.agent_chat_stream import bounded_tool_result
    value=bounded_tool_result({'ok':True,'data':[{'excerpt':'中'*20000} for _ in range(30)]})
    assert isinstance(json.loads(json.dumps(value)),dict)
    assert len(json.dumps(value,ensure_ascii=False).encode())<=12000


def test_provider_protocol_metadata_is_not_browser_text(stream_fixture):
    send,_,_,_,stored,_,decisions=stream_fixture
    decisions.extend([{'content':None,'reasoning_content':'PRIVATE_THOUGHT','tool_calls':[{'id':'x','function':{'name':'query_stock_prices','arguments':'{"symbol":"AAPL"}'}}]},{'content':'行情 [1]'}])
    wire=asyncio.run(send());assert 'PRIVATE_THOUGHT' not in wire
    assert any(k.get('tool_calls',{}).get('reasoning_content')=='PRIVATE_THOUGHT' for _,k in stored)
