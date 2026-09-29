"""On-demand translation endpoints for immutable AI analysis records."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .ai_content_localization import LocalizationError, localize_ai_content
from .database import SessionLocal
from .event_provider import create_deepseek_provider_from_env


router = APIRouter()


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _require_schema(db: Session, *, source_table: str) -> None:
    inspector = inspect(db.get_bind())
    if not inspector.has_table("ai_content_translations") or not inspector.has_table(source_table):
        raise HTTPException(
            status_code=503,
            detail={"code": "migration_required", "message": "AI content localization schema is not applied; run scripts/migrate_ai_content_translations.py --apply."},
        )


def _localize(db: Session, *, kind: str, content_id: UUID):
    try:
        return localize_ai_content(
            db,
            content_kind=kind,
            content_id=content_id,
            locale="en-US",
            provider_factory=create_deepseek_provider_from_env,
        )
    except LocalizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/v3/material-analyses/{analysis_id}/localization")
def material_analysis_localization(analysis_id: UUID, db: Session = Depends(_db)):
    _require_schema(db, source_table="material_analysis_versions")
    return _localize(db, kind="material_analysis", content_id=analysis_id)


@router.post("/v2/forecast-versions/{version_id}/brief-localization")
def forecast_brief_localization(version_id: UUID, db: Session = Depends(_db)):
    _require_schema(db, source_table="forecast_versions_v2")
    return _localize(db, kind="forecast_brief", content_id=version_id)
