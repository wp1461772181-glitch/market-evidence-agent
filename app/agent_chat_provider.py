from __future__ import annotations
import asyncio
import json
import httpx
import os
from fastapi import HTTPException
from .agent_chat_api import PROVIDERS, BASE_URLS, PinnedPublicTransport


def _bounded_seconds(name: str, default: float, low: float, high: float) -> float:
    try:
        return min(high, max(low, float(os.getenv(name, default))))
    except ValueError:
        return default


# One request to the model service (a stage of the workflow, a tool decision): slow "thinking" models
# need more than the old 25 s, and a request that runs out is retried once inside the stage limit.
PROVIDER_REQUEST_SECONDS = _bounded_seconds("AGENT_PROVIDER_TIMEOUT_SECONDS", 35.0, 10.0, 90.0)
# The whole streamed final answer; the turn's own deadline (graph.MAX_SECONDS) still bounds it.
PROVIDER_STREAM_SECONDS = _bounded_seconds("AGENT_PROVIDER_STREAM_SECONDS", 90.0, 30.0, 180.0)

# A reasoning model thinks before it answers: hundreds to thousands of hidden tokens, a minute or two on a busy
# route.  One turn of this workflow makes several calls one after the other, and a stage of it (a JSON
# classification, a plan) does not need deep thinking, so left at the default such a model makes a turn take
# minutes and runs into the request timeout.  OpenRouter takes one unified parameter for the effort (a model
# without reasoning ignores it).  "default" sends nothing and leaves it to the model.
REASONING_EFFORT = os.getenv("AGENT_REASONING_EFFORT", "low").strip().lower()
_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


def reasoning_options(provider: str) -> dict:
    if provider != "openrouter" or REASONING_EFFORT not in _REASONING_EFFORTS:
        return {}
    return {"reasoning": {"effort": REASONING_EFFORT}}


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
        raw = (await response.aread())[:4096]
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
    body={"model":model,"messages":messages,"max_tokens":8192 if not schemas else 4096,"stream":False}
    if schemas: body.update(tools=schemas,tool_choice="auto")
    if provider=="minimax":body["reasoning_split"]=True
    body.update(reasoning_options(provider))
    return await _post(provider,api_key,body,base_url)
async def _post(provider,key,body,base_url=None):
    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"}
    if provider=="openrouter":headers["X-Title"]="Market Evidence Agent"
    try:
        async with asyncio.timeout(PROVIDER_REQUEST_SECONDS):
          options={"timeout":httpx.Timeout(PROVIDER_REQUEST_SECONDS),"follow_redirects":False,"trust_env":False}
          if base_url: options["transport"]=PinnedPublicTransport()
          async with httpx.AsyncClient(**options) as client:
            async with client.stream("POST",endpoint(provider,base_url),headers=headers,json=body) as r:
                if 300 <= r.status_code < 400: raise HTTPException(502,"provider_redirect_rejected")
                if r.status_code>=400:
                    raise await _provider_error(r, "tool_decision" if body.get("tools") else "classification")
                chunks=[]; total=0
                async for chunk in r.aiter_bytes(chunk_size=16_384):
                    total+=len(chunk)
                    if total>1_000_000:raise HTTPException(502,"provider_response_too_large")
                    chunks.append(chunk)
                raw=b"".join(chunks)
    except HTTPException: raise
    except (httpx.TimeoutException,TimeoutError) as e: raise HTTPException(504,"provider_timeout") from e
    except httpx.HTTPError as e: raise HTTPException(502,"provider_network_error") from e
    try:
        data=json.loads(raw); choice=data["choices"][0]; msg=choice["message"]
    except (ValueError,KeyError,IndexError,TypeError) as e: raise HTTPException(502,"provider_incompatible_response") from e
    if not isinstance(msg,dict):raise HTTPException(502,"provider_incompatible_response")
    return data,msg

async def stream_answer(provider,model,api_key,messages,reasoning_collector=None,base_url=None):
    body={"model":model,"messages":messages,"max_tokens":8192,"stream":True}
    if provider=="minimax":body["reasoning_split"]=True
    body.update(reasoning_options(provider))
    headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}
    if provider=="openrouter":headers["X-Title"]="Market Evidence Agent"
    try:
        async with asyncio.timeout(PROVIDER_STREAM_SECONDS):
            options={"timeout":httpx.Timeout(PROVIDER_REQUEST_SECONDS),"follow_redirects":False,"trust_env":False}
            if base_url: options["transport"]=PinnedPublicTransport()
            async with httpx.AsyncClient(**options) as client:
                async with client.stream("POST",endpoint(provider,base_url),headers=headers,json=body) as r:
                    if 300 <= r.status_code < 400: raise HTTPException(502,"provider_redirect_rejected")
                    if r.status_code>=400:
                        raise await _provider_error(r, "final_answer")
                    completed=False; buffer=b""; finished=False; raw_total=0
                    async for chunk in r.aiter_bytes():
                      raw_total += len(chunk)
                      if raw_total > 1_000_000: raise HTTPException(502,"provider_response_too_large")
                      buffer += chunk
                      if len(buffer) > 65536 and b"\n" not in buffer: raise HTTPException(502,"provider_response_too_large")
                      while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n",1)
                        if len(line)>65536: raise HTTPException(502,"provider_response_too_large")
                        line=line.rstrip(b"\r")
                        try: decoded=line.decode("utf-8")
                        except UnicodeDecodeError as exc: raise HTTPException(502,"provider_incompatible_response") from exc
                        if not decoded or decoded.startswith(":") or not decoded.startswith("data:"): continue
                        raw=decoded[5:].strip()
                        if raw=="[DONE]":
                            if not finished: raise HTTPException(502,"provider_incomplete_stream")
                            completed=True;break
                        try:
                            event=json.loads(raw)
                            if not isinstance(event,dict): raise ValueError()
                            if "error" in event:raise HTTPException(502,"provider_error")
                            choices=event.get("choices")
                            if isinstance(reasoning_collector,dict) and isinstance(event.get("usage"),dict):
                                reasoning_collector["_usage"]=event["usage"]
                            if choices == [] and "usage" in event:
                                if isinstance(reasoning_collector,dict) and isinstance(event["usage"],dict):
                                    reasoning_collector["_usage"]=event["usage"]
                                continue
                            choice=choices[0]; delta_obj=choice["delta"]
                            if not isinstance(delta_obj,dict): raise ValueError()
                            reason=choice.get("finish_reason")
                            if reason in ("length","tool_calls","function_call"): raise HTTPException(502,"provider_incomplete_stream")
                            if reason is not None: finished=True
                            delta=delta_obj.get("content")
                            reasoning=delta_obj.get("reasoning_content")
                            if isinstance(reasoning,str) and reasoning and reasoning_collector is not None:
                                if isinstance(reasoning_collector,dict):
                                    bucket=reasoning_collector.setdefault("reasoning_content",[])
                                    if sum(len(part.encode("utf-8")) for part in bucket)+len(reasoning.encode("utf-8"))>16000: raise HTTPException(502,"provider_protocol_too_large")
                                    bucket.append(reasoning)
                                else: reasoning_collector.append(reasoning)
                            reasoning_details=delta_obj.get("reasoning_details")
                            if reasoning_details is not None:
                                if not isinstance(reasoning_details,list): raise ValueError()
                                if isinstance(reasoning_collector,dict):
                                    bucket=reasoning_collector.setdefault("reasoning_details",[])
                                    if len(json.dumps(bucket+reasoning_details,ensure_ascii=False).encode("utf-8"))>16000: raise HTTPException(502,"provider_protocol_too_large")
                                    bucket.extend(reasoning_details)
                            if delta_obj.get("tool_calls"): raise HTTPException(502,"provider_incomplete_stream")
                            if delta is not None and not isinstance(delta,str): raise ValueError()
                        except HTTPException:raise
                        except (ValueError,KeyError,IndexError,TypeError) as e:
                            raise HTTPException(502,"provider_incompatible_response") from e
                        if isinstance(delta,str) and delta:yield delta
                      if completed: break
                    if buffer.strip(): raise HTTPException(502,"provider_incomplete_stream")
                if not completed or not finished:raise HTTPException(502,"provider_incomplete_stream")
    except HTTPException: raise
    except (httpx.TimeoutException,TimeoutError) as e: raise HTTPException(504,"provider_timeout") from e
    except httpx.HTTPError as e: raise HTTPException(502,"provider_network_error") from e
