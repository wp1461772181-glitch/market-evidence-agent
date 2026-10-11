import asyncio, json
from starlette.requests import Request
from app.agent_chat_stream import StreamRequest, stream_chat


def run_stream(monkeypatch, decision, tool_result=None):
    import app.agent_chat_stream as module
    seen=[]
    async def verify(*args): return True
    async def create(*args): return "12345678-1234-5678-1234-567812345678"
    async def append(*args, **kwargs): seen.append((args,kwargs))
    async def history(*args): return []
    async def finish(*args): pass
    async def decide(*args): return ({}, decision)
    async def tool(*args): return tool_result or {"ok":True,"data":[{"close":250}],"source":"fixture","as_of":"2026-10-09"}
    async def answer(*args):
        yield "First "
        await asyncio.sleep(0)
        yield "increment"
    monkeypatch.setattr(module,"verify_conversation",verify)
    monkeypatch.setattr(module,"create_conversation",create)
    monkeypatch.setattr(module,"append_message",append)
    monkeypatch.setattr(module,"owned_messages",history)
    monkeypatch.setattr(module,"history",history)
    monkeypatch.setattr(module,"finish_conversation",finish)
    monkeypatch.setattr(module,"decision_request",decide)
    monkeypatch.setattr(module,"execute_tool",tool)
    monkeypatch.setattr(module,"stream_answer",answer)
    payload=StreamRequest(provider="deepseek",model="deepseek-chat",api_key="abcdefgh",message="show AAPL",conversation_id=None)
    req=Request({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]})
    response=asyncio.run(module.stream_chat(payload,req,"x"*40))
    async def collect(): return [part async for part in response.body_iterator]
    chunks=asyncio.run(collect())
    return "".join(chunks),seen


def test_tool_event_precedes_incremental_final_answer_and_reasoning_is_persisted(monkeypatch):
    decision={"tool_calls":[{"id":"call-1","type":"function","function":{"name":"query_stock_prices","arguments":"{\"symbol\":\"AAPL\"}"}}],"reasoning_content":"private reasoning","reasoning_details":[{"type":"reasoning.text","text":"private"}]}
    wire,stored=run_stream(monkeypatch,decision)
    assert wire.index("event: tool_start") < wire.index("event: tool_result") < wire.index('event: delta')
    assert wire.count('event: delta') == 2 and "private reasoning" not in wire
    protocol=next(kwargs["tool_calls"] for args,kwargs in stored if kwargs.get("tool_calls"))
    assert protocol["reasoning_content"] == "private reasoning"
    assert "event: done" in wire and "event: error" not in wire


def test_no_tool_answer_still_emits_separate_incremental_deltas(monkeypatch):
    wire,_=run_stream(monkeypatch,{"content":"ordinary answer"})
    assert wire.count("event: delta") == 2
    assert "First " in wire and "increment" in wire and "event: done" in wire


def test_bounded_tool_output_stays_valid_json():
    from app.agent_chat_stream import bounded_tool_result
    result=bounded_tool_result({"ok":True,"data":[{"text":"x"*20000}],"source":"fixture","as_of":None})
    assert result["truncated"] is True
    assert len(json.dumps(result,ensure_ascii=False)) < 8000

def test_actual_tool_content_uses_compact_budget_and_preserves_ordinals(monkeypatch):
    import app.agent_chat_stream as module
    rows=[{"answer":f"[{i}] saved evidence",**{f"k{j}":"x" for j in range(9)}} for i in range(1,72)]
    result=module.bounded_tool_result({"ok":True,"data":rows,"source":"fixture","as_of":None})
    compact=json.dumps(result,ensure_ascii=False,separators=(",",":"))
    spaced=json.dumps(result,ensure_ascii=False)
    assert len(compact.encode("utf-8")) < 8000 < len(spaced.encode("utf-8"))

    decision={"tool_calls":[{"id":"budget-call","type":"function","function":{"name":"query_research_faq","arguments":"{}"}}]}
    wire,stored=run_stream(monkeypatch,decision,tool_result={"ok":True,"data":rows,"source":"fixture","as_of":None})
    persisted=next(args[3] for args,kwargs in stored if len(args)>3 and args[2]=="tool")
    # The streamed result and persisted tool message carry the same canonical JSON content.
    event_line=next(block.split("data: ",1)[1] for block in wire.split("\n\n") if block.startswith("event: tool_result") and '"call_id":"budget-call"' in block)
    streamed=json.loads(event_line)["result"]
    assert len(persisted.encode("utf-8")) <= 8000
    assert len(json.dumps(streamed,ensure_ascii=False,separators=(",",":")).encode("utf-8")) <= 8000
    assert json.loads(persisted)==streamed
    retained=[row["answer"] for row in json.loads(persisted)["data"]]
    assert retained == [row["answer"] for row in rows[:len(retained)]]

def test_system_prompt_sets_numbered_citation_and_financial_claim_contract():
    from app.agent_chat_stream import SYSTEM
    required=(
        'exact [n] ordinals present in the current numbered tool evidence',
        'Never invent, renumber, remap, or reuse a citation number',
        'Never promise or imply guaranteed returns',
        'never state a definite future market direction',
        'Stars are source-credibility ratings, not probabilities',
        'never describe it as a complete filing, full report, or full financial statement',
        'do not replace checking that each factual claim is actually supported',
    )
    assert all(clause in SYSTEM for clause in required)

def test_synthetic_forbidden_financial_outputs_are_covered_by_prompt_contract():
    from app.agent_chat_stream import SYSTEM
    forbidden_outputs=(
        ('[9] Revenue was USD 123 million.', 'Never invent, renumber, remap, or reuse a citation number'),
        ('AAPL is guaranteed to return 20%.', 'Never promise or imply guaranteed returns'),
        ('AAPL will definitely rise tomorrow.', 'never state a definite future market direction'),
        ('Three stars mean a 3-star chance that AAPL rises.', 'Stars are source-credibility ratings, not probabilities'),
        ('This saved excerpt is the complete annual report.', 'never describe it as a complete filing, full report, or full financial statement'),
    )
    assert all(output and rule in SYSTEM for output,rule in forbidden_outputs)


def test_cancelled_stream_persists_terminal_state_and_releases_capacity(monkeypatch):
    import app.agent_chat_stream as module
    import app.agent_chat_api as legacy
    from starlette.requests import Request
    from uuid import uuid4
    seen=[]; cid=str(uuid4())
    async def yes(*args): return True
    async def append(*args, **kwargs): seen.append((args,kwargs))
    async def no_history(*args): return []
    async def finish(*args): seen.append(((),{"finished":args[1]}))
    async def blocked(*args): await asyncio.Event().wait()
    monkeypatch.setattr(module,"verify_conversation",yes)
    monkeypatch.setattr(module,"append_message",append)
    monkeypatch.setattr(module,"history",no_history)
    monkeypatch.setattr(module,"finish_conversation",finish)
    monkeypatch.setattr(module,"decision_request",blocked)
    before=legacy.REQUEST_SLOTS._value
    payload=StreamRequest(provider="deepseek",model="deepseek-chat",api_key="abcdefgh",message="hello",conversation_id=cid)
    req=Request({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]})
    async def exercise():
        response=await module.stream_chat(payload,req,"x"*40)
        iterator=response.body_iterator.__aiter__()
        await iterator.__anext__() # conversation
        await iterator.__anext__() # selecting status
        pending=asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        pending.cancel()
        try: await pending
        except asyncio.CancelledError: pass
        return module.LOCKS.get(cid) is not None and module.LOCKS[cid].locked()
    locked=asyncio.run(exercise())
    assert not locked and legacy.REQUEST_SLOTS._value == before
    assert any(kwargs.get("status")=="cancelled" for _,kwargs in seen)
    assert any(kwargs.get("finished")=="cancelled" for _,kwargs in seen)


def test_disconnect_before_body_iteration_releases_capacity(monkeypatch):
    import app.agent_chat_stream as module
    from starlette.requests import Request
    from uuid import uuid4
    async def yes(*args): return True
    async def nothing(*args,**kwargs): pass
    monkeypatch.setattr(module,"verify_conversation",yes)
    monkeypatch.setattr(module,"append_message",nothing)
    cid=str(uuid4()); before=module.REQUEST_SLOTS._value
    payload=StreamRequest(provider="deepseek",model="deepseek-chat",api_key="abcdefgh",message="hello",conversation_id=cid)
    req=Request({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]})
    async def exercise():
        response=await module.stream_chat(payload,req,"x"*40)
        async def receive(): await asyncio.Event().wait()
        async def send(message):
            if message["type"]=="http.response.start": raise OSError("client disconnected")
        try: await response({"type":"http","method":"POST","path":"/agent-chat/stream","headers":[]},receive,send)
        except BaseException: pass
    asyncio.run(exercise())
    assert module.REQUEST_SLOTS._value==before
    assert cid not in module.LOCKS

@__import__('pytest').mark.parametrize(('result','judge_result','expected_status','expected_pool'),[
    ({'ok':True,'data':[]},None,'active',True),
    ({'ok':True,'data':[{'answer':'some evidence'}]},{'content':'{"sufficient":false,"reason":"missing_fact"}'},'active',True),
    ({'ok':True,'data':[{'answer':'some evidence'}]},{'content':'invalid json'},'failed',False),
    ({'ok':True,'data':[{'answer':'some evidence'}]},'timeout','failed',False),
    ({'ok':False,'data':None,'error_code':'knowledge_unavailable'},None,'failed',False),
    ({'ok':False,'data':None,'error_code':'tool_timeout'},None,'failed',False),
])
def test_knowledge_refusal_and_failure_sse_persists_final_role_status(monkeypatch,result,judge_result,expected_status,expected_pool):
    import app.agent_chat_stream as module
    from starlette.requests import Request
    seen=[];finished=[];decisions=[];pool=[]
    async def yes(*args):return True
    async def create(*args):return '12345678-1234-5678-1234-567812345678'
    async def append(*args,**kwargs):seen.append((args,kwargs))
    async def empty(*args):return []
    async def finish(*args):finished.append(args[1])
    async def decide(*args):
        decisions.append(args)
        if len(decisions)>1 and judge_result=='timeout':raise TimeoutError('judge timeout')
        return ({},{'tool_calls':[{'id':'c1','type':'function','function':{'name':'query_materials','arguments':'{"symbol":"DEMO","keyword":"revenue"}'}}]} if len(decisions)==1 else ({} if judge_result is None else judge_result))
    async def tool(*args):return result
    monkeypatch.setattr(module,'verify_conversation',yes);monkeypatch.setattr(module,'create_conversation',create)
    monkeypatch.setattr(module,'append_message',append);monkeypatch.setattr(module,'history',empty);monkeypatch.setattr(module,'owned_messages',empty)
    monkeypatch.setattr(module,'finish_conversation',finish);monkeypatch.setattr(module,'decision_request',decide);monkeypatch.setattr(module,'execute_tool',tool)
    monkeypatch.setattr(module,'_record_low_confidence',lambda *args:pool.append(args))
    payload=StreamRequest(provider='deepseek',model='deepseek-chat',api_key='abcdefgh',message='DEMO revenue?',conversation_id=None)
    req=Request({'type':'http','method':'POST','path':'/agent-chat/stream','headers':[]})
    async def collect():
        response=await module.stream_chat(payload,req,'x'*40)
        return ''.join([part async for part in response.body_iterator])
    wire=asyncio.run(collect())
    assistant_rows=[(args,kwargs) for args,kwargs in seen if len(args)>2 and args[2]=='assistant']
    final_args,final_kwargs=assistant_rows[-1]
    assert final_args[2]=='assistant'
    assert final_kwargs.get('status','complete')=='failed' if expected_status=='failed' else final_kwargs.get('status','complete')!='failed'
    assert finished[-1]==expected_status
    assert ('event: error' in wire)==(expected_status=='failed')
    if result.get('error_code')=='tool_timeout':
        assert '"code":"tool_timeout"' in wire and 'event: done' not in wire
    assert ('event: done' in wire)==(expected_status=='active')
    if judge_result=='timeout':assert '"code":"knowledge_sufficiency_timeout"' in wire
    assert (len(decisions)==1 if result['data']==[] or result.get('ok') is False else len(decisions)==2)
    assert len(pool)==(1 if expected_pool else 0)
    assert ('关键事实' in wire or '没有找到' in wire or 'unavailable' in wire.lower() or result.get('error_code')=='tool_timeout' or judge_result=='timeout')


@__import__('pytest').mark.parametrize(('tool_result','expected_status','expected_low_confidence'),[
    ({'ok':True,'data':[]},'active',True),
    ({'ok':False,'data':None,'error_code':'knowledge_unavailable'},'failed',False),
])
def test_knowledge_sse_persists_real_messages_and_low_confidence_rows(disposable_database,monkeypatch,tool_result,expected_status,expected_low_confidence):
    import app.agent_chat_stream as module
    from app.agent_chat_models import AgentConversation,AgentMessage
    from app.database import Base,engine,SessionLocal
    from app.knowledge.models import LowConfidenceQuestion
    from sqlalchemy import select
    from starlette.requests import Request
    Base.metadata.create_all(bind=engine,tables=[AgentConversation.__table__,AgentMessage.__table__,LowConfidenceQuestion.__table__])
    calls=[]
    async def decision(*args):
        calls.append(args)
        if len(calls)==1:return {},{'tool_calls':[{'id':'db-c1','type':'function','function':{'name':'query_materials','arguments':'{"symbol":"DEMO","keyword":"revenue"}'}}]}
        return {},{'content':'{"sufficient":true,"reason":"direct"}'}
    async def tool(*args):return tool_result
    monkeypatch.setattr(module,'decision_request',decision);monkeypatch.setattr(module,'execute_tool',tool)
    payload=StreamRequest(provider='deepseek',model='deepseek-chat',api_key='abcdefgh',message='DEMO revenue?',conversation_id=None)
    req=Request({'type':'http','method':'POST','path':'/agent-chat/stream','headers':[]})
    chat_owner='db-test-chat-session-'+expected_status+'-'+'x'*40
    async def collect():
        response=await module.stream_chat(payload,req,chat_owner)
        return ''.join([part async for part in response.body_iterator])
    wire=asyncio.run(collect())
    assert ('event: done' in wire)==(expected_status=='active')
    assert ('event: error' in wire)==(expected_status=='failed')
    from app.agent_chat_store import list_conversations,owned_messages
    async def read_rows():
        conversations=await list_conversations(chat_owner)
        return conversations,await owned_messages(conversations[0]['id'],chat_owner)
    conversations,rows=asyncio.run(read_rows())
    assert len(conversations)==1 and rows[-1]['role']=='assistant'
    assert rows[-1]['status']==('complete' if expected_status=='active' else 'failed')
    with SessionLocal() as session:
        stored=session.scalars(select(LowConfidenceQuestion).where(LowConfidenceQuestion.conversation_id==conversations[0]['id'])).all()
    assert bool(stored)==expected_low_confidence
