"""HTTP admission and read views for durable V2 forecast jobs.

Routes in this module only validate, persist, and read work.  The V2 worker is
the only component allowed to fetch market data, read evidence, call an LLM,
or publish a forecast version.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import math
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import SessionLocal
from .forecast_jobs import ForecastJobError, enqueue_job
from .forecast_v2_models import ForecastEvaluationV2, ForecastJobV2, ForecastVersionV2, OfficialMonitorRunV2
from .market_time import xnys_session_close_at
from .models import SecFilingInventory, UploadedEvidence
from .services import normalize_symbol


router = APIRouter(prefix="/v2", tags=["forecast-v2"])
FORECAST_ROOT_LIST_LIMIT = 50
MONITOR_INTERVAL_SECONDS = 60 * 60
MONITOR_STALE_AFTER_SECONDS = MONITOR_INTERVAL_SECONDS * 2
# Scores from revisions share a target with their root.  Keep the threshold
# explicit so one mature root is never represented as a usable forward result.
MINIMUM_SCORED_ROOTS = 5


class SourceRefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["official_filing", "uploaded_media"]
    source_id: UUID


class NewForecastJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=10)
    kind: Literal["new"] = "new"
    source_refs: list[SourceRefRequest] = Field(default_factory=list, max_length=10)


class ManualRevisionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_refs: list[SourceRefRequest] = Field(min_length=1, max_length=10)


class V2RequestError(ValueError):
    def __init__(self, message: str, *, status_code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> None:
        super().__init__(message)
        self.status_code = status_code


def get_v2_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/forecast-jobs", status_code=status.HTTP_202_ACCEPTED)
def create_v2_forecast_job(
    payload: NewForecastJobRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    db: Session = Depends(get_v2_db),
) -> dict[str, Any]:
    """Durably accept one new V2 request without executing it in HTTP."""
    now = datetime.now(UTC)
    try:
        symbol = _supported_symbol(payload.symbol)
        refs = _source_ref_payload(payload.source_refs)
        _validate_source_refs(db, refs=refs, symbol=symbol, decision_at=now)
        job = enqueue_job(
            db=db,
            symbol=symbol,
            kind="new",
            idempotency_key=idempotency_key,
            source_refs=refs,
        )
        return _job_payload(job)
    except V2RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ForecastJobError as exc:
        code = status.HTTP_409_CONFLICT if exc.code == "conflict" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.post("/forecast-versions/{version_id}/revision-jobs", status_code=status.HTTP_202_ACCEPTED)
def create_v2_manual_revision_job(
    version_id: UUID,
    payload: ManualRevisionJobRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    db: Session = Depends(get_v2_db),
) -> dict[str, Any]:
    """Queue a manual child branch for one selected, still-open V2 version."""
    now = datetime.now(UTC)
    try:
        parent = db.get(ForecastVersionV2, version_id)
        if parent is None:
            raise V2RequestError("forecast version was not found", status_code=status.HTTP_404_NOT_FOUND)
        root = db.get(ForecastVersionV2, parent.root_id)
        if root is None or root.root_id != root.id or parent.symbol != root.symbol:
            raise V2RequestError("forecast version has an incompatible root", status_code=status.HTTP_409_CONFLICT)
        _require_open_target(root, now)
        refs = _source_ref_payload(payload.source_refs)
        _validate_source_refs(
            db,
            refs=refs,
            symbol=parent.symbol,
            decision_at=now,
            newer_than=parent.decision_at,
        )
        job = enqueue_job(
            db=db,
            symbol=parent.symbol,
            kind="manual_revision",
            idempotency_key=idempotency_key,
            source_refs=refs,
            root_version_id=root.id,
            parent_version_id=parent.id,
        )
        return _job_payload(job)
    except V2RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ForecastJobError as exc:
        code = status.HTTP_409_CONFLICT if exc.code == "conflict" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.get("/jobs/{job_id}")
def get_v2_job(job_id: UUID, db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    try:
        job = db.get(ForecastJobV2, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="V2 forecast job was not found")
        return _job_payload(job)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.get("/forecast-versions/{version_id}")
def get_v2_forecast_version(version_id: UUID, db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    try:
        version = db.get(ForecastVersionV2, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="V2 forecast version was not found")
        return _version_payload(version)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.get("/forecast-roots/{root_id}/timeline")
def get_v2_forecast_timeline(root_id: UUID, db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    try:
        root = db.get(ForecastVersionV2, root_id)
        if root is None or root.root_id != root.id:
            raise HTTPException(status_code=404, detail="V2 forecast root was not found")
        versions = db.scalars(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.root_id == root.id)
            .order_by(ForecastVersionV2.version_no, ForecastVersionV2.created_at, ForecastVersionV2.id)
        ).all()
        return {
            "root_id": str(root.id),
            "symbol": root.symbol,
            "target_contract": root.target_contract,
            "versions": [_timeline_entry(version) for version in versions],
        }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.get("/monitor/status")
def get_v2_monitor_status(db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    """Expose persisted monitor state; process presence is never treated as health."""
    try:
        return _monitor_status_payload(db=db, now=datetime.now(UTC))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.get("/evaluations")
def get_v2_evaluations(symbol: str, db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    """Read persisted V2 evaluation history without fetching prices or writing rows.

    Forecast roots are the statistical unit. A root can have several revision
    versions for one fixed target, so version-level scores are returned for
    audit while each root appears only once in a cohort denominator.
    """
    try:
        normalized = _supported_symbol(symbol)
        versions = db.scalars(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.symbol == normalized)
            .order_by(ForecastVersionV2.root_id, ForecastVersionV2.version_no, ForecastVersionV2.created_at)
        ).all()
        evaluations = db.scalars(
            select(ForecastEvaluationV2)
            .join(ForecastVersionV2, ForecastEvaluationV2.forecast_version_id == ForecastVersionV2.id)
            .where(ForecastVersionV2.symbol == normalized)
            .order_by(
                ForecastEvaluationV2.forecast_version_id,
                ForecastEvaluationV2.result_version.desc(),
                ForecastEvaluationV2.created_at.desc(),
                ForecastEvaluationV2.id.desc(),
            )
        ).all()
        return _evaluations_payload(symbol=normalized, versions=versions, evaluations=evaluations)
    except V2RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.get("/stocks/{symbol}/forecast-roots")
def list_v2_forecast_roots(symbol: str, db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    """List compact root choices for selecting a manual-revision parent.

    The workspace endpoint intentionally returns only one current root.  This
    separate read view keeps prior forecast dates selectable without copying
    their evidence, market, or research manifests into the browser response.
    """
    now = datetime.now(UTC)
    try:
        normalized = _supported_symbol(symbol)
        roots = db.scalars(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.symbol == normalized, ForecastVersionV2.root_id == ForecastVersionV2.id)
            .order_by(ForecastVersionV2.decision_at.desc(), ForecastVersionV2.created_at.desc(), ForecastVersionV2.id.desc())
            .limit(FORECAST_ROOT_LIST_LIMIT)
        ).all()
        return {
            "symbol": normalized,
            "roots": [_root_list_entry(db=db, root=root, now=now) for root in roots],
            "limit": FORECAST_ROOT_LIST_LIMIT,
        }
    except V2RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


@router.get("/stocks/{symbol}/workspace")
def get_v2_stock_workspace(symbol: str, db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    """Return a deliberately small, explicit read model for the V2 workspace."""
    now = datetime.now(UTC)
    try:
        normalized = _supported_symbol(symbol)
        root = db.scalar(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.symbol == normalized, ForecastVersionV2.root_id == ForecastVersionV2.id)
            .order_by(ForecastVersionV2.created_at.desc(), ForecastVersionV2.id.desc())
            .limit(1)
        )
        pending_jobs = db.scalar(
            select(func.count())
            .select_from(ForecastJobV2)
            .where(ForecastJobV2.symbol == normalized, ForecastJobV2.status.in_(("queued", "running")))
        ) or 0
        if root is None:
            return {
                "symbol": normalized,
                "status": "empty",
                "current_root": None,
                "current_version": None,
                "pending_job_count": int(pending_jobs),
                "joint_model_status": "unavailable",
                "monitor_status": _monitor_status_payload(db=db, now=now)["health"],
            }
        current = db.scalar(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.root_id == root.id)
            .order_by(ForecastVersionV2.version_no.desc(), ForecastVersionV2.created_at.desc(), ForecastVersionV2.id.desc())
            .limit(1)
        )
        if current is None:  # pragma: no cover - root was returned by this table.
            raise HTTPException(status_code=500, detail="V2 forecast root has no version")
        return {
            "symbol": normalized,
            "status": "available",
            "current_root": {"id": str(root.id), "target_contract": root.target_contract},
            "current_version": _workspace_version(current),
            "pending_job_count": int(pending_jobs),
            "joint_model_status": current.model_status,
            "monitor_status": _monitor_status_payload(db=db, now=now)["health"],
        }
    except V2RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="V2 job store is unavailable") from exc


def _supported_symbol(value: str) -> str:
    normalized = normalize_symbol(value)
    if normalized not in {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"}:
        raise V2RequestError("symbol is not supported for V2 forecasting")
    return normalized


def _source_ref_payload(refs: list[SourceRefRequest]) -> list[dict[str, str]]:
    return [{"source_type": ref.source_type, "source_id": str(ref.source_id)} for ref in refs]


def _validate_source_refs(
    db: Session,
    *,
    refs: list[dict[str, str]],
    symbol: str,
    decision_at: datetime,
    newer_than: datetime | None = None,
) -> None:
    if len({(item["source_type"], item["source_id"]) for item in refs}) != len(refs):
        raise V2RequestError("source_refs must not repeat a source")
    for ref in refs:
        source_type = ref["source_type"]
        source_id = UUID(ref["source_id"])
        if source_type == "official_filing":
            source = db.get(SecFilingInventory, source_id)
            if source is None:
                raise V2RequestError("official filing source was not found", status_code=status.HTTP_404_NOT_FOUND)
            public_at = _filing_public_at(source)
            observed_at = source.content_observed_at or source.observed_at
        else:
            source = db.get(UploadedEvidence, source_id)
            if source is None:
                raise V2RequestError("uploaded media source was not found", status_code=status.HTTP_404_NOT_FOUND)
            public_at = source.published_at
            observed_at = source.observed_at
        if source.symbol != symbol:
            raise V2RequestError("source does not belong to the forecast symbol")
        if public_at > decision_at or observed_at > decision_at:
            raise V2RequestError("source is not available at this decision time")
        if newer_than is not None and public_at <= _utc(newer_than):
            raise V2RequestError("manual revision source must be published after the selected forecast")


def _filing_public_at(source: SecFilingInventory) -> datetime:
    raw = source.accepted_at
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
    # The legacy inventory accepts compact SEC timestamps, but those values
    # contain no offset.  V2's point-in-time contract must not silently label
    # them UTC and then admit a source in the wrong decision window.
    raise V2RequestError("official filing requires an exact timezone-aware SEC acceptance timestamp")


def _require_open_target(version: ForecastVersionV2, now: datetime) -> None:
    raw = version.target_contract.get("target_end_date") if isinstance(version.target_contract, dict) else None
    try:
        target_end = raw if isinstance(raw, date) else date.fromisoformat(raw)
        expired = _utc(now) >= xnys_session_close_at(target_end)
    except (TypeError, ValueError) as exc:
        raise V2RequestError("forecast version has an invalid target contract", status_code=status.HTTP_409_CONFLICT) from exc
    if expired:
        raise V2RequestError("forecast target has expired; create an evaluation instead", status_code=status.HTTP_409_CONFLICT)


def _job_payload(job: ForecastJobV2) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "symbol": job.symbol,
        "kind": job.kind,
        "root_version_id": _uuid_or_none(job.root_version_id),
        "parent_version_id": _uuid_or_none(job.parent_version_id),
        "source_refs": job.source_refs,
        "status": job.status,
        "current_stage": job.current_stage,
        "attempts": job.attempts,
        # Worker diagnostics can contain provider/database detail.  The API
        # exposes the stable classification needed by the workspace, never a
        # raw stored exception or traceback.
        "error": {"type": job.error_type} if job.error_type else None,
        "result_version_id": _uuid_or_none(job.result_version_id),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


def _version_payload(version: ForecastVersionV2) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "root_id": str(version.root_id),
        "parent_version_id": _uuid_or_none(version.parent_version_id),
        "job_id": str(version.job_id),
        "version_no": version.version_no,
        "symbol": version.symbol,
        "target_contract": version.target_contract,
        "decision_at": version.decision_at,
        "market_cutoff_at": version.market_cutoff_at,
        "price_input_manifest": version.price_input_manifest,
        "evidence_version_manifest": version.evidence_version_manifest,
        "feature_snapshot": version.feature_snapshot,
        "baseline_probabilities": version.baseline_probabilities,
        "joint_probabilities": version.joint_probabilities,
        "model_status": version.model_status,
        "model_manifest": version.model_manifest,
        "research_report": version.research_report,
        "change_reason": version.change_reason,
        "trigger_type": version.trigger_type,
        "created_at": version.created_at,
    }


def _timeline_entry(version: ForecastVersionV2) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "parent_version_id": _uuid_or_none(version.parent_version_id),
        "version_no": version.version_no,
        "decision_at": version.decision_at,
        "market_cutoff_at": version.market_cutoff_at,
        "baseline_probabilities": version.baseline_probabilities,
        "joint_probabilities": version.joint_probabilities,
        "model_status": version.model_status,
        "change_reason": version.change_reason,
        "trigger_type": version.trigger_type,
        "created_at": version.created_at,
    }


def _workspace_version(version: ForecastVersionV2) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "version_no": version.version_no,
        "decision_at": version.decision_at,
        "market_cutoff_at": version.market_cutoff_at,
        "model_status": version.model_status,
        "baseline_probabilities": version.baseline_probabilities,
        "joint_probabilities": version.joint_probabilities,
    }


def _evaluations_payload(
    *,
    symbol: str,
    versions: list[ForecastVersionV2],
    evaluations: list[ForecastEvaluationV2],
) -> dict[str, Any]:
    history_by_version: dict[UUID, list[ForecastEvaluationV2]] = {}
    for evaluation in evaluations:
        history_by_version.setdefault(evaluation.forecast_version_id, []).append(evaluation)

    roots_by_cohort: dict[str, dict[UUID, dict[str, Any]]] = {
        "prospective": {},
        "historical_research": {},
        "unknown": {},
    }
    for version in versions:
        time_mode = _persisted_time_mode(version)
        cohort = _evaluation_cohort(time_mode)
        root = roots_by_cohort[cohort].setdefault(
            version.root_id,
            {
                "root_id": str(version.root_id),
                "target_contract_hash": version.target_contract_hash,
                "target_end_date": _target_end_date_or_none(version),
                "versions": [],
            },
        )
        history = history_by_version.get(version.id, [])
        root["versions"].append(
            {
                "id": str(version.id),
                "version_no": version.version_no,
                "decision_at": version.decision_at,
                "trigger_type": version.trigger_type,
                "model_status": version.model_status,
                "time_mode": time_mode,
                "latest_evaluation": _evaluation_payload(history[0]) if history else None,
                "evaluation_history": [_evaluation_payload(item) for item in history],
            }
        )

    cohorts = {
        name: _evaluation_cohort_payload(name=name, roots=roots)
        for name, roots in roots_by_cohort.items()
    }
    return {
        "symbol": symbol,
        # Only observed-time forecasts are candidates for prospective results.
        # Historical and unknown rows remain separately inspectable.
        "status": cohorts["prospective"]["status"],
        "minimum_scored_roots": MINIMUM_SCORED_ROOTS,
        "cohorts": cohorts,
    }


def _evaluation_cohort_payload(*, name: str, roots: dict[UUID, dict[str, Any]]) -> dict[str, Any]:
    ordered_roots = sorted(
        roots.values(),
        key=lambda root: (
            root["versions"][0]["decision_at"],
            root["root_id"],
        ),
        reverse=True,
    )
    scored_roots = 0
    labelled_roots = 0
    version_count = 0
    scored_version_count = 0
    labelled_version_count = 0
    for root in ordered_roots:
        root_has_score = False
        root_has_label = False
        for version in root["versions"]:
            version_count += 1
            latest = version["latest_evaluation"]
            if latest is not None and latest["status"] == "succeeded" and latest["actual_label"] is not None:
                labelled_version_count += 1
                root_has_label = True
            if _has_numeric_scores(latest):
                scored_version_count += 1
                root_has_score = True
        if root_has_label:
            labelled_roots += 1
        if root_has_score:
            scored_roots += 1
    if scored_roots == 0:
        summary_status = "pending"
    elif scored_roots < MINIMUM_SCORED_ROOTS:
        summary_status = "insufficient_samples"
    else:
        summary_status = "available"
    return {
        "time_mode": {"prospective": "observed", "historical_research": "historical_research", "unknown": "unknown"}[name],
        "status": summary_status,
        "sample": {
            "root_denominator": len(ordered_roots),
            "labelled_root_count": labelled_roots,
            "scored_root_count": scored_roots,
            "unscored_root_count": len(ordered_roots) - scored_roots,
            "version_count": version_count,
            "labelled_version_count": labelled_version_count,
            "scored_version_count": scored_version_count,
        },
        "roots": ordered_roots,
    }


def _has_numeric_scores(evaluation: dict[str, Any] | None) -> bool:
    if evaluation is None or evaluation["status"] != "succeeded":
        return False
    values = (evaluation["brier_score"], evaluation["log_loss"])
    return all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        for value in values
    )


def _persisted_time_mode(version: ForecastVersionV2) -> str:
    """Classify only an explicitly frozen time-mode declaration.

    Older forecast rows often have no research report. Absence is deliberately
    visible as ``unknown`` instead of being inferred from dates or row age.
    """
    for metadata in (version.research_report, version.model_manifest):
        if isinstance(metadata, dict) and metadata.get("time_mode") in {"observed", "historical_research"}:
            return str(metadata["time_mode"])
    return "unknown"


def _evaluation_cohort(time_mode: str) -> str:
    if time_mode == "observed":
        return "prospective"
    if time_mode == "historical_research":
        return "historical_research"
    return "unknown"


def _target_end_date_or_none(version: ForecastVersionV2) -> str | None:
    raw = version.target_contract.get("target_end_date") if isinstance(version.target_contract, dict) else None
    try:
        return (raw if isinstance(raw, date) else date.fromisoformat(raw)).isoformat()
    except (TypeError, ValueError):
        return None


def _evaluation_payload(evaluation: ForecastEvaluationV2) -> dict[str, Any]:
    return {
        "id": str(evaluation.id),
        "result_version": evaluation.result_version,
        "status": evaluation.status,
        "actual_target_close": evaluation.actual_target_close,
        "actual_label": evaluation.actual_label,
        "label_available_at": evaluation.label_available_at,
        "brier_score": evaluation.brier_score,
        "log_loss": evaluation.log_loss,
        "direction_correct": evaluation.direction_correct,
        "price_input_version": evaluation.price_input_version,
        "created_at": evaluation.created_at,
    }


def _root_list_entry(*, db: Session, root: ForecastVersionV2, now: datetime) -> dict[str, Any]:
    latest = db.scalar(
        select(ForecastVersionV2)
        .where(ForecastVersionV2.root_id == root.id)
        .order_by(ForecastVersionV2.version_no.desc(), ForecastVersionV2.created_at.desc(), ForecastVersionV2.id.desc())
        .limit(1)
    )
    # A root always has itself, but retain a safe partial record rather than
    # turning this read endpoint into a 500 if historical storage is damaged.
    selected = latest or root
    target_end_date, expired = _root_target_end(root, now=now)
    return {
        "id": str(root.id),
        "decision_at": root.decision_at,
        "target_end_date": target_end_date,
        "model_status": selected.model_status,
        "latest_version_id": str(selected.id),
        "latest_version_no": selected.version_no,
        "expired": expired,
    }


def _root_target_end(root: ForecastVersionV2, *, now: datetime) -> tuple[str | None, bool | None]:
    raw = root.target_contract.get("target_end_date") if isinstance(root.target_contract, dict) else None
    try:
        target_end = raw if isinstance(raw, date) else date.fromisoformat(raw)
        return target_end.isoformat(), _utc(now) >= xnys_session_close_at(target_end)
    except (TypeError, ValueError):
        # A malformed legacy target remains visible for audit, but cannot be
        # safely advertised as revisable by the frontend.
        return None, None


def _monitor_status_payload(*, db: Session, now: datetime) -> dict[str, Any]:
    instant = _utc(now)
    latest = db.scalar(
        select(OfficialMonitorRunV2)
        .order_by(OfficialMonitorRunV2.started_at.desc(), OfficialMonitorRunV2.id.desc())
        .limit(1)
    )
    latest_success = db.scalar(
        select(OfficialMonitorRunV2)
        .where(OfficialMonitorRunV2.status == "succeeded", OfficialMonitorRunV2.completed_at.is_not(None))
        .order_by(OfficialMonitorRunV2.completed_at.desc(), OfficialMonitorRunV2.id.desc())
        .limit(1)
    )
    if latest is None:
        return {
            "health": "not_recorded",
            "schedule_interval_seconds": MONITOR_INTERVAL_SECONDS,
            "stale_after_seconds": MONITOR_STALE_AFTER_SECONDS,
            "last_run": None,
            "last_success": None,
        }
    return {
        "health": _monitor_health(latest=latest, latest_success=latest_success, now=instant),
        "schedule_interval_seconds": MONITOR_INTERVAL_SECONDS,
        "stale_after_seconds": MONITOR_STALE_AFTER_SECONDS,
        "last_run": _monitor_run_payload(latest),
        "last_success": _monitor_success_payload(latest_success),
    }


def _monitor_health(
    *, latest: OfficialMonitorRunV2, latest_success: OfficialMonitorRunV2 | None, now: datetime
) -> str:
    if latest.status in {"failed", "partial"} or latest_success is None:
        return "degraded"
    completed_at = latest_success.completed_at
    if completed_at is None or _utc(completed_at) < now - timedelta(seconds=MONITOR_STALE_AFTER_SECONDS):
        return "delayed"
    return "healthy"


def _monitor_run_payload(run: OfficialMonitorRunV2) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "next_due_at": run.next_due_at,
        "per_symbol_results": run.per_symbol_results,
        "error_summary": run.error_summary,
        "retry_reason": run.retry_reason,
        "evaluation_summary": run.evaluation_summary,
    }


def _monitor_success_payload(run: OfficialMonitorRunV2 | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": str(run.id),
        "completed_at": run.completed_at,
        "last_success_watermark": run.last_success_watermark,
    }


def _uuid_or_none(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V2RequestError("stored V2 time must include a timezone", status_code=status.HTTP_409_CONFLICT)
    return value.astimezone(UTC)
