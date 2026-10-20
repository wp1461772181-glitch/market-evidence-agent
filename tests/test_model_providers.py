from types import SimpleNamespace

import pytest

from app.model_providers import (
    PROVIDER_CATALOG,
    OpenAICompatibleProvider,
    thinking_disabled_options,
)


class _Completions:
    def __init__(self, captured):
        self.captured = captured

    def create(self, **kwargs):
        self.captured.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"ok":true}', refusal=None),
                )
            ],
            model="test-model",
            usage=None,
        )


class _Client:
    def __init__(self, captured):
        self.chat = SimpleNamespace(completions=_Completions(captured))


@pytest.mark.parametrize("provider", list(PROVIDER_CATALOG))
def test_every_analysis_provider_sends_a_disable_thinking_switch(provider):
    captured = []
    instance = object.__new__(OpenAICompatibleProvider)
    instance.provider = provider
    instance.label = provider
    instance.model = "test-model"
    instance._client = _Client(captured)

    instance.extract(system_prompt="system", document_payload="document", model="test-model")

    assert captured
    assert captured[0]["extra_body"] == thinking_disabled_options(provider)
    assert captured[0]["extra_body"]


def test_thinking_switch_uses_the_provider_native_shape():
    assert thinking_disabled_options("deepseek") == {"thinking": {"type": "disabled"}}
    assert thinking_disabled_options("custom") == {"thinking": {"type": "disabled"}}
    assert thinking_disabled_options("qwen") == {"enable_thinking": False}
    assert thinking_disabled_options("siliconflow") == {"enable_thinking": False}
    assert thinking_disabled_options("openrouter") == {"reasoning": {"enabled": False}}
