from __future__ import annotations

from typing import Literal

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr, field_validator

router = APIRouter(prefix="/agent-chat", tags=["agent-chat"])
PROVIDERS = {
    "deepseek": "https://api.deepseek.com/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
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
    provider: Literal["deepseek", "openrouter"]
    model: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")
    api_key: SecretStr
    messages: list[ChatMessage] = Field(max_length=20)

    @field_validator("api_key")
    @classmethod
    def key_length(cls, value: SecretStr) -> SecretStr:
        if not 8 <= len(value.get_secret_value()) <= 512:
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

async def _call(payload: ChatRequest, messages: list[dict[str, str]], max_tokens: int) -> ChatResponse:
    headers = {"Authorization": f"Bearer {payload.api_key.get_secret_value()}", "Content-Type": "application/json"}
    if payload.provider == "openrouter":
        headers["X-Title"] = "Market Evidence Agent"
    body = {"model": payload.model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages], "max_tokens": max_tokens}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0), follow_redirects=False) as client:
            response = await client.post(PROVIDERS[payload.provider], headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "provider_timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "provider_network_error") from exc
    if response.status_code == 401:
        raise HTTPException(502, "provider_invalid_key")
    if response.status_code == 403:
        raise HTTPException(502, "provider_model_access_denied")
    if response.status_code == 429:
        raise HTTPException(429, "provider_rate_or_quota_error")
    if 300 <= response.status_code < 400:
        raise HTTPException(502, "provider_redirect_rejected")
    if response.status_code in (400, 404, 422):
        raise HTTPException(502, "provider_model_unavailable_or_invalid_request")
    if response.status_code >= 400:
        raise HTTPException(502, "provider_error")
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        model = data.get("model")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, "provider_incompatible_response") from exc
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
