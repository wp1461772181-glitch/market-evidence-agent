from __future__ import annotations
import asyncio
import json
import httpx
from fastapi import HTTPException
from .agent_chat_api import PROVIDERS, BASE_URLS, PinnedPublicTransport

def endpoint(provider, base_url=None):
    if base_url is None: return PROVIDERS[provider]
    base=base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else base+"/chat/completions"
async def decision_request(provider, model, api_key, messages, schemas, base_url=None):
    body={"model":model,"messages":messages,"tools":schemas,"tool_choice":"auto","max_tokens":1200,"stream":False}
    if provider=="minimax":body["reasoning_split"]=True
    return await _post(provider,api_key,body,base_url)
async def _post(provider,key,body,base_url=None):
    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"}
    if provider=="openrouter":headers["X-Title"]="Market Evidence Agent"
    try:
        async with asyncio.timeout(25):
          options={"timeout":httpx.Timeout(25),"follow_redirects":False,"trust_env":False}
          if base_url: options["transport"]=PinnedPublicTransport()
          async with httpx.AsyncClient(**options) as client:
            async with client.stream("POST",endpoint(provider,base_url),headers=headers,json=body) as r:
                if 300 <= r.status_code < 400: raise HTTPException(502,"provider_redirect_rejected")
                if r.status_code>=400:
                    codes={400:"provider_model_unavailable_or_invalid_request",401:"provider_invalid_key",403:"provider_model_access_denied",404:"provider_model_unavailable_or_invalid_request",422:"provider_model_unavailable_or_invalid_request",429:"provider_rate_or_quota_error"}
                    raise HTTPException(429 if r.status_code==429 else 502,codes.get(r.status_code,"provider_error"))
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
    body={"model":model,"messages":messages,"max_tokens":1800,"stream":True,"tools":[],"tool_choice":"none"}
    if provider=="minimax":body["reasoning_split"]=True
    headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}
    if provider=="openrouter":headers["X-Title"]="Market Evidence Agent"
    try:
        async with asyncio.timeout(25):
            options={"timeout":httpx.Timeout(25),"follow_redirects":False,"trust_env":False}
            if base_url: options["transport"]=PinnedPublicTransport()
            async with httpx.AsyncClient(**options) as client:
                async with client.stream("POST",endpoint(provider,base_url),headers=headers,json=body) as r:
                    if 300 <= r.status_code < 400: raise HTTPException(502,"provider_redirect_rejected")
                    if r.status_code>=400:
                        codes={400:"provider_model_unavailable_or_invalid_request",401:"provider_invalid_key",403:"provider_model_access_denied",404:"provider_model_unavailable_or_invalid_request",422:"provider_model_unavailable_or_invalid_request",429:"provider_rate_or_quota_error"}
                        raise HTTPException(429 if r.status_code==429 else 502,codes.get(r.status_code,"provider_error"))
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
                            if choices == [] and "usage" in event: continue
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
