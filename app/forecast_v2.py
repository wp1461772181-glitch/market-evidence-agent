"""Atomic, immutable V2 forecast publication.

The worker does slow source/model work before calling this function. Only the
short validation and insertion transaction happens here.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .forecast_contract import ForecastContractError, target_contract_from_dict
from .forecast_v2_models import ForecastJobV2, ForecastVersionV2
from .market_time import normalize_utc, xnys_session_close_at


class ForecastPublicationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_publication") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ForecastDraft:
    target_contract: dict[str, Any]
    decision_at: datetime
    market_cutoff_at: datetime
    price_input_manifest: dict[str, Any]
    evidence_version_manifest: list[dict[str, Any]]
    feature_snapshot: dict[str, Any]
    baseline_probabilities: dict[str, float] | None
    joint_probabilities: dict[str, float] | None
    model_status: str
    model_manifest: dict[str, Any]
    research_report: dict[str, Any] | None = None
    change_reason: str | None = None
    trigger_type: str = "manual"
    decision_probabilities: dict[str, float] | None = None
    research_brief: dict[str, Any] | None = None


def publish_forecast_version(
    *,
    db: Session,
    job_id: UUID,
    worker_id: str,
    lease_epoch: int,
    draft: ForecastDraft,
    now: datetime | None = None,
) -> tuple[ForecastVersionV2, bool]:
    """Publish at most one result for a job/root/input combination.

    Returns ``(version, created)``. The job status and result pointer commit
    with the version. An expired or superseded worker cannot publish.
    """
    instant = normalize_utc(now or datetime.now(UTC), name="now")
    decision_at = normalize_utc(draft.decision_at, name="decision_at")
    market_cutoff = normalize_utc(draft.market_cutoff_at, name="market_cutoff_at")
    if decision_at > instant:
        raise ForecastPublicationError("decision cutoff cannot be in the future")
    if market_cutoff > decision_at:
        raise ForecastPublicationError("market cutoff cannot exceed decision cutoff")
    _validate_probability_vector(draft.baseline_probabilities, "baseline_probabilities")
    _validate_probability_vector(draft.joint_probabilities, "joint_probabilities")
    _validate_probability_vector(draft.decision_probabilities, "decision_probabilities", tolerance=1e-6)
    if draft.model_status not in {"research_only", "experimental_joint", "experimental_jev"}:
        raise ForecastPublicationError("unsupported model status")
    if draft.model_status == "research_only" and draft.joint_probabilities is not None:
        raise ForecastPublicationError("research_only cannot publish joint probabilities")
    if draft.model_status == "experimental_joint" and draft.joint_probabilities is None:
        raise ForecastPublicationError("experimental_joint requires joint probabilities")
    if draft.model_status == "research_only" and draft.decision_probabilities is not None:
        raise ForecastPublicationError("research_only cannot publish decision probabilities")
    validated_brief = None
    if draft.model_status == "experimental_jev":
        if draft.decision_probabilities is None or draft.research_brief is None:
            raise ForecastPublicationError("experimental_jev requires decision probabilities and a research brief")
        if draft.joint_probabilities is not None or draft.baseline_probabilities is not None:
            raise ForecastPublicationError("experimental_jev cannot populate legacy probability fields")
        try:
            from .research_brief import ResearchBriefError, validate_research_brief
            validated_brief = validate_research_brief(draft.research_brief)
        except ResearchBriefError as exc:
            raise ForecastPublicationError("experimental_jev research brief is invalid", code=exc.code) from exc
        if validated_brief.decision_at != decision_at:
            raise ForecastPublicationError("research brief decision cutoff does not match the forecast")
        market_as_of = validated_brief.market_summary.get("as_of")
        try:
            brief_market_cutoff = normalize_utc(
                market_as_of if isinstance(market_as_of, datetime) else datetime.fromisoformat(str(market_as_of).replace("Z", "+00:00")),
                name="research brief market cutoff",
            )
        except (TypeError, ValueError):
            raise ForecastPublicationError("research brief is missing its market cutoff") from None
        if brief_market_cutoff != market_cutoff:
            raise ForecastPublicationError("research brief market cutoff does not match the forecast")
        if validated_brief.input_quality.status == "insufficient":
            raise ForecastPublicationError("experimental_jev cannot publish an insufficient research brief")
    elif draft.decision_probabilities is not None:
        raise ForecastPublicationError("decision probabilities are reserved for experimental_jev")

    try:
        canonical_contract = target_contract_from_dict(draft.target_contract).as_dict()
    except ForecastContractError as exc:
        raise ForecastPublicationError(str(exc), code=exc.code) from exc
    if validated_brief is not None:
        draft_job = db.get(ForecastJobV2, job_id)
        if draft_job is None:
            raise ForecastPublicationError("job not found", code="not_found")
        if validated_brief.symbol != draft_job.symbol or validated_brief.target_contract != canonical_contract:
            raise ForecastPublicationError("experimental_jev brief does not match its forecast")
    contract_hash = _digest(canonical_contract)
    input_fingerprint = _digest(
        {
            "target": contract_hash,
            "market": draft.price_input_manifest,
            "evidence": draft.evidence_version_manifest,
            "features": draft.feature_snapshot,
            "model": draft.model_manifest,
            "research_brief": draft.research_brief,
        }
    )
    job = db.get(ForecastJobV2, job_id, with_for_update=True)
    if job is None:
        db.rollback()
        raise ForecastPublicationError("job not found", code="not_found")
    if job.status in {"succeeded", "succeeded_no_change"} and job.result_version_id is not None:
        existing = db.get(ForecastVersionV2, job.result_version_id)
        db.commit()
        if existing is None:
            raise ForecastPublicationError("published job points to a missing version")
        return existing, False
    if not (
        job.status == "running"
        and job.lease_owner == worker_id
        and job.lease_epoch == lease_epoch
        and job.lease_expires_at is not None
        and job.lease_expires_at > instant
    ):
        db.rollback()
        raise ForecastPublicationError("worker lease expired or superseded", code="stale_lease")

    frozen_time_mode = draft.model_manifest.get("time_mode") if isinstance(draft.model_manifest, dict) else None
    if frozen_time_mode is None and isinstance(draft.research_report, dict):
        frozen_time_mode = draft.research_report.get("time_mode")
    if frozen_time_mode is None and job.time_mode == "observed":
        frozen_time_mode = "observed"
    if frozen_time_mode != job.time_mode:
        db.rollback()
        raise ForecastPublicationError("forecast time mode does not match its durable job", code="time_mode_mismatch")
    if job.time_mode == "historical_research":
        requested_cutoff = normalize_utc(job.requested_decision_at, name="requested_decision_at")
        if requested_cutoff != decision_at:
            db.rollback()
            raise ForecastPublicationError("historical replay decision cutoff changed", code="time_mode_mismatch")

    target_end = _target_end(canonical_contract)
    target_closes_at = xnys_session_close_at(target_end)
    if job.time_mode == "historical_research":
        if decision_at >= target_closes_at:
            db.rollback()
            raise ForecastPublicationError("historical decision cutoff is not before its target", code="target_expired")
        if instant < target_closes_at:
            db.rollback()
            raise ForecastPublicationError("historical replay target has not matured", code="target_not_matured")
    elif decision_at >= target_closes_at:
        db.rollback()
        raise ForecastPublicationError("target has already expired", code="target_expired")

    root: ForecastVersionV2 | None = None
    parent: ForecastVersionV2 | None = None
    if job.kind != "new":
        root = db.scalar(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.id == job.root_version_id)
            .with_for_update()
        )
        parent = db.get(ForecastVersionV2, job.parent_version_id)
        if root is None or parent is None or root.root_id != root.id:
            db.rollback()
            raise ForecastPublicationError("revision root or parent is missing", code="invalid_parent")
        if parent.root_id != root.id or parent.symbol != job.symbol:
            db.rollback()
            raise ForecastPublicationError("revision parent is incompatible", code="invalid_parent")
        if root.target_contract_hash != contract_hash or root.target_contract != canonical_contract:
            db.rollback()
            raise ForecastPublicationError("revision changed the fixed target", code="target_mismatch")
        if job.kind == "automatic_revision" and instant > root.decision_at + timedelta(hours=72):
            db.rollback()
            raise ForecastPublicationError("automatic revision window expired", code="auto_window_expired")
        existing = db.scalar(
            select(ForecastVersionV2)
            .join(ForecastJobV2, ForecastVersionV2.job_id == ForecastJobV2.id)
            .where(
                ForecastVersionV2.root_id == root.id,
                ForecastJobV2.input_fingerprint == input_fingerprint,
            )
            .limit(1)
        )
        if existing is not None:
            job.status = "succeeded_no_change"
            job.current_stage = "succeeded_no_change"
            job.input_fingerprint = input_fingerprint
            job.result_version_id = existing.id
            job.completed_at = instant
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_type = None
            job.error_message = None
            db.commit()
            return existing, False
        latest = db.scalar(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.root_id == root.id)
            .order_by(ForecastVersionV2.version_no.desc())
            .limit(1)
        )
        if job.kind == "automatic_revision" and latest is not None and latest.id != parent.id:
            db.rollback()
            raise ForecastPublicationError("a newer version needs to be included", code="stale_parent")
        version_no = (latest.version_no if latest else 1) + 1
        root_id = root.id
    else:
        if job.root_version_id is not None or job.parent_version_id is not None:
            db.rollback()
            raise ForecastPublicationError("new job cannot name a parent")
        version_no = 1
        root_id = uuid4()

    version = ForecastVersionV2(
        id=root_id if root is None else uuid4(),
        root_id=root_id,
        parent_version_id=parent.id if parent else None,
        job_id=job.id,
        version_no=version_no,
        symbol=job.symbol,
        target_contract=canonical_contract,
        target_contract_hash=contract_hash,
        decision_at=decision_at,
        market_cutoff_at=market_cutoff,
        price_input_manifest=draft.price_input_manifest,
        evidence_version_manifest=draft.evidence_version_manifest,
        feature_snapshot=draft.feature_snapshot,
        baseline_probabilities=draft.baseline_probabilities,
        joint_probabilities=draft.joint_probabilities,
        model_status=draft.model_status,
        model_manifest=draft.model_manifest,
        research_report=draft.research_report,
        decision_probabilities=draft.decision_probabilities,
        research_brief=draft.research_brief,
        change_reason=draft.change_reason,
        trigger_type=draft.trigger_type,
    )
    db.add(version)
    db.flush()
    job.status = "succeeded"
    job.current_stage = "succeeded"
    job.input_fingerprint = input_fingerprint
    job.model_fingerprint = _digest(draft.model_manifest)
    job.result_version_id = version.id
    job.completed_at = instant
    job.lease_owner = None
    job.lease_expires_at = None
    job.error_type = None
    job.error_message = None
    db.commit()
    db.refresh(version)
    return version, True


def _target_end(contract: dict[str, Any]) -> date:
    raw = contract.get("target_end_date")
    try:
        return raw if isinstance(raw, date) else date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ForecastPublicationError("target contract requires target_end_date") from exc


def _validate_probability_vector(vector: dict[str, float] | None, name: str, *, tolerance: float = 1e-8) -> None:
    if vector is None:
        return
    if set(vector) != {"bearish", "neutral", "bullish"}:
        raise ForecastPublicationError(f"{name} must have bearish, neutral, bullish")
    values = list(vector.values())
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) and 0 <= value <= 1 for value in values):
        raise ForecastPublicationError(f"{name} contains invalid probabilities")
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=tolerance):
        raise ForecastPublicationError(f"{name} probabilities must sum to one")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
