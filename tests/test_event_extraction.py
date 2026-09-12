from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.event_extraction import (
    DocumentInput,
    EventExtractionError,
    PROMPT_VERSION,
    cache_key_for,
    create_event_extraction_table,
    extract_document,
    build_system_prompt,
    project_events_for_review,
    validate_provider_result,
)
from app.event_provider import DeepSeekEventProvider, EventProviderError, ProviderResult
from app.models import EventExtraction


_TEXT = "Revenue increased 6% to $100 billion. The company returned $5 billion to shareholders."


def _document(**changes) -> DocumentInput:
    payload = {
        "document_id": "example-q1",
        "company": "Example Corp",
        "ticker": "EXM",
        "source_url": "https://investor.example.com/releases/q1",
        "source_domain": "investor.example.com",
        "published_date": "2026-01-30",
        "title": "Example Corp reports results",
        "text": _TEXT,
        "sha256": hashlib.sha256(_TEXT.encode()).hexdigest(),
    }
    payload.update(changes)
    if "text" in changes and "sha256" not in changes:
        payload["sha256"] = hashlib.sha256(changes["text"].encode()).hexdigest()
    return DocumentInput.model_validate(payload)


@dataclass
class FakeProvider:
    response: str
    calls: int = 0

    def extract(self, *, system_prompt: str, document_payload: str, model: str) -> ProviderResult:
        self.calls += 1
        assert "JSON" in system_prompt
        assert '"document_id":' in document_payload
        assert model == "deepseek-flash"
        return ProviderResult(content=self.response, response_model="deepseek-flash-actual", usage={"total_tokens": 12})


def _valid_response() -> str:
    return json.dumps(
        {
            "events": [
                {
                    "event_type": "earnings_release",
                    "event_date": None,
                    "impact_direction": "positive",
                    "summary": "Revenue increased in the reported results.",
                    "evidence_quote": "Revenue increased 6% to $100 billion.",
                }
            ]
        }
    )


def test_validates_and_caches_exact_document_result_without_a_second_provider_call():
    create_event_extraction_table()
    provider = FakeProvider(_valid_response())
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return provider

    document = _document()
    with SessionLocal() as db:
        first = extract_document(document, db=db, provider_factory=factory)
        second = extract_document(
            document,
            db=db,
            provider_factory=lambda: pytest.fail("cache hit must not build an API provider"),
        )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert factory_calls == provider.calls == 1
    assert second.batch.events[0].company == "Example Corp"
    assert second.batch.events[0].source_url == document.source_url
    assert second.batch.events[0].evidence_quote in document.text


def test_review_projection_filters_only_historical_capital_return_and_marks_directions():
    text = (
        "Revenue increased 6% to $100 billion. "
        "Microsoft returned $5 billion to shareholders in the second quarter. "
        "The board declared a cash dividend of $0.25 per share. "
        "The board authorized a repurchase program."
    )
    document = _document(text=text)
    batch = validate_provider_result(
        json.dumps(
            {
                "events": [
                    {
                        "event_type": "earnings_release",
                        "event_date": "2026-01-30",
                        "impact_direction": "positive",
                        "summary": "Revenue increased in the reported results.",
                        "evidence_quote": "Revenue increased 6% to $100 billion.",
                    },
                    {
                        "event_type": "capital_return",
                        "event_date": "2026-01-30",
                        "impact_direction": "neutral",
                        "summary": "Capital was returned during the quarter.",
                        "evidence_quote": "Microsoft returned $5 billion to shareholders in the second quarter.",
                    },
                    {
                        "event_type": "capital_return",
                        "event_date": "2026-01-30",
                        "impact_direction": "positive",
                        "summary": "A dividend was declared.",
                        "evidence_quote": "The board declared a cash dividend of $0.25 per share.",
                    },
                    {
                        "event_type": "capital_return",
                        "event_date": "2026-01-30",
                        "impact_direction": "positive",
                        "summary": "A repurchase was authorized.",
                        "evidence_quote": "The board authorized a repurchase program.",
                    },
                ]
            }
        ),
        document,
    )

    projected = project_events_for_review(batch)

    assert [event["evidence_quote"] for event in projected["excluded_events"]] == [
        "Microsoft returned $5 billion to shareholders in the second quarter."
    ]
    assert "historical_quarter" in projected["excluded_events"][0]["exclusion_reason"]
    assert [event["evidence_quote"] for event in projected["events"]] == [
        "Revenue increased 6% to $100 billion.",
        "The board declared a cash dividend of $0.25 per share.",
        "The board authorized a repurchase program.",
    ]
    for event in [*projected["events"], *projected["excluded_events"]]:
        assert event["impact_direction_status"] == "review_required"
        assert "not used for forecasts" in event["impact_direction_review_note"]


def test_cached_result_can_be_projected_without_constructing_a_provider():
    create_event_extraction_table()
    provider = FakeProvider(_valid_response())
    document = _document(document_id="review-cache-only")
    with SessionLocal() as db:
        extract_document(document, db=db, provider_factory=lambda: provider)
        cached = extract_document(
            document,
            db=db,
            provider_factory=lambda: pytest.fail("cache replay must not construct a provider"),
        )

    projected = project_events_for_review(cached.batch)

    assert cached.cache_hit is True
    assert provider.calls == 1
    assert len(projected["events"]) == 1
    assert projected["excluded_events"] == []
    assert projected["events"][0]["impact_direction_status"] == "review_required"


def test_cache_key_changes_for_document_metadata_model_and_prompt_version():
    document = _document()
    baseline = cache_key_for(document, provider="deepseek", model="deepseek-flash", prompt_version="v1")
    assert baseline != cache_key_for(
        _document(title="Changed title"), provider="deepseek", model="deepseek-flash", prompt_version="v1"
    )
    assert baseline != cache_key_for(
        _document(text=_TEXT + " New sentence."), provider="deepseek", model="deepseek-flash", prompt_version="v1"
    )
    assert baseline != cache_key_for(document, provider="deepseek", model="another-model", prompt_version="v1")
    assert baseline != cache_key_for(document, provider="deepseek", model="deepseek-flash", prompt_version="v2")


def test_invalid_model_quote_never_reaches_cache():
    create_event_extraction_table()
    provider = FakeProvider(
        json.dumps(
            {
                "events": [
                    {
                        "event_type": "other",
                        "event_date": None,
                        "impact_direction": "uncertain",
                        "summary": "Unsupported claim.",
                        "evidence_quote": "not in this document",
                    }
                ]
            }
        )
    )
    document = _document(document_id="invalid-quote")
    with SessionLocal() as db, pytest.raises(EventExtractionError, match="exact substring"):
        extract_document(document, db=db, provider_factory=lambda: provider)
    with SessionLocal() as db:
        assert db.get(EventExtraction, cache_key_for(document, provider="deepseek", model="deepseek-flash", prompt_version=PROMPT_VERSION)) is None


def test_corrupted_cached_result_fails_without_building_a_provider():
    create_event_extraction_table()
    document = _document(document_id="corrupt-cache")
    with SessionLocal() as db:
        extract_document(document, db=db, provider_factory=lambda: FakeProvider(_valid_response()))
        key = cache_key_for(document, provider="deepseek", model="deepseek-flash", prompt_version=PROMPT_VERSION)
        row = db.get(EventExtraction, key)
        assert row is not None
        row.result = {
            "events": [
                {
                    "company": document.company,
                    "event_type": "other",
                    "event_date": None,
                    "impact_direction": "neutral",
                    "summary": "",
                    "source_url": document.source_url,
                    "evidence_quote": "",
                }
            ]
        }
        db.commit()

    with SessionLocal() as db, pytest.raises(EventExtractionError, match="cached event extraction"):
        extract_document(
            document,
            db=db,
            provider_factory=lambda: pytest.fail("corrupt cache must not invoke provider"),
        )


def test_provider_payload_rejects_extra_keys_and_preserves_unknown_dates_as_null():
    document = _document()
    valid = validate_provider_result(_valid_response(), document)
    assert valid.events[0].event_date is None
    invalid = json.loads(_valid_response())
    invalid["events"][0]["company"] = "hallucinated"
    with pytest.raises(EventExtractionError, match="JSON contract"):
        validate_provider_result(json.dumps(invalid), document)


def test_prompt_uses_announcement_date_for_earnings_not_fiscal_period_end():
    prompt = build_system_prompt()
    assert "event_date must be the document's published_date" in prompt
    assert "not a fiscal-period end date" in prompt
    assert "no more than 160 characters" in prompt
    assert "Do not emit capital_return merely because" in prompt
    assert "never infer it from the earnings announcement date" in prompt


def test_document_input_requires_matching_hash_and_exact_source_domain():
    with pytest.raises(ValueError, match="sha256"):
        _document(sha256="0" * 64)
    with pytest.raises(ValueError, match="source_domain"):
        _document(source_domain="example.com")


def test_deepseek_adapter_uses_json_mode_and_rejects_non_stop_response():
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                model="deepseek-flash-2026",
                usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 7}),
                choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"events":[]}'))],
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = DeepSeekEventProvider(client=client).extract(
        system_prompt="Return JSON.", document_payload="{}", model="deepseek-flash"
    )
    assert result.content == '{"events":[]}'
    assert result.usage == {"total_tokens": 7}
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert captured["temperature"] == 0
    assert captured["max_tokens"] == 4096

    class TruncatedCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                model="deepseek-flash-2026",
                usage=None,
                choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"events":[]}'))],
            )

    truncated = DeepSeekEventProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=TruncatedCompletions()))
    )
    with pytest.raises(EventProviderError, match="finish normally"):
        truncated.extract(system_prompt="Return JSON.", document_payload="{}", model="deepseek-flash")


def test_deepseek_adapter_rejects_a_refusal_even_when_content_is_present():
    class RefusalCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                model="deepseek-flash-2026",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            refusal="I cannot do that", content='{"events":[]}'
                        ),
                    )
                ],
            )

    provider = DeepSeekEventProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=RefusalCompletions()))
    )
    with pytest.raises(EventProviderError, match="declined"):
        provider.extract(system_prompt="Return JSON.", document_payload="{}", model="deepseek-flash")


def test_missing_key_is_a_clean_configuration_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(DeepSeekEventProvider, "_load_project_env", staticmethod(lambda: None))
    with pytest.raises(EventProviderError, match="DEEPSEEK_API_KEY"):
        DeepSeekEventProvider()
