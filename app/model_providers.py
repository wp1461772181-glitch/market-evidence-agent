"""Provider construction for standalone material analysis jobs.

Material analysis runs outside the chat SSE loop, so it cannot reuse the chat
request's provider object. This module builds the same OpenAI-compatible
clients from a stored job configuration while keeping the provider catalog in
one place.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .event_provider import (
    EventProviderError,
    ProviderResult,
    _optional_string,
    _usage_dict,
)

PROVIDER_CATALOG: dict[str, dict[str, str]] = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com"},
    "kimi": {"label": "Kimi", "base_url": "https://api.moonshot.cn/v1"},
    "minimax": {"label": "MiniMax", "base_url": "https://api.minimax.cn/v1"},
    "qwen": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    "doubao": {"label": "豆包", "base_url": "https://ark.cn-beijing.volces.com/api/v3"},
    "hunyuan": {"label": "腾讯混元", "base_url": "https://api.hunyuan.cloud.tencent.com/v1"},
    "baidu": {"label": "百度千帆", "base_url": "https://qianfan.baidubce.com/v2"},
    "siliconflow": {"label": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    "custom": {"label": "自定义", "base_url": ""},
}

MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")


def provider_label(provider: str) -> str:
    return PROVIDER_CATALOG.get(provider, {}).get("label", provider)


def normalize_provider_config(
    *, provider: str, base_url: str | None = None, model: str
) -> dict[str, str]:
    """Validate one provider/model pair and return a canonical configuration."""
    if provider not in PROVIDER_CATALOG:
        raise ValueError("unsupported model provider")
    normalized_model = (model or "").strip()
    if not MODEL_PATTERN.fullmatch(normalized_model):
        raise ValueError("model must be 1-160 characters of letters, digits, dot, underscore, colon, slash, or hyphen")
    default_base = PROVIDER_CATALOG[provider]["base_url"]
    resolved_base = (base_url or default_base or "").strip().rstrip("/")
    if not resolved_base:
        raise ValueError("custom provider requires a base URL")
    parsed = urlsplit(resolved_base)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http or https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain credentials, a query string, or a fragment")
    if parsed.scheme == "http":
        from .agent_chat_api import local_model_endpoints

        if resolved_base not in local_model_endpoints():
            raise ValueError("http base_url is only allowed for endpoints listed in AGENT_LOCAL_MODEL_BASE_URLS")
    return {"provider": provider, "base_url": resolved_base, "model": normalized_model}


def create_model_provider(
    *, provider: str, base_url: str, model: str, api_key: str
) -> "OpenAICompatibleProvider":
    config = normalize_provider_config(provider=provider, base_url=base_url, model=model)
    return OpenAICompatibleProvider(
        provider=config["provider"],
        base_url=config["base_url"],
        model=config["model"],
        api_key=api_key,
    )


class OpenAICompatibleProvider:
    """One JSON extraction call against an OpenAI-compatible chat endpoint."""

    def __init__(self, *, provider: str, base_url: str, model: str, api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise EventProviderError("an API key is required for the selected model provider")
        if api_key.strip() != api_key or not 8 <= len(api_key) <= 2048:
            raise EventProviderError("the API key must be a trimmed string of 8 to 2048 characters")
        from openai import OpenAI

        self.provider = provider
        self.label = provider_label(provider)
        self.base_url = base_url
        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,
            max_retries=0,
        )

    def extract(self, *, system_prompt: str, document_payload: str, model: str) -> ProviderResult:
        request: dict[str, Any] = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": document_payload},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 8192,
        }
        if self.provider == "deepseek":
            request["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            response = self._client.chat.completions.create(**request)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                raise EventProviderError(f"{self.label} extraction request failed (HTTP {status_code})") from None
            raise EventProviderError(
                f"{self.label} extraction request failed ({type(exc).__name__})"
            ) from None

        choices = getattr(response, "choices", None) or []
        finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
        if finish_reason != "stop":
            raise EventProviderError(f"{self.label} response did not finish normally")
        message = getattr(choices[0], "message", None) if choices else None
        if getattr(message, "refusal", None):
            raise EventProviderError(f"{self.label} declined the analysis request")
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise EventProviderError(f"{self.label} returned no JSON content")
        return ProviderResult(
            content=content,
            response_model=_optional_string(getattr(response, "model", None)),
            usage=_usage_dict(getattr(response, "usage", None)),
        )
