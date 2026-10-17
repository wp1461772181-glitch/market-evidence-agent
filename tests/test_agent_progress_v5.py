"""Progress, rather than cumulative work, governs cancellation. No paid calls."""
import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from app import agent_chat_provider as provider
from app.agent_workflow import direct
from app.agent_workflow.state import AgentRuntimeContext


def test_cumulative_work_does_not_exhaust_healthy_turn():
    turn = direct.Turn('hello', AgentRuntimeContext('custom', 'test', 'dummy'), None, [], {})
    turn.started -= 10000
    turn.audit.update(token_usage=10_000_000, tool_calls=500, distinct_successful_calls=500)
    assert turn.budget() is None


def test_decision_and_refused_call_ceilings_stop_a_runaway_turn():
    """A turn ends on the decision ceiling or on repeated server-refused calls, never on spend."""
    spent = direct.Turn('hello', AgentRuntimeContext('custom', 'test', 'dummy'), None, [], {})
    spent.audit['decision_steps'] = direct.MAX_DECISION_STEPS
    assert spent.budget() == 'no_progress'
    refused = direct.Turn('hello', AgentRuntimeContext('custom', 'test', 'dummy'), None, [], {})
    refused.invalid_calls = {f'bad-{i}' for i in range(direct.MAX_INVALID_CALLS)}
    assert refused.budget() == 'no_progress'
    healthy = direct.Turn('hello', AgentRuntimeContext('custom', 'test', 'dummy'), None, [], {})
    healthy.audit['decision_steps'] = direct.MAX_DECISION_STEPS - 1
    assert healthy.budget() is None


def test_model_usage_is_recorded_after_old_cumulative_limit(monkeypatch):
    async def response(*_,**__):return {'usage':{'total_tokens':19}},{'content':'ok'}
    monkeypatch.setattr(direct,'decision_request',response)
    t=direct.Turn('hello',AgentRuntimeContext('custom','test','dummy'),None,[],{})
    t.audit['token_usage']=1_000_000
    assert asyncio.run(t.model([{'role':'user','content':'hello'}],[]))['content']=='ok'
    assert t.audit['token_usage']==1_000_019


def test_long_work_keeps_recent_complete_tool_protocol_in_bounded_model_context():
    t=direct.Turn('research',AgentRuntimeContext('custom','test','dummy'),None,[],{})
    for i in range(100):
        t.protocol.extend([{'role':'assistant','tool_calls':[{'id':str(i),'function':{'name':'query_system_status','arguments':'{"kind":"monitor"}'}}]},
            {'role':'tool','tool_call_id':str(i),'content':json.dumps({'ok':True,'data':['x'*5000]})}])
    payload=t.tool_messages()
    assert len(json.dumps(payload).encode())<100000
    ids={c['id'] for m in payload if m['role']=='assistant' for c in m.get('tool_calls',[])}
    assert ids and all(m['tool_call_id'] in ids for m in payload if m['role']=='tool')
    assert '99' in ids


def test_watchdog_progress_renews_but_no_progress_expires():
    from app.agent_progress import progress_watchdog, mark_progress
    async def exercise():
        async with progress_watchdog(.04):
            for _ in range(6):
                await asyncio.sleep(.015)
                mark_progress()
        with pytest.raises(TimeoutError):
            async with progress_watchdog(.02):
                await asyncio.sleep(.06)
    asyncio.run(exercise())


class PacedStream(httpx.AsyncByteStream):
    def __init__(self, frames, delay=.015):
        self.frames, self.delay, self.closed = frames, delay, False
    async def __aiter__(self):
        for frame in self.frames:
            await asyncio.sleep(self.delay)
            yield frame
    async def aclose(self): self.closed = True


def frame(delta, finish=None):
    return ('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':finish}]})+'\n\n').encode()


def install(monkeypatch, stream):
    real = httpx.AsyncClient
    seen = []
    def response(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=stream)
    monkeypatch.setattr(provider.httpx, 'AsyncClient', lambda **kw: real(**{**kw,'transport':httpx.MockTransport(response)}))
    monkeypatch.setattr(provider, 'PROVIDER_REQUEST_SECONDS', .04)
    monkeypatch.setattr(provider, 'PROVIDER_STREAM_SECONDS', .04)
    return seen


def test_native_decision_streams_reasoning_and_function_arguments_beyond_old_deadline(monkeypatch):
    frames = [frame({'reasoning_content':'thinking'}), frame({'tool_calls':[{'index':0,'id':'call1','type':'function','function':{'name':'query_system_status','arguments':''}}]})]
    frames += [frame({'tool_calls':[{'index':0,'function':{'arguments':part}}]}) for part in ('{','"x"',':','1','}')]
    frames += [frame({}, 'tool_calls'), b'data: [DONE]\n\n']
    stream = PacedStream(frames)
    seen = install(monkeypatch, stream)
    response, message = asyncio.run(provider.decision_request('deepseek','test','dummy',[],[{'type':'function'}]))
    assert seen[0]['stream'] is True
    assert json.loads(message['tool_calls'][0]['function']['arguments']) == {'x':1}
    assert message['reasoning_content'] == 'thinking'
    assert stream.closed and response['choices'][0]['finish_reason'] == 'tool_calls'


def test_final_stream_with_real_deltas_outlives_fixed_duration(monkeypatch):
    stream = PacedStream([frame({'content':str(i)}) for i in range(8)]+[frame({},'stop'),b'data: [DONE]\n\n'])
    install(monkeypatch, stream)
    async def exercise(): return ''.join([s async for s in provider.stream_answer('deepseek','test','dummy',[])])
    assert asyncio.run(exercise()) == '01234567'
    assert stream.closed


@pytest.mark.parametrize('frames', [[b': heartbeat\n\n']*12, [frame({'role':'assistant'})]*12])
def test_keepalives_and_role_only_frames_do_not_renew_progress(monkeypatch, frames):
    stream = PacedStream(frames)
    install(monkeypatch, stream)
    with pytest.raises(HTTPException) as error:
        asyncio.run(provider.decision_request('deepseek','test','dummy',[],[]))
    assert error.value.detail == 'provider_timeout'
    assert stream.closed


def test_cancelled_stream_closes_transport(monkeypatch):
    stream = PacedStream([frame({'content':'x'})]*100)
    install(monkeypatch, stream)
    async def exercise():
        task=asyncio.create_task(provider.decision_request('deepseek','test','dummy',[],[]))
        await asyncio.sleep(.025)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert stream.closed
