from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
import app.localization_api as localization_api


@pytest.fixture(autouse=True)
def localization_schema(disposable_database):
    Base.metadata.create_all(bind=engine)


def _test_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    test_app = FastAPI()
    test_app.include_router(localization_api.router)
    test_app.dependency_overrides[localization_api._db] = _test_db
    with TestClient(test_app) as test_client:
        yield test_client


def test_material_analysis_localization_route_returns_cached_overlay(client, monkeypatch):
    analysis_id = uuid4()
    monkeypatch.setattr(localization_api, "localize_ai_content", lambda *args, **kwargs: {
        "content_kind": "material_analysis", "content_id": str(analysis_id), "locale": "en-US",
        "source_sha256": "a" * 64, "prompt_version": "v1", "cache_hit": True,
        "fields": {"/summary": "Revenue grew."},
    })

    response = client.post(f"/v3/material-analyses/{analysis_id}/localization")

    assert response.status_code == 200
    assert response.json()["fields"] == {"/summary": "Revenue grew."}


def test_forecast_brief_localization_route_uses_forecast_version_id(client, monkeypatch):
    version_id = uuid4()
    calls = []

    def fake_localize(*args, **kwargs):
        calls.append(kwargs)
        return {"content_kind": "forecast_brief", "content_id": str(version_id), "locale": "en-US",
                "source_sha256": "b" * 64, "prompt_version": "v1", "cache_hit": False,
                "fields": {"/supporting/0/statement": "Cloud revenue rose."}}

    monkeypatch.setattr(localization_api, "localize_ai_content", fake_localize)

    response = client.post(f"/v2/forecast-versions/{version_id}/brief-localization")

    assert response.status_code == 200
    assert response.json()["content_kind"] == "forecast_brief"
    assert calls[0]["content_id"] == version_id
