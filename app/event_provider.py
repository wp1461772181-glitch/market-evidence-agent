"""Small DeepSeek adapter for Week 6 event extraction.

The adapter deliberately exposes one JSON-only operation.  It has no web
tools, retrieval, or agent loop: documents are fetched and stored separately
so their provenance remains visible to the caller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


class EventProviderError(RuntimeError):
    """A safe-to-display provider configuration or request failure."""


@dataclass(frozen=True)
class ProviderResult:
    """The JSON text and non-sensitive response metadata returned by a model."""

    content: str
    response_model: str | None
    usage: dict[str, Any] | None


class DeepSeekEventProvider:
    """Call DeepSeek's OpenAI-compatible Chat Completions JSON mode."""

    name = "deepseek"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if api_key is None:
                self._load_project_env()
                api_key = os.getenv("DEEPSEEK_API_KEY")
            if not api_key:
                raise EventProviderError("DEEPSEEK_API_KEY is required for an uncached DeepSeek extraction")
            client = self._create_client(api_key)
        self.model = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
        if not self.model.strip():
            raise EventProviderError("DEEPSEEK_MODEL must not be empty")
        self._client = client

    @staticmethod
    def _load_project_env() -> None:
        """Load only this repository's optional .env without replacing shell env."""
        from dotenv import load_dotenv

        project_env = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(project_env, override=False)

    @staticmethod
    def _create_client(api_key: str) -> Any:
        from openai import OpenAI

        return OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=60.0,
            max_retries=0,
        )

    def extract(self, *, system_prompt: str, document_payload: str, model: str) -> ProviderResult:
        """Request a single JSON object, with thinking disabled to bound cost."""
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": document_payload},
                ],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                temperature=0,
                max_tokens=4096,
            )
        except Exception as exc:  # The SDK exception may contain request details; do not expose it.
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                raise EventProviderError(f"DeepSeek extraction request failed (HTTP {status_code})") from None
            raise EventProviderError(
                f"DeepSeek extraction request failed ({type(exc).__name__})"
            ) from None

        choices = getattr(response, "choices", None) or []
        finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
        if finish_reason != "stop":
            raise EventProviderError("DeepSeek response did not finish normally")
        message = getattr(choices[0], "message", None) if choices else None
        if getattr(message, "refusal", None):
            raise EventProviderError("DeepSeek declined the extraction request")
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise EventProviderError("DeepSeek returned no JSON content")
        return ProviderResult(
            content=content,
            response_model=_optional_string(getattr(response, "model", None)),
            usage=_usage_dict(getattr(response, "usage", None)),
        )


def create_deepseek_provider_from_env(*, model: str | None = None) -> DeepSeekEventProvider:
    """Build a live provider only after the caller has exhausted cache lookup."""
    return DeepSeekEventProvider(model=model)


def configured_deepseek_model(model: str | None = None) -> str:
    """Resolve the model from the repository .env without requiring a key."""
    DeepSeekEventProvider._load_project_env()
    resolved = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    if not resolved.strip():
        raise EventProviderError("DEEPSEEK_MODEL must not be empty")
    return resolved


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _usage_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else None
    if isinstance(value, dict):
        return value
    return None
