"""Read-only catalog and durable-job API for material analysis."""

from __future__ import annotations

from uuid import UUID

from urllib.parse import quote
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .database import SessionLocal
from .material_analysis import (
    MaterialAnalysisError,
    analysis_history,
    analysis_settings_summary,
    clear_analysis_settings,
    get_job,
    get_material_download,
    get_material_analysis,
    get_material_original,
    list_materials,
    request_material_analysis,
    save_analysis_settings,
)
from .model_providers import PROVIDER_CATALOG


router = APIRouter()


class AnalysisJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=128)
    force: bool = False
    provider: str | None = Field(default=None, max_length=32)
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=160)
    api_key: SecretStr | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _check_model_override(self):
        supplied = (self.provider, self.base_url, self.model, self.api_key)
        if any(value is not None for value in supplied):
            if not self.provider or not self.model or self.api_key is None:
                raise ValueError("provider, model, and api_key are required together")
            if self.provider not in PROVIDER_CATALOG:
                raise ValueError("unsupported model provider")
            if self.provider == "custom" and not self.base_url:
                raise ValueError("custom provider requires a base_url")
            key = self.api_key.get_secret_value()
            if not 8 <= len(key) <= 2048 or key.strip() != key:
                raise ValueError("api_key must be a trimmed string of 8 to 2048 characters")
        return self


class AnalysisSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=32)
    base_url: str | None = Field(default=None, max_length=2048)
    model: str = Field(min_length=1, max_length=160)
    api_key: SecretStr | None = Field(default=None, max_length=2048)


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _require_schema(db: Session) -> None:
    inspector = inspect(db.get_bind())
    required = (
        "material_analysis_jobs", "material_analysis_versions",
        "material_analysis_job_model_configs", "material_analysis_settings",
    )
    if not all(inspector.has_table(name) for name in required):
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
        return request_material_analysis(
            db=db, source_type=source_type, source_id=source_id,
            idempotency_key=payload.idempotency_key, force=payload.force,
            provider=payload.provider, base_url=payload.base_url, model=payload.model,
            api_key=payload.api_key.get_secret_value() if payload.api_key else None,
        )
    except MaterialAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/v3/materials/analysis-settings")
def material_analysis_settings(db: Session = Depends(_db)):
    _require_schema(db)
    return analysis_settings_summary(db)


@router.put("/v3/materials/analysis-settings")
def save_material_analysis_settings(payload: AnalysisSettingsRequest, db: Session = Depends(_db)):
    _require_schema(db)
    try:
        return save_analysis_settings(
            db=db, provider=payload.provider, base_url=payload.base_url, model=payload.model,
            api_key=payload.api_key.get_secret_value() if payload.api_key else None,
        )
    except MaterialAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.delete("/v3/materials/analysis-settings")
def reset_material_analysis_settings(db: Session = Depends(_db)):
    _require_schema(db)
    return clear_analysis_settings(db)


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


@router.get("/v3/materials/{source_type}/{source_id}/original")
def material_original(source_type: str, source_id: UUID, db: Session = Depends(_db)):
    try:
        return get_material_original(db=db, source_type=source_type, source_id=source_id)
    except MaterialAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/v3/materials/{source_type}/{source_id}/download")
def material_download(source_type: str, source_id: UUID, db: Session = Depends(_db)):
    try:
        content, media_type, filename = get_material_download(db=db, source_type=source_type, source_id=source_id)
    except MaterialAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    safe_filename = "".join("_" if unicodedata.category(char) == "Cc" or char in "\\/" else char for char in filename)
    safe_filename = safe_filename.replace('"', "_").strip() or "material"
    safe_ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", safe_filename).replace('"', "_") or "material"
    disposition = f'attachment; filename="{safe_ascii_name}"; filename*=UTF-8\'\'{quote(safe_filename, safe="")}'
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": disposition,
        "X-Content-Type-Options": "nosniff",
    })
