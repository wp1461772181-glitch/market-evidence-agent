from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit
import ipaddress, socket
import os
import json
import re
from datetime import datetime

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
        return _validated_base_url(value)

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

def _ch34_simulated_hello(request: Request, payload: ChatRequest) -> ChatResponse | None:
    """Return a clearly labelled, non-provider hello only in the isolated demo."""
    if os.getenv('CH34_ENABLE_SIMULATED_PROVIDER','false').lower() not in {'1','true','yes'}:
        return None
    if (payload.provider,payload.base_url,payload.model,payload.api_key.get_secret_value()) != (
        'custom','https://provider.invalid/v1','ch34-simulated','fake-test-key-not-a-credential'):
        return None
    peer=request.client.host if request.client else ''
    try:
        if not ipaddress.ip_address(peer.split('%',1)[0]).is_loopback:return None
    except ValueError:return None
    authority=urlsplit('//'+request.headers.get('host',''))
    host=(authority.hostname or '').lower()
    if host not in {'127.0.0.1','localhost','::1'} or any(name.lower() in {'forwarded','x-real-ip'} or name.lower().startswith('x-forwarded-') for name in request.headers):
        return None
    try:
        from sqlalchemy.engine import make_url
        database=make_url(os.getenv('DATABASE_URL',''))
    except Exception:return None
    if database.get_backend_name()!='postgresql' or not database.database or not __import__('re').fullmatch(r'test_[a-z0-9_]+',database.database) or database.host not in {'127.0.0.1','localhost','::1'} or database.port!=55432:
        return None
    return ChatResponse(content='SIMULATED local demo provider: hello check passed. No external model was called.',model=payload.model)

def _canonical_model_url(value):
    if value is None: return value
    if any(ord(c)<32 or c=="\\" for c in value): raise ValueError("invalid endpoint")
    p=urlsplit(value.strip())
    if p.scheme not in {"http","https"} or not p.hostname or p.username or p.password or p.query or p.fragment: raise ValueError("invalid endpoint")
    try: port=p.port
    except ValueError as exc: raise ValueError("invalid endpoint") from exc
    if "%" in p.netloc or (port is not None and not 1 <= port <= 65535): raise ValueError("invalid endpoint")
    path=p.path.rstrip("/")
    if path.endswith("/chat/completions"): path=path[:-len("/chat/completions")].rstrip("/")
    if '%' in path or '..' in path or '//' in path:raise ValueError('invalid endpoint')
    return urlunsplit((p.scheme,p.netloc.lower(),path,"",""))

def local_model_endpoints():
    try:
        values=json.loads(os.getenv('AGENT_LOCAL_MODEL_BASE_URLS','[]'))
        if not isinstance(values,list) or len(values)>16:return set()
        return {_canonical_model_url(v) for v in values if isinstance(v,str) and len(v)<=2048}
    except (ValueError,TypeError):return set()

def _validated_base_url(value):
    canonical=_canonical_model_url(value)
    if canonical and not canonical.startswith('https://') and canonical not in local_model_endpoints():raise ValueError('invalid endpoint')
    return canonical

def model_transport(base_url,**kwargs):
    base=_validated_base_url(base_url)
    return LocalModelTransport(base) if base in local_model_endpoints() else PinnedPublicTransport(**kwargs)

class LocalModelTransport(httpx.AsyncBaseTransport):
    """Only exact operator endpoints, only model routes, pinned private addresses."""
    def __init__(self,base_url,inner=None):
        self.base=_canonical_model_url(base_url)
        if self.base not in local_model_endpoints():raise ValueError('local_model_not_configured')
        self.inner=inner or httpx.AsyncHTTPTransport(trust_env=False)
    async def handle_async_request(self,request):
        if self.base not in local_model_endpoints() or str(request.url) not in {self.base+'/models',self.base+'/chat/completions'}:
            raise httpx.ConnectError('unsafe_endpoint')
        if (request.method,request.url.path.rsplit('/',1)[-1]) not in {('GET','models'),('POST','completions')}:
            raise httpx.ConnectError('unsafe_endpoint')
        host=request.url.host;port=request.url.port or (443 if request.url.scheme=='https' else 80)
        try:
            answers=await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM),5)
            ips={ipaddress.ip_address(row[4][0]) for row in answers}
            networks=[ipaddress.ip_network(n) for n in ('127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','::1/128','fc00::/7')]
            if not ips or any(not any(ip in net for net in networks if ip.version==net.version) for ip in ips):raise ValueError()
            ip=sorted(ips,key=lambda v:(v.version,int(v)))[0]
        except Exception as exc:raise httpx.ConnectError('unsafe_endpoint') from exc
        request.headers['host']=urlsplit(self.base).netloc
        request.url=request.url.copy_with(host=str(ip),port=port)
        request.extensions['sni_hostname']=host
        return await self.inner.handle_async_request(request)
    async def aclose(self):await self.inner.aclose()

class ModelDiscoveryRequest(BaseModel):
    provider: Literal["deepseek", "kimi", "minimax", "qwen", "zhipu", "doubao", "hunyuan", "baidu", "siliconflow", "openrouter", "custom"]
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_discovery(self):
        if self.provider == "custom" and not self.base_url: raise ValueError("custom endpoint required")
        if self.provider != "openrouter" and (self.api_key is None or not 8 <= len(self.api_key.get_secret_value().strip()) <= 2048): raise ValueError("invalid key length")
        if self.provider == "openrouter" and self.api_key is not None and not self.api_key.get_secret_value().strip(): self.api_key = None
        if self.api_key is not None and not 8 <= len(self.api_key.get_secret_value().strip()) <= 2048: raise ValueError("invalid key length")
        return self

    @field_validator("base_url")
    @classmethod
    def safe_discovery_base_url(cls, value): return _validated_base_url(value)

class PinnedPublicTransport(httpx.AsyncBaseTransport):
    """Resolve every endpoint request, reject any non-public answer, then connect to a pinned IP."""
    def __init__(self, inner=None, *, allow_internal_query=False): self.inner=inner or httpx.AsyncHTTPTransport(trust_env=False); self.allow_internal_query=allow_internal_query
    async def handle_async_request(self, request):
        host=request.url.host; port=request.url.port or 443
        if request.url.scheme!="https" or request.url.username or request.url.password or (request.url.query and not self.allow_internal_query) or request.url.fragment:
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
        # HTTPX's documented extension contract is a hostname string. httpcore
        # passes this value directly to start_tls(server_hostname=...).
        request.extensions["sni_hostname"]=original_host
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
        if payload.base_url is not None:_validated_base_url(payload.base_url)
        custom=payload.base_url is not None
        client_options={"timeout":httpx.Timeout(25.0),"follow_redirects":False,"trust_env":False}
        if custom: client_options["transport"]=model_transport(payload.base_url)
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
async def hello(payload: ChatRequest, request: Request):
    simulated=_ch34_simulated_hello(request,payload)
    if simulated:return simulated
    return await _limited_call(payload, [{"role": "user", "content": "hello. Reply briefly."}], 64)

@router.post("/messages", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    if not payload.messages:
        raise HTTPException(422, "messages_required")
    return await _limited_call(payload, [item.model_dump() for item in payload.messages], 800)

MODEL_LIST_LIMIT = 8_000_000
MODEL_DISCOVERY_TIMEOUT = 20.0
MODEL_DISCOVERY_UNSUPPORTED_PROVIDERS = {"minimax"}
_NON_CHAT_WORDS = ("embed", "embedding", "rerank", "re-rank", "bge-", "image", "audio", "video", "speech-to-text", "text-to-speech", "transcription")
_GENERATIVE_MODALITIES = {"image", "audio", "video"}

def _model_is_conversational(row: dict) -> bool:
    kind = str(row.get("type", row.get("object", ""))).lower()
    input_modalities = row.get("input_modalities")
    output_modalities = row.get("output_modalities")
    architecture = row.get("architecture")
    if isinstance(architecture, dict):
        input_modalities = architecture.get("input_modalities", input_modalities)
        output_modalities = architecture.get("output_modalities", output_modalities)
    if kind and any(word in kind for word in ("embedding", "rerank", "image", "audio", "video")):
        return False
    if isinstance(output_modalities, list):
        normalized_output = {str(value).lower() for value in output_modalities}
        return "text" in normalized_output and not normalized_output.intersection(_GENERATIVE_MODALITIES)
    if isinstance(input_modalities, list) and input_modalities and not any(str(value).lower() == "text" for value in input_modalities): return False
    identity = " ".join(str(row.get(key, "")) for key in ("id", "name", "type")).lower()
    return not any(word in identity for word in _NON_CHAT_WORDS)

def _normalize_models(payload: object) -> tuple[list[dict], str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("incompatible")
    items = []
    seen = set()
    has_timestamp = False
    valid_record_count = 0
    for position, row in enumerate(payload["data"]):
        if not isinstance(row, dict): continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 160 or not re.fullmatch(r"[A-Za-z0-9._:/-]+", identifier) or identifier in seen: continue
        valid_record_count += 1
        seen.add(identifier)
        if not _model_is_conversational(row): continue
        label = row.get("name")
        item = {"id": identifier, "name": label.strip()[:160] if isinstance(label, str) and label.strip() else identifier, "_position": position}
        timestamp = row.get("created", row.get("release_date", row.get("published_time")))
        if isinstance(timestamp, str):
            try: timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
            except ValueError: timestamp = None
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and timestamp > 0:
            item["_timestamp"] = timestamp
            has_timestamp = True
        items.append(item)
    if has_timestamp:
        items.sort(key=lambda item: (-item.get("_timestamp", 0), item["_position"]))
        ordering = "provider_timestamp_desc_with_undated" if any("_timestamp" not in item for item in items) else "provider_timestamp_desc"
    else:
        ordering = "upstream_order_unknown_recency"
    if payload["data"] and not valid_record_count: raise ValueError("incompatible")
    result = [{key: value for key, value in row.items() if not key.startswith("_")} for row in items[:5]]
    return result, ordering

@router.post("/models")
async def discover_models(payload: ModelDiscoveryRequest):
    if payload.base_url is not None:
        base = payload.base_url
    else:
        base = BASE_URLS.get(payload.provider, "")
    if not base:
        return {"status": "unsupported", "models": [], "ordering": "unknown"}
    if payload.provider in MODEL_DISCOVERY_UNSUPPORTED_PROVIDERS and base.rstrip("/") == BASE_URLS[payload.provider]:
        return {"status": "unsupported", "models": [], "ordering": "unknown"}
    siliconflow_query = payload.provider == "siliconflow" and base.rstrip("/") == BASE_URLS["siliconflow"]
    target = base.rstrip("/") + "/models" + ("?sub_type=chat" if siliconflow_query else "")
    headers = {"Accept": "application/json"}
    if payload.api_key is not None:
        headers["Authorization"] = f"Bearer {payload.api_key.get_secret_value()}"
    if payload.provider == "openrouter": headers["X-Title"] = "Market Evidence Agent"
    try:
        await asyncio.wait_for(REQUEST_SLOTS.acquire(), timeout=2.0)
    except TimeoutError as exc:
        raise HTTPException(503, "model_discovery_capacity_reached") from exc
    try:
        try:
            async with asyncio.timeout(MODEL_DISCOVERY_TIMEOUT):
                async with httpx.AsyncClient(timeout=httpx.Timeout(MODEL_DISCOVERY_TIMEOUT), follow_redirects=False, trust_env=False, transport=model_transport(base,allow_internal_query=siliconflow_query)) as client:
                    async with client.stream("GET", target, headers=headers) as response:
                        code = response.status_code
                        if 300 <= code < 400: raise HTTPException(502, "model_discovery_redirect_rejected")
                        if code in (404, 405): return {"status": "unsupported", "models": [], "ordering": "unknown"}
                        if code == 401: raise HTTPException(502, "model_discovery_auth_required")
                        if code == 403: raise HTTPException(502, "model_discovery_access_denied")
                        if code == 429: raise HTTPException(429, "model_discovery_rate_limited")
                        if code >= 400: raise HTTPException(502, "model_discovery_unavailable")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16384):
                            raw.extend(chunk)
                            if len(raw) > MODEL_LIST_LIMIT: raise HTTPException(502, "model_discovery_response_too_large")
            try: upstream = json.loads(raw)
            except (ValueError, TypeError) as exc: raise HTTPException(502, "model_discovery_incompatible_response") from exc
            try: models, ordering = _normalize_models(upstream)
            except ValueError as exc: raise HTTPException(502, "model_discovery_incompatible_response") from exc
            return {"status": "available" if models else "empty", "models": models, "ordering": ordering}
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise HTTPException(504, "model_discovery_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "model_discovery_network_error") from exc
    finally:
        REQUEST_SLOTS.release()


async def safe_validation_response(request: Request, _exc) -> JSONResponse:
    """Never echo request input: API key and user chat text are sensitive."""
    return JSONResponse(status_code=422, content={"detail": "invalid_agent_chat_request"})
