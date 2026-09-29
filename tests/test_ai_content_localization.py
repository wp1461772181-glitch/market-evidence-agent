from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.event_provider import ProviderResult
from app.forecast_v2_models import EvidenceEventVersionV2
from app.localization_models import AIContentTranslation
from app.material_analysis_models import MaterialAnalysisJob, MaterialAnalysisVersion
from app.ai_content_localization import (
    LocalizationError,
    _build_translatable_fields,
    _parse_translation_response,
    _protect_numeric_tokens,
    _restore_numeric_tokens,
    localize_ai_content,
)


@pytest.fixture(autouse=True)
def localization_tables(disposable_database):
    Base.metadata.create_all(bind=engine)


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.payloads: list[dict] = []

    def extract(self, *, system_prompt, document_payload, model):
        self.calls += 1
        request = json.loads(document_payload)
        self.payloads.append(request)
        translations = {item["key"]: f"English: {item['text']}" for item in request["fields"]}
        return ProviderResult(
            content=json.dumps({"fields": translations}, ensure_ascii=False),
            response_model="deepseek-test",
            usage={"total_tokens": 12},
        )


def _material_payload() -> dict:
    return {
        "schema_version": "material-analysis-v1",
        "prompt_version": "material-analysis-prompt-v1",
        "summary": "收入增长 8%。",
        "facts": [{"id": "f1", "statement": "收入达到 120 亿美元。", "citations": [{
            "quote": "Revenue reached $12 billion.", "start_char": 0, "end_char": 28,
        }]}],
        "supporting": [{"id": "s1", "statement": "需求有所增长。", "rationale": "同比提升 8%。",
                        "fact_ids": ["f1"], "citations": [{"quote": "Revenue reached $12 billion."}]}],
        "counter": [],
        "uncertainties": [{"id": "u1", "statement": "指引仍有不确定性。", "reason": "管理层没有给出 2027 年展望。",
                           "citations": []}],
        "key_numbers": [{"name": "收入", "value_text": "$12 billion, up 8%", "period": "2026 年 Q3",
                         "citations": [{"quote": "Revenue reached $12 billion."}]}],
    }


def _create_analysis(db) -> MaterialAnalysisVersion:
    source_id = uuid4()
    evidence = EvidenceEventVersionV2(
        symbol="AAPL", source_type="uploaded_media", source_id=source_id,
        source_snapshot={"title": "Original source title", "source_url": "https://example.test/source"},
        content_sha256="a" * 64, published_at=datetime(2026, 9, 1, tzinfo=UTC),
        observed_at=datetime(2026, 9, 2, tzinfo=UTC), event_key=f"test:{source_id}",
        structured_facts={}, citations=[], review_status="pending_review", review_snapshot={},
        review_fingerprint="", extraction_schema_version="test-v1", extraction_cache_key=None,
    )
    db.add(evidence)
    db.flush()
    job = MaterialAnalysisJob(
        source_type="uploaded_media", source_id=source_id, evidence_version_id=evidence.id,
        status="succeeded", current_stage="succeeded", idempotency_key=f"localize:{uuid4()}",
        request_fingerprint="b" * 64, input_fingerprint="c" * 64,
        requested_model="deepseek-test", schema_version="material-analysis-v1",
        prompt_version="material-analysis-prompt-v1", force=False, cache_hit=False,
        attempts=1,
    )
    db.add(job)
    db.flush()
    analysis = MaterialAnalysisVersion(
        source_type="uploaded_media", source_id=source_id, evidence_version_id=evidence.id,
        version_no=1, previous_version_id=None, job_id=job.id, input_fingerprint="c" * 64,
        schema_version="material-analysis-v1", prompt_version="material-analysis-prompt-v1",
        requested_model="deepseek-test", actual_model="deepseek-test", payload=_material_payload(),
        source_manifest={"title": "Original source title", "source_url": "https://example.test/source"},
    )
    db.add(analysis)
    db.flush()
    job.result_analysis_id = analysis.id
    db.commit()
    db.refresh(analysis)
    return analysis


def test_material_analysis_fields_exclude_quotes_and_keep_numeric_metadata():
    payload = _material_payload()
    fields = _build_translatable_fields("material_analysis", payload)

    assert fields["/summary"] == "收入增长 8%。"
    assert fields["/facts/0/statement"] == "收入达到 120 亿美元。"
    assert fields["/supporting/0/rationale"] == "同比提升 8%。"
    assert fields["/key_numbers/0/value_text"] == "$12 billion, up 8%"
    assert fields["/key_numbers/0/period"] == "2026 年 Q3"
    assert not any("quote" in key for key in fields)
    assert not any("id" in key for key in fields)


def test_research_brief_fields_include_narrative_but_exclude_titles_and_quotes():
    brief = {
        "material_refs": [{"title": "SEC original title", "analysis_summary": "收入增长。",
                           "key_numbers": [{"name": "收入", "value_text": "$12 billion", "period": "FY2026",
                                            "citations": [{"quote": "Revenue $12 billion"}]}]}],
        "new_facts": [{"statement": "利润增长。", "citations": [{"analysis_id": "a", "section": "facts", "item_id": "f1"}]}],
        "supporting": [], "counter": [],
        "background": [{"statement": "云业务保持增长。", "continuing_reason": "多个季度持续增长。", "citations": []}],
        "conflicts": [{"description": "两个来源的数字不同。", "citations": []}],
        "unknowns": [{"question": "需求会否持续？", "reason": "没有给出全年数据。", "citations": []}],
        "changes": [{"change_type": "modified", "description": "材料状态已更新。", "citations": []}],
        "input_quality": {"status": "limited", "reasons": ["存在未覆盖的材料。"]},
    }

    fields = _build_translatable_fields("forecast_brief", brief)

    assert fields["/material_refs/0/analysis_summary"] == "收入增长。"
    assert fields["/background/0/continuing_reason"] == "多个季度持续增长。"
    assert fields["/input_quality/reasons/0"] == "存在未覆盖的材料。"
    assert not any("title" in key or "quote" in key or "citations" in key for key in fields)


def test_translation_response_requires_exact_keys_and_unchanged_numeric_tokens():
    source = {"/summary": "收入增长 8%，达到 120 亿美元。", "/number": "$12 billion"}

    parsed = _parse_translation_response(
        json.dumps({"fields": {"/summary": "Revenue grew 8%, reaching $120 billion.",
                                "/number": "$12 billion"}}), source,
    )
    assert parsed["/summary"] == "Revenue grew 8%, reaching $120 billion."

    with pytest.raises(LocalizationError, match="numeric"):
        _parse_translation_response(json.dumps({"fields": {"/summary": "Revenue grew 9%.",
                                                               "/number": "$12 billion"}}), source)
    with pytest.raises(LocalizationError, match="field"):
        _parse_translation_response(json.dumps({"fields": {"/summary": "Revenue grew 8%.",
                                                               "/extra": "extra"}}), source)


def test_protected_numeric_tokens_restore_exact_source_values():
    source = {"/summary": "2026 年收入达到 $12.5 billion，同比增长 8%。"}
    protected, numbers = _protect_numeric_tokens(source)

    assert "2026" not in protected["/summary"]
    assert "12.5" not in protected["/summary"]
    assert "8%" not in protected["/summary"]
    placeholders = list(numbers["/summary"])
    translated = {"/summary": f"Revenue reached ${placeholders[1]} billion, up {placeholders[2]} in {placeholders[0]}."}

    assert _restore_numeric_tokens(translated, numbers, source) == {
        "/summary": "Revenue reached $12.5 billion, up 8% in 2026."
    }
    with pytest.raises(LocalizationError, match="protected numeric"):
        _restore_numeric_tokens({"/summary": f"Revenue reached {placeholders[1]} in {placeholders[0]}."}, numbers, source)


def test_translation_is_cached_and_canonical_analysis_is_unchanged():
    with SessionLocal() as db:
        analysis = _create_analysis(db)
        original_payload = dict(analysis.payload)
        provider = FakeProvider()

        first = localize_ai_content(
            db, content_kind="material_analysis", content_id=analysis.id, locale="en-US",
            provider_factory=lambda: provider,
        )
        second = localize_ai_content(
            db, content_kind="material_analysis", content_id=analysis.id, locale="en-US",
            provider_factory=lambda: provider,
        )

        assert first["cache_hit"] is False
        assert second["cache_hit"] is True
        assert first["fields"] == second["fields"]
        assert provider.calls == 1
        assert all("citations" not in item for request in provider.payloads for item in request["fields"])
        db.refresh(analysis)
        assert analysis.payload == original_payload
        assert db.scalar(select(AIContentTranslation).where(
            AIContentTranslation.content_id == analysis.id,
        )) is not None
