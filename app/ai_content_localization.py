"""Translate approved AI narrative fields without mutating canonical records."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .event_provider import (
    DeepSeekEventProvider,
    EventProviderError,
    configured_deepseek_model,
    create_deepseek_provider_from_env,
)
from .forecast_v2_models import ForecastVersionV2
from .localization_models import AIContentTranslation
from .material_analysis_models import MaterialAnalysisVersion


ContentKind = Literal["material_analysis", "forecast_brief"]
LOCALE = "en-US"
PROMPT_VERSION = "ai-content-translation-v1"
_NUMBER_TOKEN = re.compile(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
_TRANSLATION_CHAR_LIMIT = 9_000
_NUMBER_PLACEHOLDER = re.compile(r"\[\[NUMTOKEN_([A-Z]+)\]\]")


class LocalizationError(ValueError):
    def __init__(self, message: str, *, code: str = "localization_failed", status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def localize_ai_content(
    db: Session,
    *,
    content_kind: ContentKind,
    content_id: UUID,
    locale: Literal["en-US"] = LOCALE,
    provider_factory: Callable[[], DeepSeekEventProvider] | None = None,
) -> dict[str, Any]:
    if locale != LOCALE:
        raise LocalizationError("Only English display translations are supported.", code="unsupported_locale", status_code=422)

    lock_key = _advisory_lock_key(content_kind, content_id, locale)
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # Hold a separate advisory-lock transaction across provider work. This
        # serializes same-record cache misses without keeping the ORM read
        # transaction open during a potentially slow network request.
        with bind.begin() as lock_connection:
            lock_connection.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
            return _localize_locked(
                db, content_kind=content_kind, content_id=content_id, locale=locale,
                provider_factory=provider_factory,
            )
    return _localize_locked(
        db, content_kind=content_kind, content_id=content_id, locale=locale,
        provider_factory=provider_factory,
    )


def _localize_locked(
    db: Session,
    *,
    content_kind: ContentKind,
    content_id: UUID,
    locale: Literal["en-US"],
    provider_factory: Callable[[], DeepSeekEventProvider] | None,
) -> dict[str, Any]:
    source = _load_content(db, content_kind, content_id)
    source_sha256 = hashlib.sha256(_canonical_json(source).encode("utf-8")).hexdigest()
    cached = db.scalar(select(AIContentTranslation).where(
        AIContentTranslation.content_kind == content_kind,
        AIContentTranslation.content_id == content_id,
        AIContentTranslation.source_sha256 == source_sha256,
        AIContentTranslation.locale == locale,
        AIContentTranslation.prompt_version == PROMPT_VERSION,
    ))
    if cached is not None:
        return _result(cached, cache_hit=True)

    source_fields = _build_translatable_fields(content_kind, source)
    translated_fields: dict[str, str] = {}
    usage_records: list[dict[str, Any]] = []
    requested_model = "none"
    actual_models: list[str] = []
    if source_fields:
        try:
            requested_model = configured_deepseek_model()
            provider = provider_factory() if provider_factory else create_deepseek_provider_from_env(model=requested_model)
            for field_chunk in _chunk_fields(source_fields):
                protected_fields, protected_numbers = _protect_numeric_tokens(field_chunk)
                result = provider.extract(
                    system_prompt=_translation_prompt(),
                    document_payload=json.dumps(
                        {"target_locale": "English (United States)", "fields": [
                            {"key": key, "text": value} for key, value in protected_fields.items()
                        ]},
                        ensure_ascii=False,
                    ),
                    model=requested_model,
                )
                parsed = _parse_translation_response(result.content, protected_fields)
                translated_fields.update(_restore_numeric_tokens(parsed, protected_numbers, field_chunk))
                if result.usage:
                    usage_records.append(result.usage)
                if result.response_model:
                    actual_models.append(result.response_model)
        except LocalizationError:
            raise
        except EventProviderError:
            raise LocalizationError(
                "The translation provider could not complete this request.",
                code="translation_provider_failed", status_code=502,
            ) from None
        except Exception:
            # Do not expose raw provider responses, request details, or database
            # diagnostics through this display-only endpoint.
            raise LocalizationError(
                "The saved AI content could not be translated.",
                code="translation_provider_failed", status_code=502,
            ) from None

    row = AIContentTranslation(
        content_kind=content_kind,
        content_id=content_id,
        source_sha256=source_sha256,
        locale=locale,
        prompt_version=PROMPT_VERSION,
        fields=translated_fields,
        provider="deepseek" if source_fields else "none",
        requested_model=requested_model,
        actual_model=", ".join(dict.fromkeys(actual_models)) or requested_model,
        usage={"calls": len(usage_records), "responses": usage_records} if usage_records else None,
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # A unique constraint is the final guard if the service is deployed
        # without PostgreSQL advisory locking.
        cached = db.scalar(select(AIContentTranslation).where(
            AIContentTranslation.content_kind == content_kind,
            AIContentTranslation.content_id == content_id,
            AIContentTranslation.source_sha256 == source_sha256,
            AIContentTranslation.locale == locale,
            AIContentTranslation.prompt_version == PROMPT_VERSION,
        ))
        if cached is None:
            raise LocalizationError("The translation could not be saved; please retry.",
                                    code="translation_cache_failed", status_code=503) from None
        return _result(cached, cache_hit=True)
    db.refresh(row)
    return _result(row, cache_hit=False)


def _load_content(db: Session, content_kind: ContentKind, content_id: UUID) -> dict[str, Any]:
    if content_kind == "material_analysis":
        row = db.get(MaterialAnalysisVersion, content_id)
        if row is None:
            raise LocalizationError("The material analysis was not found.", code="not_found", status_code=404)
        return row.payload if isinstance(row.payload, dict) else {}
    if content_kind == "forecast_brief":
        row = db.get(ForecastVersionV2, content_id)
        if row is None:
            raise LocalizationError("The forecast version was not found.", code="not_found", status_code=404)
        if not isinstance(row.research_brief, dict):
            raise LocalizationError("This forecast version has no saved research brief.",
                                    code="localization_content_missing", status_code=404)
        return row.research_brief
    raise LocalizationError("Unsupported AI content type.", code="unsupported_content_kind", status_code=422)


def _build_translatable_fields(content_kind: ContentKind, payload: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}

    def add(path: tuple[str | int, ...], value: Any) -> None:
        if isinstance(value, str) and value.strip():
            fields[_json_pointer(path)] = value

    def list_fields(group_name: str, names: tuple[str, ...]) -> None:
        rows = payload.get(group_name)
        if not isinstance(rows, list):
            return
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                continue
            for name in names:
                add((group_name, index, name), item.get(name))

    if content_kind == "material_analysis":
        add(("summary",), payload.get("summary"))
        list_fields("facts", ("statement",))
        list_fields("supporting", ("statement", "rationale"))
        list_fields("counter", ("statement", "rationale"))
        list_fields("uncertainties", ("statement", "reason"))
        list_fields("key_numbers", ("name", "value_text", "period"))
    elif content_kind == "forecast_brief":
        list_fields("new_facts", ("statement",))
        list_fields("supporting", ("statement",))
        list_fields("counter", ("statement",))
        list_fields("background", ("statement", "continuing_reason"))
        list_fields("conflicts", ("description",))
        list_fields("unknowns", ("question", "reason"))
        list_fields("changes", ("description",))
        refs = payload.get("material_refs")
        if isinstance(refs, list):
            for index, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    continue
                add(("material_refs", index, "analysis_summary"), ref.get("analysis_summary"))
                key_numbers = ref.get("key_numbers")
                if isinstance(key_numbers, list):
                    for number_index, number in enumerate(key_numbers):
                        if not isinstance(number, dict):
                            continue
                        for name in ("name", "value_text", "period"):
                            add(("material_refs", index, "key_numbers", number_index, name), number.get(name))
        quality = payload.get("input_quality")
        reasons = quality.get("reasons") if isinstance(quality, dict) else None
        if isinstance(reasons, list):
            for index, reason in enumerate(reasons):
                add(("input_quality", "reasons", index), reason)
    else:
        raise LocalizationError("Unsupported AI content type.", code="unsupported_content_kind", status_code=422)
    return fields


def _parse_translation_response(raw: str, source_fields: dict[str, str]) -> dict[str, str]:
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise LocalizationError("The translation response was not valid JSON.", code="invalid_translation") from None
    translated = decoded.get("fields") if isinstance(decoded, dict) else None
    if not isinstance(translated, dict) or set(translated) != set(source_fields):
        raise LocalizationError("The translation response did not match the saved fields.", code="invalid_translation")
    result: dict[str, str] = {}
    for key, source_text in source_fields.items():
        value = translated.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LocalizationError("The translation response contained an empty field.", code="invalid_translation")
        if _number_tokens(value) != _number_tokens(source_text):
            raise LocalizationError("The translation changed a numeric value.", code="invalid_translation_numeric")
        result[key] = value.strip()
    return result


def _protect_numeric_tokens(fields: dict[str, str]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    protected: dict[str, str] = {}
    numbers_by_field: dict[str, dict[str, str]] = {}
    for key, source_text in fields.items():
        numbers: dict[str, str] = {}
        token_index = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal token_index
            while True:
                placeholder = f"[[NUMTOKEN_{_alphabetic_index(token_index)}]]"
                token_index += 1
                if placeholder not in source_text and placeholder not in numbers:
                    break
            numbers[placeholder] = match.group(0)
            return placeholder

        protected[key] = _NUMBER_TOKEN.sub(replace, source_text)
        if numbers:
            numbers_by_field[key] = numbers
    return protected, numbers_by_field


def _restore_numeric_tokens(
    translated_fields: dict[str, str],
    numbers_by_field: dict[str, dict[str, str]],
    source_fields: dict[str, str],
) -> dict[str, str]:
    restored: dict[str, str] = {}
    for key, translated in translated_fields.items():
        replacements = numbers_by_field.get(key, {})
        placeholders = Counter(_NUMBER_PLACEHOLDER.findall(translated))
        expected = Counter(match.group(1) for placeholder in replacements if (match := _NUMBER_PLACEHOLDER.fullmatch(placeholder)))
        if placeholders != expected:
            raise LocalizationError("The translation changed a protected numeric token.", code="invalid_translation_numeric")
        value = _NUMBER_PLACEHOLDER.sub(lambda match: replacements[match.group(0)], translated)
        if _number_tokens(value) != _number_tokens(source_fields[key]):
            raise LocalizationError("The translation changed a numeric value.", code="invalid_translation_numeric")
        restored[key] = value
    return restored


def _alphabetic_index(value: int) -> str:
    result = ""
    while value >= 0:
        result = chr(ord("A") + value % 26) + result
        value = value // 26 - 1
    return result


def _chunk_fields(fields: dict[str, str]):
    chunk: dict[str, str] = {}
    character_count = 0
    for key, value in fields.items():
        if chunk and character_count + len(value) > _TRANSLATION_CHAR_LIMIT:
            yield chunk
            chunk = {}
            character_count = 0
        chunk[key] = value
        character_count += len(value)
    if chunk:
        yield chunk


def _translation_prompt() -> str:
    return (
        "Translate each supplied AI-generated value into natural English (United States). "
        "Return one JSON object with exactly this shape: {\"fields\": {<same key>: <translated string>}}. "
        "Keep every key byte-for-byte identical. Preserve every numeric token and its value exactly, including "
        "percentages, dates, fiscal periods, and currency amounts. Translate descriptive words around numbers "
        "when useful. Values may contain protected placeholders such as [[NUMTOKEN_A]]; copy each placeholder "
        "exactly once without translating or moving it. Do not add facts, citations, quotations, IDs, headings, or keys. The input contains only "
        "AI-generated narrative fields; source titles and verbatim source quotations are deliberately omitted."
    )


def _number_tokens(value: str) -> Counter[str]:
    return Counter(_NUMBER_TOKEN.findall(value))


def _json_pointer(path: tuple[str | int, ...]) -> str:
    def escape(value: str | int) -> str:
        return str(value).replace("~", "~0").replace("/", "~1")
    return "/" + "/".join(escape(item) for item in path)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _advisory_lock_key(content_kind: str, content_id: UUID, locale: str) -> int:
    digest = hashlib.sha256(f"localization:{content_kind}:{content_id}:{locale}".encode()).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


def _result(row: AIContentTranslation, *, cache_hit: bool) -> dict[str, Any]:
    return {
        "content_kind": row.content_kind,
        "content_id": str(row.content_id),
        "locale": row.locale,
        "source_sha256": row.source_sha256,
        "prompt_version": row.prompt_version,
        "cache_hit": cache_hit,
        "fields": row.fields,
    }
