"""Research-brief forecast processor using the existing observed V2 input path."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID

from dotenv import load_dotenv

from .event_provider import configured_deepseek_model, create_deepseek_provider_from_env
from .forecast_v2 import ForecastDraft
from .forecast_v2_models import ForecastJobV2
from .forecast_v2_processor import ForecastInputError, ResearchOnlyForecastProcessor
from .jev_provider import JevProviderError, create_jev_provider_from_env
from .material_analysis import (
    get_material_analysis,
    request_material_analysis,
    run_material_analysis_once,
)
from .research_brief import ResearchBrief, build_research_brief


PROCESSOR_VERSION = "agent-forecast-processor-v1"
MAX_ANALYZED_MATERIALS = 8


class AgentForecastProcessor(ResearchOnlyForecastProcessor):
    """Refresh/freeze through V2, analyze selected materials, then build a brief.

    The parent class owns market refresh, point-in-time source freezing and
    immutable root/revision contracts. Its old research bridge is overridden
    so this path only uses versioned single-material analysis and one brief.
    All model boundaries remain injectable for offline tests.
    """

    def __init__(
        self,
        *,
        session_factory,
        decision_mode: str | None = None,
        material_provider_factory: Callable[[], Any] | None = None,
        brief_provider_factory: Callable[[], Any] | None = None,
        jev_provider_factory: Callable[[], Any] | None = None,
        analysis_requester=request_material_analysis,
        analysis_runner=run_material_analysis_once,
        analysis_getter=get_material_analysis,
        brief_builder=build_research_brief,
        **market_inputs,
    ) -> None:
        super().__init__(session_factory=session_factory, **market_inputs)
        self._decision_mode = _decision_mode(decision_mode)
        self._material_provider_factory = material_provider_factory or _deepseek_provider_factory
        self._brief_provider_factory = brief_provider_factory or _deepseek_provider_factory
        self._jev_provider_factory = jev_provider_factory or create_jev_provider_from_env
        self._analysis_requester = analysis_requester
        self._analysis_runner = analysis_runner
        self._analysis_getter = analysis_getter
        self._brief_builder = brief_builder

    def _run_frozen_research(self, *, db, context) -> dict[str, Any]:
        # Prevent the old report bridge from issuing a second, incompatible
        # research request. L3 uses material-analysis versions and one brief.
        return {
            "status": "material_analysis_pipeline",
            "research_conclusions_status": "pending",
            "research_conclusions_reason": "research_brief_is_built_after_material_analysis",
            "source_ids": [str(event.id) for event in context.events],
            "source_snapshot": [],
        }

    def __call__(self, job: ForecastJobV2) -> ForecastDraft:
        base = super().__call__(job)
        parent = self._load_parent_for_brief(job)
        selected_events, explicit_refs, excluded_refs = _select_manifest_events(
            base.evidence_version_manifest, job.source_refs or []
        )
        analyses: list[dict[str, Any]] = []
        unavailable: list[dict[str, str]] = []
        for event in selected_events:
            identity = {"source_type": event["source_type"], "source_id": event["source_id"]}
            try:
                analysis = self._ensure_analysis(job=job, event=event)
            except ForecastInputError:
                unavailable.append(identity)
                continue
            if analysis is None:
                unavailable.append(identity)
                continue
            manifest = dict(analysis.get("source_manifest") or {})
            # A material service call can observe a source update after the
            # forecast's initial freeze. Such an analysis belongs to that new
            # source version and cannot be attached to this forecast.
            if manifest.get("content_sha256") != event.get("content_sha256"):
                unavailable.append(identity)
                continue
            manifest.update({
                "review_status": event.get("review_status", "pending_review"),
                "user_rating_stars": event.get("user_rating_stars"),
                "is_active": event.get("is_active", True),
                "is_corrected_or_withdrawn": event.get("is_corrected_or_withdrawn", False),
            })
            analysis["source_manifest"] = manifest
            analysis["review_status"] = manifest["review_status"]
            analysis["user_rating_stars"] = manifest["user_rating_stars"]
            analysis["is_active"] = manifest["is_active"]
            analysis["is_corrected_or_withdrawn"] = manifest["is_corrected_or_withdrawn"]
            analyses.append(analysis)

        observed_times = [
            _parse_aware(row.get("source_manifest", {}).get("observed_at"))
            for row in analyses
        ]
        decision_at = max([base.decision_at, _utc(self._now_factory()), *[t for t in observed_times if t]])
        market_summary = _market_summary(base, job.symbol)
        try:
            brief = self._brief_builder(
                market_summary=market_summary,
                analyses=analyses,
                target_contract=base.target_contract,
                decision_at=decision_at,
                parent_brief=parent.research_brief if parent and parent.research_brief else None,
                provider=self._brief_provider_factory() if analyses else None,
                explicit_source_refs=explicit_refs,
                unavailable_source_refs=unavailable,
                excluded_source_refs=excluded_refs,
                model=configured_deepseek_model(),
            )
        except Exception as exc:
            if isinstance(exc, ForecastInputError):
                raise
            raise ForecastInputError("research_brief_failed") from None

        brief_data = brief.model_dump(mode="json") if isinstance(brief, ResearchBrief) else brief
        model_manifest: dict[str, Any] = {
            "processor_version": PROCESSOR_VERSION,
            "model_status": "research_only",
            "decision_mode": self._decision_mode,
            "time_mode": "observed",
            "research_brief_schema_version": brief_data.get("schema_version"),
            "research_brief_sha256": _digest(brief_data),
            "material_analysis_ids": [item.get("analysis_id") for item in brief_data.get("material_refs", [])],
        }
        decision_probabilities = None
        model_status = "research_only"
        if self._decision_mode == "jev" and brief_data.get("input_quality", {}).get("status") != "insufficient":
            try:
                jev_result = self._jev_provider_factory().evaluate(brief_data)
            except JevProviderError as exc:
                raise ForecastInputError(f"jev_{exc.code}", retryable=exc.retryable) from None
            except Exception:
                raise ForecastInputError("jev_provider_error", retryable=True) from None
            decision_probabilities = dict(jev_result.probabilities)
            model_status = "experimental_jev"
            model_manifest.update({
                "model_status": model_status,
                "decision_provider": {
                    "provider": "openrouter",
                    "requested_model": jev_result.requested_model,
                    "actual_model": jev_result.actual_model,
                    "request_id": jev_result.request_id,
                    "usage": jev_result.usage,
                    "latency_ms": jev_result.latency_ms,
                    "question_version": jev_result.question_version,
                    "input_sha256": jev_result.input_sha256,
                    "choice": jev_result.choice,
                    "confidence": jev_result.confidence,
                },
            })

        feature_snapshot = dict(base.feature_snapshot)
        feature_snapshot["research_brief_input_quality"] = brief_data.get("input_quality", {}).get("status")
        prior_report = dict(base.research_report or {})
        prior_report.update({
            "time_mode": "observed",
            "decision_mode": self._decision_mode,
            "research_conclusions_status": "ready" if brief_data.get("input_quality", {}).get("status") != "insufficient" else "insufficient",
        })
        return ForecastDraft(
            target_contract=base.target_contract,
            decision_at=decision_at,
            market_cutoff_at=base.market_cutoff_at,
            price_input_manifest=base.price_input_manifest,
            evidence_version_manifest=base.evidence_version_manifest,
            feature_snapshot=feature_snapshot,
            baseline_probabilities=None,
            joint_probabilities=None,
            model_status=model_status,
            model_manifest=model_manifest,
            research_report=prior_report,
            change_reason=base.change_reason,
            trigger_type=base.trigger_type,
            decision_probabilities=decision_probabilities,
            research_brief=brief_data,
        )

    def _load_parent_for_brief(self, job: ForecastJobV2):
        if job.parent_version_id is None:
            return None
        with self._session_factory() as db:
            from .forecast_v2_models import ForecastVersionV2
            return db.get(ForecastVersionV2, job.parent_version_id)

    def _ensure_analysis(self, *, job: ForecastJobV2, event: dict[str, Any]) -> dict[str, Any] | None:
        source_type = event.get("source_type")
        source_id = event.get("source_id")
        try:
            source_uuid = UUID(str(source_id))
        except (TypeError, ValueError):
            return None
        idempotency_key = f"forecast:{job.id}:{source_type}:{source_uuid}"
        with self._session_factory() as db:
            requested = self._analysis_requester(
                db=db, source_type=source_type, source_id=source_uuid,
                idempotency_key=idempotency_key, force=False,
            )
        status = requested.get("status")
        analysis_id = requested.get("analysis_id")
        if status in {"queued", "running"}:
            try:
                material_provider = self._material_provider_factory()
                self._analysis_runner(
                    session_factory=self._session_factory,
                    provider=material_provider,
                    job_id=UUID(str(requested["job_id"])),
                    worker_id=f"forecast-{job.id}",
                )
            except Exception:
                return None
            with self._session_factory() as db:
                refreshed = self._analysis_requester(
                    db=db, source_type=source_type, source_id=source_uuid,
                    idempotency_key=idempotency_key, force=False,
                )
            status = refreshed.get("status")
            analysis_id = refreshed.get("analysis_id")
        if status != "succeeded" or not analysis_id:
            return None
        try:
            with self._session_factory() as db:
                return self._analysis_getter(db=db, analysis_id=UUID(str(analysis_id)))
        except Exception:
            return None


def _decision_mode(value: str | None) -> str:
    if value is None:
        load_dotenv(__import__("pathlib").Path(__file__).resolve().parent.parent / ".env", override=False)
        value = os.getenv("FORECAST_DECISION_MODE", "research_only")
    if not isinstance(value, str) or value.strip().lower() not in {"research_only", "jev"}:
        raise ForecastInputError("invalid_forecast_decision_mode")
    return value.strip().lower()


def _deepseek_provider_factory():
    return create_deepseek_provider_from_env(model=configured_deepseek_model())


def _select_manifest_events(manifest: list[dict[str, Any]], explicit_refs: list[dict[str, Any]]):
    explicit = {(item.get("source_type"), str(item.get("source_id"))) for item in explicit_refs}
    by_group: dict[bool, list[dict[str, Any]]] = {True: [], False: []}
    excluded: list[dict[str, str]] = []
    for item in manifest:
        if not isinstance(item, dict) or item.get("source_type") not in {"official_filing", "uploaded_media"}:
            continue
        identity = (item.get("source_type"), str(item.get("source_id")))
        if item.get("review_status") == "rejected":
            excluded.append({"source_type": identity[0], "source_id": identity[1], "reason": "source_rejected"})
            continue
        if item.get("is_active") is False or item.get("is_corrected_or_withdrawn") is True:
            excluded.append({"source_type": identity[0], "source_id": identity[1], "reason": "inactive_source"})
            continue
        is_new = bool(item.get("is_new"))
        by_group[is_new].append(item)
    for values in by_group.values():
        values.sort(key=lambda item: (
            (item.get("source_type"), str(item.get("source_id"))) in explicit,
            str(item.get("published_at") or ""), str(item.get("observed_at") or ""),
        ), reverse=True)
    explicit_new = [item for item in by_group[True] if (item.get("source_type"), str(item.get("source_id"))) in explicit]
    explicit_background = [item for item in by_group[False] if (item.get("source_type"), str(item.get("source_id"))) in explicit]
    if len(explicit_new) > 5 or len(explicit_background) > 3 or len(explicit) > MAX_ANALYZED_MATERIALS:
        raise ForecastInputError("explicit_material_limit")
    if explicit:
        selected = [item for item in by_group[True] if (item.get("source_type"), str(item.get("source_id"))) in explicit]
    else:
        selected = by_group[True][:5]
    selected.extend(by_group[False][:3])
    return selected, explicit_refs, excluded


def _market_summary(draft: ForecastDraft, symbol: str) -> dict[str, Any]:
    rows = draft.price_input_manifest.get("rows", {}).get(symbol, [])
    last = rows[-1] if rows else {}
    features = draft.feature_snapshot
    return {
        "symbol": symbol,
        "as_of": draft.market_cutoff_at.isoformat(),
        "latest_close": last.get("close"),
        "return_5_sessions": features.get("momentum_5d"),
        "return_20_sessions": features.get("momentum_20d"),
        "volatility_20_sessions": features.get("volatility_20d"),
        "volume_ratio_20_sessions": features.get("volume_ratio_20d"),
        "remaining_sessions": features.get("remaining_sessions"),
        "realized_return_from_anchor": features.get("realized_return_from_anchor"),
    }


def _parse_aware(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return _utc(parsed)
        except ValueError:
            return None
    return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastInputError("invalid_stored_time")
    return value.astimezone(UTC)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
