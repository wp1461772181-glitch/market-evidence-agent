"""The turn graph on the wire, out of the checkpoint, and back in the history.

The graph is drawn while the answer is still being written, so these tests pin both
directions: every node reaches the browser before the answer text, and the saved turn
keeps the same nodes plus the time the stream spent thinking.
"""
import asyncio, json
from types import SimpleNamespace
import pytest
from test_agent_chat_stream import frames, stream_fixture  # noqa: F401

def test_trace_frames_stream_before_the_answer_and_are_saved(stream_fixture):
    send,_,_,_,stored,_,_=stream_fixture
    events=frames(asyncio.run(send()))
    streamed=[data for name,data in events if name=='trace']
    assert streamed and streamed[0]['id']=='n1' and streamed[0]['kind']=='input'
    order=[name for name,_ in events]
    assert order.index('trace')<order.index('delta')
    done=next(data for name,data in events if name=='done')
    assert isinstance(done.get('thinking_ms'),int) and done['thinking_ms']>=0
    protocol=next(kw['tool_calls'] for _,kw in stored
                  if isinstance(kw.get('tool_calls'),dict) and isinstance(kw['tool_calls'].get('trace'),list))
    assert {node['id'] for node in protocol['trace']}=={node['id'] for node in streamed}
    assert isinstance(protocol.get('thinking_ms'),int)
    assert all(isinstance(node.get('kind'),str) for node in protocol['trace'])

def test_the_checkpoint_state_does_not_carry_the_trace():
    from app.agent_chat_store import owner_hash
    from app.agent_workflow.graph import _direct_agent_turn
    from app.agent_workflow.state import AgentRuntimeContext
    async def answerer(*_):return '市盈率是股价与每股收益的比值。'
    context=AgentRuntimeContext('custom','test','dummy',owner_hash=owner_hash('guest'),conversation_id='c',
        classifier=lambda q:{'category':'B','requested_actions':[]},
        resolver=lambda payload:{'relationship':'new','canonical_question':payload.get('question'),'referents':[],'question':payload.get('question'),'choices':[]},
        answerer=answerer)
    state=asyncio.run(_direct_agent_turn({'question':'什么是市盈率','messages':[],'requested_pending_id':None},
                                        SimpleNamespace(context=context)))
    assert 'trace' not in state
    assert state['answer']=='市盈率是股价与每股收益的比值。'

def test_history_returns_the_trace_and_the_thinking_time(monkeypatch):
    import app.agent_chat_stream as stream
    trace=[{'id':'n1','kind':'input','status':'done','after':[],'start_ms':0,'ms':3,'output':{'question':'苹果营收'}},
           {'id':'n2','kind':'output','status':'done','after':['n1'],'start_ms':40,'ms':5,'output':{'status':'succeeded'}}]
    async def rows(*_args,**_kwargs):
        return {'messages':[
            {'role':'assistant','content':'带图','status':'complete','turn_id':'t1','tool_calls':{'trace':trace,'thinking_ms':1234}},
            {'role':'assistant','content':'只有旧计时','status':'complete','turn_id':'t2','tool_calls':{'audit':{'elapsed_ms':900}}},
            {'role':'assistant','content':'都没有','status':'complete','turn_id':'t3','tool_calls':None}],
            'has_more':False,'next_before':None}
    monkeypatch.setattr(stream,'owned_message_page',rows)
    items=asyncio.run(stream.get_messages('conversation','owner'))['messages']
    assert items[0]['trace']==trace and items[0]['thinking_ms']==1234
    assert items[1]['thinking_ms']==900 and 'trace' not in items[1]
    assert 'trace' not in items[2] and 'thinking_ms' not in items[2]
