"""Read-only catalog and durable-job API for material analysis."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .database import SessionLocal
from .material_analysis import (
    MaterialAnalysisError,
    analysis_history,
    get_job,
    get_material_analysis,
    list_materials,
    request_material_analysis,
)


router = APIRouter()


class AnalysisJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=128)
    force: bool = False


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _require_schema(db: Session) -> None:
    inspector = inspect(db.get_bind())
    if not inspector.has_table("material_analysis_jobs") or not inspector.has_table("material_analysis_versions"):
        raise HTTPException(status_code=503, detail={"code": "migration_required", "message": "Material analysis schema is not applied; run scripts/migrate_material_analysis.py --apply."})


@router.get("/v3/materials")
def materials(
    symbol: str | None = None, source_type: str | None = None, analysis_status: str | None = None,
    review_status: str | None = None, limit: int = 10, offset: int = 0, db: Session = Depends(_db),
):
    _require_schema(db)
    try:
        return list_materials(db=db, symbol=symbol, source_type=source_type, analysis_status=analysis_status,
                              review_status=review_status, limit=limit, offset=offset)
    except MaterialAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/v3/materials/{source_type}/{source_id}/analysis-jobs", status_code=status.HTTP_202_ACCEPTED)
def create_analysis_job(source_type: str, source_id: UUID, payload: AnalysisJobRequest, db: Session = Depends(_db)):
    _require_schema(db)
    try:
        return request_material_analysis(db=db, source_type=source_type, source_id=source_id,
                                         idempotency_key=payload.idempotency_key, force=payload.force)
    except MaterialAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/v3/material-analysis-jobs/{job_id}")
def material_analysis_job(job_id: UUID, db: Session = Depends(_db)):
    _require_schema(db)
    result = get_job(db=db, job_id=job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="material analysis job not found")
    return result


@router.get("/v3/materials/{source_type}/{source_id}/analyses")
def material_analysis_history(source_type: str, source_id: UUID, db: Session = Depends(_db)):
    _require_schema(db)
    if source_type not in {"official_filing", "uploaded_media"}:
        raise HTTPException(status_code=422, detail="source_type must be official_filing or uploaded_media")
    # Distinguish an empty history from a nonexistent material ID.
    from .material_analysis import _load_source
    try:
        _load_source(db, source_type, source_id)
    except MaterialAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    return {"source_type": source_type, "source_id": str(source_id),
            "items": analysis_history(db=db, source_type=source_type, source_id=source_id)}


@router.get("/v3/material-analyses/{analysis_id}")
def material_analysis_detail(analysis_id: UUID, db: Session = Depends(_db)):
    _require_schema(db)
    result = get_material_analysis(db=db, analysis_id=analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="material analysis not found")
    return result
