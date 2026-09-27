import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.database import Base, SessionLocal, engine
from app.evidence_context import freeze_evidence_context
from app.agent_forecast_processor import AgentForecastProcessor, _market_summary, _select_manifest_events
from app.forecast_jobs import enqueue_job
from app.material_analysis import get_material_analysis, request_material_analysis, run_material_analysis_once
from app.models import UploadedEvidence
from app.research_brief import build_research_brief


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
