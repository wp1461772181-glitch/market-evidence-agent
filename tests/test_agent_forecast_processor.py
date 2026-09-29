import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.database import Base, SessionLocal, engine
from app.evidence_context import freeze_evidence_context
from app.agent_forecast_processor import AgentForecastProcessor, _forecast_brief_error, _market_summary, _select_manifest_events
from app.forecast_v2 import ForecastDraft
from app.forecast_v2_processor import ResearchOnlyForecastProcessor
from app.forecast_jobs import enqueue_job
from app.material_analysis import get_material_analysis, request_material_analysis, run_material_analysis_once
from app.models import UploadedEvidence
from app.research_brief import ResearchBriefError, build_research_brief


def test_explicit_trial_sources_do_not_auto_fill_and_rejected_sources_are_skipped():
    now = datetime(2026, 9, 27, 10, tzinfo=UTC)
    manifest = [
        {"source_type": "official_filing", "source_id": "one", "is_new": True, "published_at": now.isoformat()},
        {"source_type": "official_filing", "source_id": "automatic", "is_new": True, "published_at": now.isoformat()},
        {"source_type": "uploaded_media", "source_id": "rejected", "is_new": True,
         "review_status": "rejected", "published_at": now.isoformat()},
        {"source_type": "uploaded_media", "source_id": "inactive", "is_new": True,
         "is_active": False, "published_at": now.isoformat()},
    ]

    selected, explicit, excluded = _select_manifest_events(
        manifest, [{"source_type": "official_filing", "source_id": "one"}]
    )

    assert [item["source_id"] for item in selected] == ["one"]
    assert explicit == [{"source_type": "official_filing", "source_id": "one"}]
    assert {item["reason"] for item in excluded} == {"source_rejected", "inactive_source"}


def test_market_summary_uses_the_requested_symbol_without_relying_on_row_symbol_fields():
    draft = SimpleNamespace(
        price_input_manifest={"rows": {"MSFT": [{"close": 410.0}], "SPY": [{"close": 500.0}]}},
        feature_snapshot={"momentum_5d": 0.02, "momentum_20d": 0.04, "volatility_20d": 0.3,
                          "volume_ratio_20d": 1.2, "remaining_sessions": 20,
                          "realized_return_from_anchor": 0.0},
        market_cutoff_at=datetime(2026, 9, 26, 20, tzinfo=UTC),
    )

    summary = _market_summary(draft, "MSFT")

    assert summary["symbol"] == "MSFT"
    assert summary["latest_close"] == 410.0
    assert summary["return_5_sessions"] == 0.02


def test_brief_failures_keep_safe_reason_and_retry_only_transient_provider_errors():
    provider_error = _forecast_brief_error(ResearchBriefError("provider unavailable", code="provider_error"))
    invalid_output = _forecast_brief_error(ResearchBriefError("invalid cited response", code="invalid_model_output"))
    invalid_contract = _forecast_brief_error(ResearchBriefError("invalid contract", code="invalid_brief_output"))

    assert provider_error.reason == "research_brief_provider_error"
    assert provider_error.retryable is True
    assert invalid_output.reason == "research_brief_invalid_model_output"
    assert invalid_output.retryable is True
    assert invalid_contract.reason == "research_brief_invalid_brief_output"
    assert invalid_contract.retryable is False


def test_real_l1_cache_reuses_text_analysis_after_review_state_change_without_rebinding():
    Base.metadata.create_all(bind=engine)
    text_value = "Revenue grew."
    raw = text_value.encode()
    source_id = uuid4()
    now = datetime.now(UTC) - timedelta(minutes=3)
    with SessionLocal() as db:
        db.add(UploadedEvidence(
            id=source_id, symbol="MSFT", title="Results", source_url="https://example.org/results",
            published_at=now - timedelta(days=1), observed_at=now - timedelta(hours=1),
            credibility_stars=2, credibility_reason="Awaiting review", impact_severity="medium",
            filename="results.txt", content_sha256=hashlib.sha256(raw).hexdigest(),
            raw_content=raw, content_text=text_value, status="unconfirmed",
        ))
        db.commit()

    class AnalysisProvider:
        def extract(self, **_kwargs):
            return SimpleNamespace(content=json.dumps({
                "schema_version": "material-analysis-v1",
                "prompt_version": "material-analysis-prompt-v1",
                "summary": "Revenue increased.",
                "facts": [{"id": "f1", "statement": "Revenue grew.",
                           "citations": [{"quote": "Revenue grew."}]}],
                "supporting": [], "counter": [], "uncertainties": [], "key_numbers": [],
            }), response_model="fixture-model", usage={})

    first_request_key = f"agent-l1-first-{uuid4()}"
    with SessionLocal() as db:
        queued = request_material_analysis(db=db, source_type="uploaded_media", source_id=source_id,
                                           idempotency_key=first_request_key)
    analyzed = run_material_analysis_once(
        session_factory=SessionLocal, provider=AnalysisProvider(), job_id=__import__("uuid").UUID(queued["job_id"]),
        worker_id="agent-processor-test",
    )
    assert analyzed["status"] == "succeeded"
    original_analysis_id = analyzed["analysis_id"]
    with SessionLocal() as db:
        original_analysis = get_material_analysis(db=db, analysis_id=__import__("uuid").UUID(original_analysis_id))
        original_analysis_evidence_id = original_analysis["evidence_version_id"]
        source = db.get(UploadedEvidence, source_id)
        source.credibility_stars = 5
        source.credibility_reason = "Reviewed and retained"
        db.commit()
        current_context = freeze_evidence_context(
            db=db, symbol="MSFT", decision_at=datetime.now(UTC), mode="observed",
            source_refs=[{"source_type": "uploaded_media", "source_id": source_id}], max_new_documents=1,
        )
    current_event = current_context.events[0].as_manifest_item()
    processor = AgentForecastProcessor(
        session_factory=SessionLocal, decision_mode="research_only",
        analysis_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cache hit must not run provider")),
    )
    cached = processor._ensure_analysis(job=SimpleNamespace(id=uuid4()), event=current_event)

    assert cached["analysis_id"] == original_analysis_id
    assert cached["evidence_version_id"] == original_analysis_evidence_id
    assert original_analysis_evidence_id != current_event["event_version_id"]
    assert cached["source_manifest"]["content_sha256"] == current_event["content_sha256"]

    refreshed_manifest = dict(cached["source_manifest"])
    refreshed_manifest.update({"review_status": current_event["review_status"],
                               "user_rating_stars": current_event["user_rating_stars"]})
    cached_for_brief = {**cached, "source_manifest": refreshed_manifest}

    class BriefProvider:
        def extract(self, **_kwargs):
            return SimpleNamespace(content=json.dumps({
                "new_facts": [], "supporting": [], "counter": [], "background": [],
                "conflicts": [], "unknowns": [], "changes": [],
            }), response_model="fixture-model", usage={})

    cutoff = datetime.now(UTC) + timedelta(seconds=1)
    brief = build_research_brief(
        market_summary={"symbol": "MSFT", "as_of": cutoff.isoformat(), "latest_close": 410.0,
                        "return_5_sessions": 0.01, "return_20_sessions": 0.03,
                        "volatility_20_sessions": 0.2, "volume_ratio_20_sessions": 1.0},
        analyses=[cached_for_brief], target_contract={"target_end_date": "2026-10-23"},
        decision_at=cutoff, parent_brief=None, provider=BriefProvider(),
    )
    assert brief.material_refs[0].analysis_id == original_analysis_id
    assert brief.material_refs[0].evidence_version_id == original_analysis_evidence_id
    assert brief.material_refs[0].user_rating_stars == 5
    with SessionLocal() as db:
        still_original = get_material_analysis(db=db, analysis_id=__import__("uuid").UUID(original_analysis_id))
    assert still_original["evidence_version_id"] == original_analysis_evidence_id
    assert still_original["source_manifest"].get("user_rating_stars") is None


def test_failed_material_analysis_retries_once_with_stronger_deepseek_model(monkeypatch):
    source_id = uuid4()
    calls = []

    class EmptySession:
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    def requester(**kwargs):
        calls.append(kwargs)
        retry = kwargs.get("model_override") is not None
        if not retry:
            return {"status": "queued", "job_id": str(uuid4())} if len([c for c in calls if c.get("model_override") is None]) == 1 else {"status": "failed", "safe_error_code": "invalid_model_output"}
        return {"status": "queued", "job_id": str(uuid4())} if len([c for c in calls if c.get("model_override")]) == 1 else {"status": "succeeded", "analysis_id": str(uuid4())}

    result_analysis_id = None

    def analysis_getter(*, db, analysis_id):
        nonlocal result_analysis_id
        result_analysis_id = analysis_id
        return {"analysis_id": str(analysis_id), "source_manifest": {"content_sha256": "a" * 64}}

    monkeypatch.setenv("DEEPSEEK_ANALYSIS_RETRY_MODEL", "deepseek-v4-pro")
    processor = AgentForecastProcessor(
        session_factory=EmptySession,
        analysis_requester=requester,
        analysis_runner=lambda **_kwargs: {"status": "failed"},
        analysis_getter=analysis_getter,
        material_provider_factory=lambda: object(),
    )
    result = processor._ensure_analysis(
        job=SimpleNamespace(id=uuid4()),
        event={"source_type": "official_filing", "source_id": str(source_id)},
    )

    assert result is not None
    assert result_analysis_id is not None
    assert len(calls) == 4
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
    assert calls[2]["idempotency_key"].endswith(":quality-retry-v1")
    assert calls[2]["model_override"] == "deepseek-v4-pro"
    assert calls[2]["force"] is False


def test_invalid_material_analysis_failure_is_cached_until_forced_retry():
    Base.metadata.create_all(bind=engine)
    content = b"Revenue increased by 12 percent."
    source_id = uuid4()
    with SessionLocal() as db:
        db.add(UploadedEvidence(
            id=source_id, symbol="MSFT", title="Results", source_url="https://example.org/results",
            published_at=datetime.now(UTC) - timedelta(days=1), observed_at=datetime.now(UTC),
            credibility_stars=3, credibility_reason="Awaiting review", impact_severity="medium",
            filename="results.txt", content_sha256=hashlib.sha256(content).hexdigest(),
            raw_content=content, content_text=content.decode(), status="unconfirmed",
        ))
        db.commit()
        first = request_material_analysis(
            db=db, source_type="uploaded_media", source_id=source_id,
            idempotency_key=f"invalid-analysis-first-{uuid4()}",
        )

    class InvalidProvider:
        def extract(self, **_kwargs):
            return SimpleNamespace(content="{}", response_model="fixture-model", usage={})

    outcome = run_material_analysis_once(
        session_factory=SessionLocal, provider=InvalidProvider(),
        job_id=__import__("uuid").UUID(first["job_id"]), worker_id="invalid-analysis-cache-test",
    )
    assert outcome["status"] == "failed"

    with SessionLocal() as db:
        cached_failure = request_material_analysis(
            db=db, source_type="uploaded_media", source_id=source_id,
            idempotency_key=f"invalid-analysis-cached-{uuid4()}",
        )
        forced = request_material_analysis(
            db=db, source_type="uploaded_media", source_id=source_id,
            idempotency_key=f"invalid-analysis-forced-{uuid4()}", force=True,
        )

    assert cached_failure["status"] == "failed"
    assert cached_failure["failure_cached"] is True
    assert cached_failure["job_id"] == first["job_id"]
    assert forced["status"] == "queued"
    assert forced["job_id"] != first["job_id"]


def test_insufficient_material_inputs_skip_jev_even_when_mode_is_enabled():
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_forecast_v2_processor import _Provider, _rows

    Base.metadata.create_all(bind=engine)
    now = datetime(2025, 3, 4, 22, tzinfo=UTC)
    anchor = datetime(2025, 1, 31).date()
    rows_by_symbol = {
        "AMZN": _rows(anchor=anchor, symbol="AMZN", observed_at=now),
        "SPY": _rows(anchor=anchor, symbol="SPY", observed_at=now),
    }
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol="AMZN", kind="new", idempotency_key=f"agent-insufficient-{uuid4()}")
        job_id = job.id

    def fail_jev():
        raise AssertionError("insufficient brief must not instantiate Jev")

    processor = AgentForecastProcessor(
        session_factory=SessionLocal, decision_mode="jev",
        market_provider_factory=lambda: _Provider(rows_by_symbol),
        now_factory=lambda: now,
        market_ingester=lambda *_args, **_kwargs: None,
        market_loader=lambda ticker, **_kwargs: rows_by_symbol[ticker],
        material_provider_factory=lambda: (_ for _ in ()).throw(AssertionError("there are no materials")),
        brief_provider_factory=lambda: SimpleNamespace(extract=lambda **_kwargs: None),
        jev_provider_factory=fail_jev,
    )
    with SessionLocal() as db:
        from app.forecast_v2_models import ForecastJobV2
        job = db.get(ForecastJobV2, job_id)
        draft = processor(job)

    assert draft.model_status == "research_only"
    assert draft.decision_probabilities is None
    assert draft.joint_probabilities is None
    assert draft.research_brief["input_quality"]["status"] == "insufficient"
    assert draft.research_brief["symbol"] == "AMZN"


@pytest.mark.parametrize("calibrator_available", [False, True])
def test_jev_processor_preserves_raw_output_and_records_calibration_fallback(monkeypatch, calibrator_available):
    import app.jev_learning as learning

    instant = datetime(2026, 9, 28, 15, tzinfo=UTC)
    raw = {"bearish": 0.2, "neutral": 0.3, "bullish": 0.5}
    calibrated = {"bearish": 0.3, "neutral": 0.4, "bullish": 0.3}
    base = ForecastDraft(
        target_contract={"target_spec_version": "absolute-close-v1", "horizon_sessions": 20, "threshold": 0.05},
        decision_at=instant,
        market_cutoff_at=instant - timedelta(minutes=1),
        price_input_manifest={"rows": {"MSFT": [{"close": 410.0}], "SPY": [{"close": 500.0}]}},
        evidence_version_manifest=[],
        feature_snapshot={"remaining_sessions": 20},
        baseline_probabilities=None,
        joint_probabilities=None,
        model_status="research_only",
        model_manifest={"time_mode": "observed"},
    )
    monkeypatch.setattr(ResearchOnlyForecastProcessor, "__call__", lambda _self, _job: base)

    class EmptySession:
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    provider_result = SimpleNamespace(
        probabilities=raw,
        requested_model="typesafe/jev-1.13",
        actual_model="typesafe/jev-1.13-20260917",
        request_id="fixture-request",
        usage={},
        latency_ms=10,
        question_version="jev-direction-v1",
        input_sha256="a" * 64,
        choice="neutral",
        confidence=0.5,
    )
    model = SimpleNamespace(id=uuid4(), parameters_sha256="b" * 64, training_manifest={"fixture": True})
    monkeypatch.setattr(
        learning,
        "active_jev_calibrator",
        lambda **_kwargs: model if calibrator_available else None,
    )
    monkeypatch.setattr(learning, "apply_jev_calibrator", lambda *_args: calibrated)
    processor = AgentForecastProcessor(
        session_factory=EmptySession,
        decision_mode="jev",
        now_factory=lambda: instant,
        material_provider_factory=lambda: None,
        brief_provider_factory=lambda: None,
        jev_provider_factory=lambda: SimpleNamespace(evaluate=lambda _brief: provider_result),
        brief_builder=lambda **_kwargs: {"schema_version": "research-brief-v1", "input_quality": {"status": "ready"}, "material_refs": []},
    )
    job = SimpleNamespace(id=uuid4(), parent_version_id=None, source_refs=[], symbol="MSFT", time_mode="observed")

    draft = processor(job)

    assert draft.model_manifest["decision_provider"]["raw_probabilities"] == raw
    if calibrator_available:
        assert draft.decision_probabilities == calibrated
        assert draft.model_manifest["local_calibration"]["status"] == "active"
    else:
        assert draft.decision_probabilities == raw
        assert draft.model_manifest["local_calibration"]["status"] == "not_applied"
