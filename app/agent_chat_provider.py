from __future__ import annotations
import asyncio
import json
import httpx
import os
from contextlib import AsyncExitStack,asynccontextmanager
from contextvars import ContextVar
from fastapi import HTTPException
from .agent_chat_api import PROVIDERS, BASE_URLS, PinnedPublicTransport, model_transport
from .agent_progress import progress_watchdog, mark_progress

_DECISION_SESSION=ContextVar('agent_decision_session',default=None)

@asynccontextmanager
async def decision_session(base_url=None):
    # One lazy pool per turn. Headers/keys remain on individual requests, and
    # ContextVar prevents concurrent conversations from sharing session state.
    async with AsyncExitStack() as stack:
        token=_DECISION_SESSION.set({'base_url':base_url,'stack':stack,'client':None})
        try:yield
        finally:_DECISION_SESSION.reset(token)

@asynccontextmanager
async def _decision_client(base_url):
    options={'timeout':httpx.Timeout(PROVIDER_REQUEST_SECONDS),'follow_redirects':False,'trust_env':False}
    if base_url:options['transport']=model_transport(base_url)
    session=_DECISION_SESSION.get()
    if session is not None and session['base_url']==base_url:
        if session['client'] is None:
            session['client']=await session['stack'].enter_async_context(httpx.AsyncClient(**options))
        yield session['client']
    else:
        async with httpx.AsyncClient(**options) as client:yield client

def _bounded_seconds(name: str, default: float, low: float, high: float) -> float:
    try:
        return min(high, max(low, float(os.getenv(name, default))))
    except ValueError:
        return default


# One request to the model service (a stage of the workflow, a tool decision): slow "thinking" models
# need more than the old 25 s, and a request that runs out is retried once inside the stage limit.
PROVIDER_REQUEST_SECONDS = _bounded_seconds("AGENT_PROVIDER_TIMEOUT_SECONDS", 35.0, 10.0, 90.0)
# Maximum inactivity of a streamed answer, renewed only by semantic model deltas.
PROVIDER_STREAM_SECONDS = _bounded_seconds("AGENT_PROVIDER_STREAM_SECONDS", 90.0, 30.0, 180.0)

# A reasoning model thinks before it answers: hundreds to thousands of hidden tokens, a minute or two on a busy
# route.  One turn of this workflow makes several calls one after the other, and a stage of it (a JSON
# classification, a plan) does not need deep thinking, so left at the default such a model makes a turn take
# minutes and runs into the request timeout.  OpenRouter takes one unified parameter for the effort (a model
# without reasoning ignores it).  "default" sends nothing and leaves it to the model.
REASONING_EFFORT = os.getenv("AGENT_REASONING_EFFORT", "low").strip().lower()
_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


def reasoning_options(provider: str) -> dict:
    if REASONING_EFFORT not in _REASONING_EFFORTS:
        return {}
    if provider == "deepseek":
        effort={"minimal":"low","medium":"high"}.get(REASONING_EFFORT,REASONING_EFFORT)
        return {"thinking":{"type":"enabled"},"reasoning_effort":effort}
    if provider == "openrouter":
        return {"reasoning": {"effort": REASONING_EFFORT}}
    return {}


class ProviderFailure(HTTPException):
    def __init__(self, code: str, *, stage: str, provider_status: int | None = None,
                 request_id: str | None = None, retryable: bool = False):
        super().__init__(429 if provider_status == 429 else 502, code)
        self.stage = stage
        self.provider_status = provider_status
        self.request_id = request_id
        self.retryable = retryable


async def _provider_error(response, stage: str) -> ProviderFailure:
    status = response.status_code
    request_id = next((response.headers.get(name) for name in ("x-request-id", "x-ds-request-id", "cf-ray")
                       if response.headers.get(name)), None)
    if request_id and not __import__("re").fullmatch(r"[A-Za-z0-9_.:-]{1,100}", request_id):
        request_id = None
    code = {400: "provider_model_unavailable_or_invalid_request", 401: "provider_invalid_key",
            403: "provider_model_access_denied", 404: "provider_model_unavailable_or_invalid_request",
            422: "provider_model_unavailable_or_invalid_request", 429: "provider_rate_or_quota_error"}.get(status, "provider_error")
    if status == 400:
        raw=b''
        async for chunk in response.aiter_bytes():
            raw+=chunk[:4096-len(raw)]
            if len(raw)>=4096:break
        # Only classify a known protocol phrase; never return arbitrary provider
        # text because it can echo private prompts or headers.
        if b"reasoning_content" in raw or b"tool_call_id" in raw:
            code = "provider_protocol_incompatible"
    return ProviderFailure(code, stage=stage, provider_status=status,
                           request_id=request_id, retryable=status in {429, 500, 502, 503, 504})

def endpoint(provider, base_url=None):
    if base_url is None: return PROVIDERS[provider]
    base=base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else base+"/chat/completions"
async def decision_request(provider, model, api_key, messages, schemas, base_url=None):
    # Thinking output counts against max_tokens; a 1200-token JSON classifier
    # can exhaust its budget before producing any visible JSON.
    body={"model":model,"messages":messages,"max_tokens":8192 if not schemas else 4096,"stream":True}
    if schemas: body.update(tools=schemas,tool_choice="auto")
    if provider=="minimax":body["reasoning_split"]=True
    body.update(reasoning_options(provider))
    return await _post(provider,api_key,body,base_url)
def _semantic_delta(delta):
    return (any(isinstance(delta.get(k),str) and delta[k] for k in ('content','reasoning_content'))
            or bool(delta.get('reasoning_details'))
            or any(isinstance(c,dict) and isinstance(c.get('function'),dict)
                   and any(c['function'].get(k) for k in ('name','arguments'))
                   for c in (delta.get('tool_calls') or [])))


async def _events(response):
    """Bounded SSE parser. Transport keepalives never renew the progress clock."""
    buffer=b''; total=0; done=False
    async for chunk in response.aiter_bytes():
        total+=len(chunk)
        if total>1_000_000:raise HTTPException(502,'provider_response_too_large')
        buffer+=chunk
        if len(buffer)>65536 and b'\n' not in buffer:raise HTTPException(502,'provider_response_too_large')
        while b'\n' in buffer:
            line,buffer=buffer.split(b'\n',1)
            if len(line)>65536:raise HTTPException(502,'provider_response_too_large')
            try:line=line.rstrip(b'\r').decode('utf-8')
            except UnicodeDecodeError as exc:raise HTTPException(502,'provider_incompatible_response') from exc
            if not line or not line.startswith('data:'):continue
            raw=line[5:].strip()
            if raw=='[DONE]':done=True;break
            try:
                event=json.loads(raw)
                if not isinstance(event,dict):raise ValueError()
                if 'error' in event:raise HTTPException(502,'provider_error')
                choices=event.get('choices')
                if choices==[] and isinstance(event.get('usage'),dict):
                    yield event;continue
                choice=choices[0];delta=choice['delta']
                if not isinstance(delta,dict):raise ValueError()
                if _semantic_delta(delta):mark_progress()
            except HTTPException:raise
            except (ValueError,KeyError,IndexError,TypeError,AttributeError) as exc:
                raise HTTPException(502,'provider_incompatible_response') from exc
            yield event
        if done:break
    if not done or buffer.strip():raise HTTPException(502,'provider_incomplete_stream')


def _merge_delta(message, calls, delta):
    for key in ('content','reasoning_content'):
        part=delta.get(key)
        if part is not None:
            if not isinstance(part,str):raise HTTPException(502,'provider_incompatible_response')
            message[key]=message.get(key,'')+part
    details=delta.get('reasoning_details')
    if details is not None:
        if not isinstance(details,list):raise HTTPException(502,'provider_incompatible_response')
        message.setdefault('reasoning_details',[]).extend(details)
    if len(json.dumps({k:v for k,v in message.items() if k.startswith('reasoning_')}).encode())>16000:
        raise HTTPException(502,'provider_protocol_too_large')
    parts=delta.get('tool_calls',[])
    if not isinstance(parts,list):raise HTTPException(502,'provider_incompatible_response')
    for part in parts:
        if not isinstance(part,dict) or type(part.get('index')) is not int or not 0<=part['index']<12:
            raise HTTPException(502,'provider_incompatible_response')
        target=calls.setdefault(part['index'],{'id':'','type':'function','function':{'name':'','arguments':''}})
        if part.get('type','function')!='function':raise HTTPException(502,'provider_incompatible_response')
        fn=part.get('function',{})
        if not isinstance(fn,dict):raise HTTPException(502,'provider_incompatible_response')
        for container, key, value in ((target,'id',part.get('id')),
                (target['function'],'name',fn.get('name')),(target['function'],'arguments',fn.get('arguments'))):
            if value is not None:
                if not isinstance(value,str):raise HTTPException(502,'provider_incompatible_response')
                container[key]+=value


async def _post(provider,key,body,base_url=None):
    headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'}
    if provider=='openrouter':headers['X-Title']='Market Evidence Agent'
    try:
        async with progress_watchdog(PROVIDER_REQUEST_SECONDS):
          async with _decision_client(base_url) as client:
            async with client.stream('POST',endpoint(provider,base_url),headers=headers,json=body) as r:
                if 300<=r.status_code<400:raise HTTPException(502,'provider_redirect_rejected')
                if r.status_code>=400:raise await _provider_error(r,'tool_decision' if body.get('tools') else 'classification')
                if 'text/event-stream' in r.headers.get('content-type',''):
                    message={'role':'assistant','content':''};calls={};finish=None;usage={}
                    async for event in _events(r):
                        if isinstance(event.get('usage'),dict):usage=event['usage']
                        if not event.get('choices'):continue
                        choice=event['choices'][0]
                        if finish is not None and _semantic_delta(choice['delta']):raise HTTPException(502,'provider_incompatible_response')
                        _merge_delta(message,calls,choice['delta'])
                        reason=choice.get('finish_reason')
                        if reason is not None:
                            if reason not in {'stop','tool_calls'}:raise HTTPException(502,'provider_incomplete_response')
                            finish=reason
                    if finish is None:raise HTTPException(502,'provider_incomplete_stream')
                    if calls:
                        if finish!='tool_calls' or sorted(calls)!=list(range(len(calls))):raise HTTPException(502,'provider_incomplete_stream')
                        message['tool_calls']=[calls[i] for i in sorted(calls)]
                        if any(not c['id'] or not c['function']['name'] or not c['function']['arguments'] for c in message['tool_calls']):
                            raise HTTPException(502,'provider_incomplete_stream')
                    elif finish=='tool_calls':raise HTTPException(502,'provider_incomplete_stream')
                    data={'choices':[{'message':message,'finish_reason':finish}],'usage':usage}
                else:
                    # Some compatible endpoints ignore stream=True. A complete JSON
                    # response is supported but partial bytes are not proof of progress.
                    chunks=[];total=0
                    async for chunk in r.aiter_bytes():
                        total+=len(chunk)
                        if total>1_000_000:raise HTTPException(502,'provider_response_too_large')
                        chunks.append(chunk)
                    try:
                        data=json.loads(b''.join(chunks));choice=data['choices'][0];message=choice['message']
                    except (ValueError,KeyError,IndexError,TypeError) as exc:raise HTTPException(502,'provider_incompatible_response') from exc
                    if choice.get('finish_reason') in {'length','content_filter'}:raise HTTPException(502,'provider_incomplete_response')
                    if not isinstance(message,dict):raise HTTPException(502,'provider_incompatible_response')
                mark_progress()
                return data,message
    except HTTPException:raise
    except (httpx.TimeoutException,TimeoutError) as exc:raise HTTPException(504,'provider_timeout') from exc
    except httpx.HTTPError as exc:raise HTTPException(502,'provider_network_error') from exc

async def stream_answer(provider,model,api_key,messages,reasoning_collector=None,base_url=None):
    body={'model':model,'messages':messages,'max_tokens':8192,'stream':True}
    if provider=='minimax':body['reasoning_split']=True
    body.update(reasoning_options(provider))
    headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'}
    if provider=='openrouter':headers['X-Title']='Market Evidence Agent'
    try:
        async with progress_watchdog(PROVIDER_STREAM_SECONDS):
          async with _decision_client(base_url) as client:
            async with client.stream('POST',endpoint(provider,base_url),headers=headers,json=body) as r:
                if 300<=r.status_code<400:raise HTTPException(502,'provider_redirect_rejected')
                if r.status_code>=400:raise await _provider_error(r,'final_answer')
                message={};calls={};finished=False
                async for event in _events(r):
                    if isinstance(reasoning_collector,dict) and isinstance(event.get('usage'),dict):reasoning_collector['_usage']=event['usage']
                    if not event.get('choices'):continue
                    choice=event['choices'][0];delta=choice['delta'];reason=choice.get('finish_reason')
                    if finished and _semantic_delta(delta):raise HTTPException(502,'provider_incompatible_response')
                    if reason is not None:
                        if reason!='stop':raise HTTPException(502,'provider_incomplete_stream')
                        finished=True
                    if delta.get('tool_calls'):raise HTTPException(502,'provider_incomplete_stream')
                    _merge_delta(message,calls,delta)
                    if reasoning_collector is not None:
                        if isinstance(reasoning_collector,dict):
                            for key in ('reasoning_content','reasoning_details'):
                                if delta.get(key):reasoning_collector.setdefault(key,[]).extend([delta[key]] if key=='reasoning_content' else delta[key])
                        elif delta.get('reasoning_content'):reasoning_collector.append(delta['reasoning_content'])
                    if delta.get('content'):yield delta['content']
                if not finished:raise HTTPException(502,'provider_incomplete_stream')
    except HTTPException:raise
    except (httpx.TimeoutException,TimeoutError) as exc:raise HTTPException(504,'provider_timeout') from exc
    except httpx.HTTPError as exc:raise HTTPException(502,'provider_network_error') from exc
