"""The revision-candidate read endpoint: the workspace offers what the revision API accepts.

Every test keeps to a symbol of its own because saved sources outlive the test module.  The
workspace must not decide eligibility by comparing the published parent manifest with the
current material state; the server rule compares the frozen evidence event instead.
"""
from datetime import UTC, date, datetime, timedelta
import hashlib
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.forecast_v2_api as api
from app.database import Base, SessionLocal, engine
from app.evidence_context import freeze_evidence_context
from app.forecast_revision_candidates import REVISION_LOOKAHEAD, REVISION_LOOKBACK
from app.forecast_v2_models import ForecastVersionV2
from app.models import UploadedEvidence

from test_forecast_api_v2 import _create_version, _official_source, _test_db  # noqa: F401
from test_forecast_revision_candidates import forget_symbols


SYMBOLS = ["RWA", "RWB", "RWC", "RWD"]


@pytest.fixture(autouse=True)
def _own_symbols_only(disposable_database):
    Base.metadata.create_all(bind=engine)
    yield
    forget_symbols(SYMBOLS)


@pytest.fixture()
def client():
    test_app = FastAPI()
    test_app.include_router(api.router)
    test_app.dependency_overrides[api.get_v2_db] = _test_db
    with TestClient(test_app) as test_client:
        yield test_client


def _upload(*, symbol, published_at, observed_at):
    raw = f"{symbol} used upload before the forecast".encode()
    with SessionLocal() as db:
        source = UploadedEvidence(
            symbol=symbol, title="Previously used upload", source_url="https://news.example.test/used",
            published_at=published_at, observed_at=observed_at, credibility_stars=4, credibility_reason="fixture",
            impact_severity="medium", filename="used.txt",
            content_sha256=hashlib.sha256(raw).hexdigest(), raw_content=raw, content_text=raw.decode(),
            status="unconfirmed",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source.id


def _freeze_into(version_id, symbol, decided, source_type, source_id):
    with SessionLocal() as db:
        parent = db.get(ForecastVersionV2, version_id)
        parent.evidence_version_manifest = freeze_evidence_context(
            db=db, symbol=symbol, decision_at=decided, mode="observed",
            source_refs=[{"source_type": source_type, "source_id": str(source_id)}],
            include_automatic_new_sources=False,
        ).evidence_manifest()
        db.commit()
        return parent.evidence_version_manifest


def test_inside_the_window_is_a_candidate_and_the_eighth_day_says_why(client):
    decided = datetime.now(UTC) - timedelta(days=12)
    version = _create_version(symbol="RWA", decision_at=decided)
    inside = _official_source(
        symbol="RWA", accepted_at=decided + timedelta(days=6),
        observed_at=decided + timedelta(days=6, hours=1),
    )
    late = _official_source(
        symbol="RWA", accepted_at=decided + timedelta(days=8),
        observed_at=decided + timedelta(days=8, hours=1),
    )

    response = client.get(f"/v2/forecast-versions/{version}/revision-candidates")

    assert response.status_code == 200
    body = response.json()
    assert body["version_id"] == str(version)
    assert body["version_no"] == 1
    assert body["symbol"] == "RWA"
    assert datetime.fromisoformat(body["decision_at"]) == decided
    assert datetime.fromisoformat(body["window_start"]) == decided - REVISION_LOOKBACK
    assert datetime.fromisoformat(body["window_end"]) == decided + REVISION_LOOKAHEAD
    assert body["target_end_date"] == "2027-01-29"
    assert body["expired"] is False
    assert [row["source_id"] for row in body["candidates"]] == [str(inside)]
    candidate = body["candidates"][0]
    assert candidate["source_type"] == "official_filing"
    assert candidate["title"] and candidate["kind"] == "SEC 10-Q"
    assert candidate["category"] == "new"
    assert candidate["badge"] == "预测后发布的新材料"
    assert set(candidate) == {
        "source_type", "source_id", "title", "kind", "published_at", "observed_at", "category", "badge",
    }
    assert [row["source_id"] for row in body["excluded"]] == [str(late)]
    excluded = body["excluded"][0]
    assert excluded["reason"] == "published_after_window"
    assert "7 天" in excluded["reason_text"]
    assert set(excluded) == {
        "source_type", "source_id", "title", "kind", "published_at", "reason", "reason_text",
    }


def test_excluded_keeps_the_newest_three_and_skips_unpublished_material(client):
    decided = datetime.now(UTC) - timedelta(days=20)
    version = _create_version(symbol="RWC", decision_at=decided)
    late = [
        _official_source(
            symbol="RWC", accepted_at=decided + timedelta(days=offset),
            observed_at=decided + timedelta(days=offset, hours=1),
        )
        for offset in (8, 9, 10, 11)
    ]
    future = _official_source(
        symbol="RWC", accepted_at=datetime.now(UTC) + timedelta(days=1),
        observed_at=datetime.now(UTC) + timedelta(days=1),
    )

    response = client.get(f"/v2/forecast-versions/{version}/revision-candidates")

    assert response.status_code == 200
    body = response.json()
    assert body["candidates"] == []
    assert [row["source_id"] for row in body["excluded"]] == [str(source) for source in reversed(late[-3:])]
    assert all(row["reason"] == "published_after_window" for row in body["excluded"])
    assert str(future) not in {row["source_id"] for row in body["excluded"]}


def test_a_used_upload_does_not_look_changed_only_because_the_manifest_rating_is_null(client):
    decided = datetime.now(UTC) - timedelta(hours=3)
    version = _create_version(symbol="RWB", decision_at=decided)
    used = _upload(
        symbol="RWB", published_at=decided - timedelta(days=2), observed_at=decided - timedelta(days=1),
    )
    manifest = _freeze_into(version, "RWB", decided, "uploaded_media", used)
    assert manifest[0]["user_rating_stars"] == 4
    # The workspace used to compare this nullable manifest field with the current
    # credibility_stars; the immutable event still says the material is unchanged.
    with SessionLocal() as db:
        parent = db.get(ForecastVersionV2, version)
        parent.evidence_version_manifest = [
            {**item, "user_rating_stars": None, "user_stars": None} for item in parent.evidence_version_manifest
        ]
        db.commit()

    response = client.get(f"/v2/forecast-versions/{version}/revision-candidates")

    assert response.status_code == 200
    body = response.json()
    assert body["candidates"] == []
    assert {row["source_id"] for row in body["excluded"]} == {str(used)}
    assert body["excluded"][0]["reason"] == "used_unchanged"


def test_an_unknown_version_is_404(client):
    response = client.get(f"/v2/forecast-versions/{uuid4()}/revision-candidates")

    assert response.status_code == 404
    assert response.json()["detail"] == "V2 forecast version was not found"


def test_an_expired_target_offers_no_candidates(client):
    decided = datetime.now(UTC) - timedelta(days=5)
    version = _create_version(
        symbol="RWD", target_end=date.today() - timedelta(days=1), decision_at=decided,
    )
    _official_source(
        symbol="RWD", accepted_at=decided + timedelta(days=1),
        observed_at=decided + timedelta(days=1, hours=1),
    )

    response = client.get(f"/v2/forecast-versions/{version}/revision-candidates")

    assert response.status_code == 200
    body = response.json()
    assert body["target_end_date"] == (date.today() - timedelta(days=1)).isoformat()
    assert body["expired"] is True
    assert body["candidates"] == []
    assert body["excluded"] == []