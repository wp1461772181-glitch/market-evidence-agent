"""HTTP admission and read views for durable V2 forecast jobs.

Routes in this module only validate, persist, and read work.  The V2 worker is
the only component allowed to fetch market data, read evidence, call an LLM,
or publish a forecast version.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import SessionLocal
from .forecast_jobs import ForecastJobError, enqueue_job
from .forecast_v2_models import ForecastJobV2, ForecastVersionV2
from .market_time import xnys_session_close_at
from .models import SecFilingInventory, UploadedEvidence
from .services import normalize_symbol


router = APIRouter(prefix="/v2", tags=["forecast-v2"])


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


@router.get("/stocks/{symbol}/workspace")
def get_v2_stock_workspace(symbol: str, db: Session = Depends(get_v2_db)) -> dict[str, Any]:
    """Return a deliberately small, explicit read model for the V2 workspace."""
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
                "monitor_status": "not_recorded",
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
            "monitor_status": "not_recorded",
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


def _uuid_or_none(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V2RequestError("stored V2 time must include a timezone", status_code=status.HTTP_409_CONFLICT)
    return value.astimezone(UTC)
