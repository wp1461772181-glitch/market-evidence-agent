from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit
import ipaddress, socket
import json

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

router = APIRouter(prefix="/agent-chat", tags=["agent-chat"])
PROVIDERS = {
    "deepseek": "https://api.deepseek.com/chat/completions",
    "kimi": "https://api.moonshot.cn/v1/chat/completions",
    "minimax": "https://api.minimax.cn/v1/chat/completions",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "doubao": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
    "hunyuan": "https://api.hunyuan.cloud.tencent.com/v1/chat/completions",
    "baidu": "https://qianfan.baidubce.com/v2/chat/completions",
    "siliconflow": "https://api.siliconflow.cn/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
PROVIDER_NAMES = ("deepseek", "kimi", "minimax", "qwen", "zhipu", "doubao", "hunyuan", "baidu", "siliconflow", "openrouter", "custom")
BASE_URLS = {"deepseek":"https://api.deepseek.com", "kimi":"https://api.moonshot.cn/v1", "minimax":"https://api.minimax.cn/v1", "qwen":"https://dashscope.aliyuncs.com/compatible-mode/v1", "zhipu":"https://open.bigmodel.cn/api/paas/v4", "doubao":"https://ark.cn-beijing.volces.com/api/v3", "hunyuan":"https://api.hunyuan.cloud.tencent.com/v1", "baidu":"https://qianfan.baidubce.com/v2", "siliconflow":"https://api.siliconflow.cn/v1", "openrouter":"https://openrouter.ai/api/v1"}
REQUEST_SLOTS = asyncio.Semaphore(8)
SYSTEM_PROMPT = "You are a general-purpose conversational assistant. You cannot access this website's project materials, databases, forecasts, or tools. Do not claim that you have read or cited them."

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message is empty")
        return value

class ChatRequest(BaseModel):
    provider: Literal["deepseek", "kimi", "minimax", "qwen", "zhipu", "doubao", "hunyuan", "baidu", "siliconflow", "openrouter", "custom"]
    base_url: str | None = Field(default=None, max_length=2048)
    model: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")
    api_key: SecretStr
    messages: list[ChatMessage] = Field(max_length=20)

    @model_validator(mode="after")
    def custom_has_endpoint(self):
        if self.provider == "custom" and not self.base_url: raise ValueError("custom endpoint required")
        return self

    @field_validator("base_url")
    @classmethod
    def safe_base_url(cls, value):
        if value is None: return value
        if any(ord(c)<32 or c=="\\" for c in value): raise ValueError("invalid endpoint")
        p=urlsplit(value.strip())
        if p.scheme!="https" or not p.hostname or p.username or p.password or p.query or p.fragment: raise ValueError("invalid endpoint")
        try: port=p.port
        except ValueError as exc: raise ValueError("invalid endpoint") from exc
        if "%" in p.netloc or (port is not None and not 1 <= port <= 65535): raise ValueError("invalid endpoint")
        path=p.path.rstrip("/")
        if path.endswith("/chat/completions"): path=path[:-len("/chat/completions")].rstrip("/")
        return urlunsplit(("https",p.netloc,path,"",""))

    @field_validator("api_key")
    @classmethod
    def key_length(cls, value: SecretStr) -> SecretStr:
        if not 8 <= len(value.get_secret_value()) <= 2048:
            raise ValueError("invalid key length")
        return value

    @field_validator("messages")
    @classmethod
    def total_size(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if sum(len(item.content) for item in value) > 20000:
            raise ValueError("conversation is too long")
        return value

class ChatResponse(BaseModel):
    content: str
    model: str

class PinnedPublicTransport(httpx.AsyncBaseTransport):
    """Resolve every endpoint request, reject any non-public answer, then connect to a pinned IP."""
    def __init__(self, inner=None): self.inner=inner or httpx.AsyncHTTPTransport(trust_env=False)
    async def handle_async_request(self, request):
        host=request.url.host; port=request.url.port or 443
        if request.url.scheme!="https" or request.url.username or request.url.password or request.url.query or request.url.fragment:
            raise httpx.ConnectError("unsafe_endpoint")
        try:
            answers=await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM),timeout=5)
            ips={ipaddress.ip_address(row[4][0]) for row in answers}
            if not ips or any(not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or getattr(ip,"ipv4_mapped",None) is not None for ip in ips): raise ValueError("non-public")
            ip=sorted(ips,key=lambda v:(v.version,int(v)))[0]
        except Exception as exc: raise httpx.ConnectError("unsafe_endpoint") from exc
        original_host=host
        authority=f"[{original_host}]" if ":" in original_host else original_host
        if port!=443: authority+=f":{port}"
        request.headers["host"]=authority
        request.url=request.url.copy_with(host=str(ip),port=port)
        request.extensions["sni_hostname"]=original_host.encode()
        return await self.inner.handle_async_request(request)
    async def aclose(self): await self.inner.aclose()

async def _call(payload: ChatRequest, messages: list[dict[str, str]], max_tokens: int) -> ChatResponse:
    headers = {"Authorization": f"Bearer {payload.api_key.get_secret_value()}", "Content-Type": "application/json"}
    if payload.provider == "openrouter":
        headers["X-Title"] = "Market Evidence Agent"
    body = {"model": payload.model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages], "max_tokens": max_tokens}
    if payload.provider == "minimax": body["reasoning_split"] = True
    try:
        target = (payload.base_url or PROVIDERS.get(payload.provider, BASE_URLS.get(payload.provider, ""))).rstrip("/")
        if not target.endswith("/chat/completions"): target += "/chat/completions"
        if payload.base_url is not None and not target.startswith("https://"): raise HTTPException(422,"invalid_agent_chat_request")
        custom=payload.base_url is not None
        client_options={"timeout":httpx.Timeout(25.0),"follow_redirects":False,"trust_env":False}
        if custom: client_options["transport"]=PinnedPublicTransport()
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(**client_options) as client:
                if custom:
                    # Bound decompressed response bytes before JSON parsing. Keep
                    # this branch custom-only so legacy fixed-provider mocks retain
                    # their established post() behavior.
                    async with client.stream("POST", target, headers=headers, json=body) as response:
                        status_code = response.status_code
                        if status_code == 401: raise HTTPException(502, "provider_invalid_key")
                        if status_code == 403: raise HTTPException(502, "provider_model_access_denied")
                        if status_code == 429: raise HTTPException(429, "provider_rate_or_quota_error")
                        if 300 <= status_code < 400: raise HTTPException(502, "provider_redirect_rejected")
                        if status_code in (400, 404, 422): raise HTTPException(502, "provider_model_unavailable_or_invalid_request")
                        if status_code >= 400: raise HTTPException(502, "provider_error")
                        raw_chunks = []
                        total = 0
                        async for chunk in response.aiter_bytes(chunk_size=16_384):
                            total += len(chunk)
                            if total > 1_000_000:
                                raise HTTPException(502, "provider_response_too_large")
                            raw_chunks.append(chunk)
                        raw_response = b"".join(raw_chunks)
                else:
                    response = await client.post(target, headers=headers, json=body)
                    status_code = response.status_code
    except (httpx.TimeoutException,TimeoutError) as exc:
        raise HTTPException(504, "provider_timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "provider_network_error") from exc
    if status_code == 401:
        raise HTTPException(502, "provider_invalid_key")
    if status_code == 403:
        raise HTTPException(502, "provider_model_access_denied")
    if status_code == 429:
        raise HTTPException(429, "provider_rate_or_quota_error")
    if 300 <= status_code < 400:
        raise HTTPException(502, "provider_redirect_rejected")
    if status_code in (400, 404, 422):
        raise HTTPException(502, "provider_model_unavailable_or_invalid_request")
    if status_code >= 400:
        raise HTTPException(502, "provider_error")
    try:
        data = json.loads(raw_response) if custom else response.json()
        message=data["choices"][0]["message"]
        content = message.get("content")
        model = data.get("model")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, "provider_incompatible_response") from exc
    if (not isinstance(content, str) or not content.strip()) and max_tokens==64 and isinstance(message.get("reasoning_content"),str) and message["reasoning_content"].strip():
        return ChatResponse(content="Connection verified.",model=model if isinstance(model,str) else payload.model)
    if not isinstance(content, str) or not content.strip() or len(content) > 4000:
        raise HTTPException(502, "provider_empty_or_invalid_answer")
    return ChatResponse(content=content, model=model if isinstance(model, str) else payload.model)

async def _limited_call(payload: ChatRequest, messages: list[dict[str, str]], max_tokens: int) -> ChatResponse:
    try:
        await asyncio.wait_for(REQUEST_SLOTS.acquire(), timeout=2.0)
    except TimeoutError as exc:
        raise HTTPException(503, "chat_capacity_reached") from exc
    try:
        return await _call(payload, messages, max_tokens)
    finally:
        REQUEST_SLOTS.release()

@router.post("/hello", response_model=ChatResponse)
async def hello(payload: ChatRequest):
    return await _limited_call(payload, [{"role": "user", "content": "hello. Reply briefly."}], 64)

@router.post("/messages", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    if not payload.messages:
        raise HTTPException(422, "messages_required")
    return await _limited_call(payload, [item.model_dump() for item in payload.messages], 800)


async def safe_validation_response(request: Request, _exc) -> JSONResponse:
    """Never echo request input: API key and user chat text are sensitive."""
    return JSONResponse(status_code=422, content={"detail": "invalid_agent_chat_request"})
