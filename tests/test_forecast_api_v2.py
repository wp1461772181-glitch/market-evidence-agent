from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.forecast_v2_api as api
from app.database import Base, SessionLocal, engine
from app.forecast_jobs import enqueue_job
from app.forecast_v2_models import ForecastEvaluationV2, ForecastJobV2, ForecastVersionV2, OfficialMonitorRunV2
from app.models import SecFilingInventory, UploadedEvidence
from app.main import app as main_app


@pytest.fixture(autouse=True)
def v2_tables(disposable_database):
    Base.metadata.create_all(bind=engine)
    yield
    # This module creates durable V2 rows through HTTP admission. The shared
    # disposable database is reused by later test modules, including evaluator
    # batches that intentionally inspect every forecast version.
    with SessionLocal() as db:
        db.query(ForecastJobV2).update(
            {
                ForecastJobV2.root_version_id: None,
                ForecastJobV2.parent_version_id: None,
                ForecastJobV2.result_version_id: None,
            },
            synchronize_session=False,
        )
        db.query(ForecastEvaluationV2).delete(synchronize_session=False)
        db.query(ForecastVersionV2).delete(synchronize_session=False)
        db.query(ForecastJobV2).delete(synchronize_session=False)
        db.query(OfficialMonitorRunV2).delete(synchronize_session=False)
        db.commit()


@pytest.fixture()
def client():
    test_app = FastAPI()
    test_app.include_router(api.router)
    test_app.dependency_overrides[api.get_v2_db] = _test_db
    with TestClient(test_app) as test_client:
        yield test_client


def _test_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _create_version(*, symbol="AAPL", target_end=None, decision_at=None):
    target_end = target_end or date(2027, 1, 29)
    decision_at = decision_at or datetime.now(UTC) - timedelta(hours=1)
    with SessionLocal() as db:
        job = enqueue_job(db=db, symbol=symbol, kind="new", idempotency_key=f"api-root-{uuid4()}")
        root = ForecastVersionV2(
            id=uuid4(),
            root_id=uuid4(),
            job_id=job.id,
            version_no=1,
            symbol=symbol,
            target_contract={"target_end_date": target_end.isoformat()},
            target_contract_hash="a" * 64,
            decision_at=decision_at,
            market_cutoff_at=decision_at - timedelta(minutes=5),
            price_input_manifest={}, evidence_version_manifest=[], feature_snapshot={},
            baseline_probabilities={"bearish": 0.2, "neutral": 0.4, "bullish": 0.4},
            joint_probabilities=None, model_status="research_only", model_manifest={}, trigger_type="manual",
        )
        root.root_id = root.id
        db.add(root)
        db.commit()
        db.refresh(root)
        return root.id


def _official_source(*, symbol="AAPL", accepted_at=None, observed_at=None):
    instant = datetime.now(UTC)
    accepted_at = accepted_at or instant - timedelta(minutes=30)
    observed_at = observed_at or instant - timedelta(minutes=20)
    accepted_at_text = accepted_at if isinstance(accepted_at, str) else accepted_at.isoformat()
    with SessionLocal() as db:
        source = SecFilingInventory(
            symbol=symbol, cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
            form="10-Q", filed_at=instant.date(), accepted_at=accepted_at_text,
            primary_document="q.htm", source_url="https://www.sec.gov/Archives/example/q.htm", source="sec-edgar",
            review_status="accepted", human_review_note=None, reviewed_at=None, observed_at=observed_at,
            content_status="fetched", content_observed_at=observed_at, content_excerpt="test", content_excerpt_sha256="a" * 64,
            content_truncated=False, content_error=None,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source.id


def _media_source(*, symbol="AAPL", published_at=None, observed_at=None):
    instant = datetime.now(UTC)
    published_at = published_at or instant - timedelta(minutes=30)
    observed_at = observed_at or instant - timedelta(minutes=20)
    with SessionLocal() as db:
        source = UploadedEvidence(
            symbol=symbol, title="Reported product issue", source_url="https://news.example.test/report",
            published_at=published_at, observed_at=observed_at, credibility_stars=4, credibility_reason="fixture",
            impact_severity="high", filename="report.txt", content_sha256=uuid4().hex.ljust(64, "0"),
            raw_content=b"fixture", content_text="fixture", status="unconfirmed",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source.id


def _clear_monitor_runs():
    with SessionLocal() as db:
        db.query(OfficialMonitorRunV2).delete(synchronize_session=False)
        db.commit()


def _delete_owned_evaluation_fixture_versions(version_ids):
    """Keep API fixtures out of later evaluator batches in the shared test DB."""
    with SessionLocal() as db:
        versions = db.query(ForecastVersionV2).filter(ForecastVersionV2.id.in_(version_ids)).all()
        job_ids = [version.job_id for version in versions]
        db.query(ForecastEvaluationV2).filter(ForecastEvaluationV2.forecast_version_id.in_(version_ids)).delete(
            synchronize_session=False
        )
        db.query(ForecastVersionV2).filter(ForecastVersionV2.id.in_(version_ids)).delete(synchronize_session=False)
        db.query(ForecastJobV2).filter(ForecastJobV2.id.in_(job_ids)).delete(synchronize_session=False)
        db.commit()


def test_new_job_is_durable_idempotent_and_does_not_run_forecast_work(client):
    body = {"symbol": " aapl ", "kind": "new"}
    first = client.post("/v2/forecast-jobs", json=body, headers={"Idempotency-Key": "new-aapl"})
    second = client.post("/v2/forecast-jobs", json=body, headers={"Idempotency-Key": "new-aapl"})

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "queued"
    assert first.json()["result_version_id"] is None
    assert client.get(f"/v2/jobs/{first.json()['id']}").json()["current_stage"] == "queued"

    with SessionLocal() as db:
        assert db.get(ForecastJobV2, first.json()["id"]).status == "queued"


def test_new_job_requires_header_and_rejects_reused_key_with_a_different_request(client):
    missing = client.post("/v2/forecast-jobs", json={"symbol": "AAPL", "kind": "new"})
    assert missing.status_code == 422

    headers = {"Idempotency-Key": "conflicting-key"}
    assert client.post("/v2/forecast-jobs", json={"symbol": "AAPL", "kind": "new"}, headers=headers).status_code == 202
    conflicting = client.post("/v2/forecast-jobs", json={"symbol": "MSFT", "kind": "new"}, headers=headers)
    assert conflicting.status_code == 409


def test_manual_revision_validates_parent_source_symbol_and_source_time(client):
    root_id = _create_version()
    current_source = _official_source()
    accepted = client.post(
        f"/v2/forecast-versions/{root_id}/revision-jobs",
        json={"source_refs": [{"source_type": "official_filing", "source_id": str(current_source)}]},
        headers={"Idempotency-Key": "manual-source"},
    )
    assert accepted.status_code == 202
    assert accepted.json()["kind"] == "manual_revision"
    assert accepted.json()["root_version_id"] == str(root_id)
    assert accepted.json()["parent_version_id"] == str(root_id)

    other_symbol = _media_source(symbol="MSFT")
    cross_stock = client.post(
        f"/v2/forecast-versions/{root_id}/revision-jobs",
        json={"source_refs": [{"source_type": "uploaded_media", "source_id": str(other_symbol)}]},
        headers={"Idempotency-Key": "cross-stock"},
    )
    assert cross_stock.status_code == 422

    old_source = _official_source(accepted_at=datetime.now(UTC) - timedelta(days=2))
    old = client.post(
        f"/v2/forecast-versions/{root_id}/revision-jobs",
        json={"source_refs": [{"source_type": "official_filing", "source_id": str(old_source)}]},
        headers={"Idempotency-Key": "old-source"},
    )
    assert old.status_code == 422


def test_revision_rejects_missing_version_and_expired_target(client):
    missing = client.post(
        f"/v2/forecast-versions/{uuid4()}/revision-jobs",
        json={"source_refs": [{"source_type": "uploaded_media", "source_id": str(uuid4())}]},
        headers={"Idempotency-Key": "missing-version"},
    )
    assert missing.status_code == 404

    expired = _create_version(symbol="MSFT", target_end=date(2025, 1, 2))
    source = _media_source(symbol="MSFT")
    blocked = client.post(
        f"/v2/forecast-versions/{expired}/revision-jobs",
        json={"source_refs": [{"source_type": "uploaded_media", "source_id": str(source)}]},
        headers={"Idempotency-Key": "expired-version"},
    )
    assert blocked.status_code == 409


def test_revision_rejects_ambiguous_compact_sec_timestamp_instead_of_assuming_utc(client):
    root_id = _create_version(symbol="NVDA")
    ambiguous_source = _official_source(symbol="NVDA", accepted_at="20260924140000")

    response = client.post(
        f"/v2/forecast-versions/{root_id}/revision-jobs",
        json={"source_refs": [{"source_type": "official_filing", "source_id": str(ambiguous_source)}]},
        headers={"Idempotency-Key": "ambiguous-sec-time"},
    )

    assert response.status_code == 422
    assert "timezone-aware" in response.json()["detail"]


def test_read_version_timeline_and_explicit_empty_workspace_never_invent_joint_probabilities(client):
    root_id = _create_version(symbol="GOOGL")
    version = client.get(f"/v2/forecast-versions/{root_id}")
    timeline = client.get(f"/v2/forecast-roots/{root_id}/timeline")
    empty = client.get("/v2/stocks/AMZN/workspace")
    available = client.get("/v2/stocks/GOOGL/workspace")

    assert version.status_code == timeline.status_code == empty.status_code == available.status_code == 200
    assert version.json()["joint_probabilities"] is None
    assert timeline.json()["target_contract"] == version.json()["target_contract"]
    assert timeline.json()["versions"][0]["id"] == str(root_id)
    assert empty.json()["status"] == "empty"
    assert empty.json()["joint_model_status"] == "unavailable"
    assert available.json()["current_version"]["joint_probabilities"] is None


def test_root_list_keeps_multiple_forecast_dates_selectable_and_excludes_other_stocks(client):
    old_root = _create_version(
        symbol="AMZN",
        target_end=date(2025, 1, 2),
        decision_at=datetime(2024, 12, 2, 22, tzinfo=UTC),
    )
    newest_root = _create_version(
        symbol="AMZN",
        target_end=date(2027, 1, 29),
        decision_at=datetime(2026, 9, 24, 22, tzinfo=UTC),
    )
    _create_version(symbol="MSFT", target_end=date(2027, 1, 29), decision_at=datetime(2026, 9, 25, 22, tzinfo=UTC))
    with SessionLocal() as db:
        root = db.get(ForecastVersionV2, newest_root)
        child_job = enqueue_job(
            db=db,
            symbol="AMZN",
            kind="manual_revision",
            idempotency_key=f"api-root-list-child-{uuid4()}",
            root_version_id=root.id,
            parent_version_id=root.id,
        )
        child = ForecastVersionV2(
            id=uuid4(),
            root_id=root.id,
            parent_version_id=root.id,
            job_id=child_job.id,
            version_no=2,
            symbol="AMZN",
            target_contract=root.target_contract,
            target_contract_hash=root.target_contract_hash,
            decision_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
            market_cutoff_at=datetime(2026, 9, 25, 21, tzinfo=UTC),
            price_input_manifest={},
            evidence_version_manifest=[],
            feature_snapshot={},
            baseline_probabilities=None,
            joint_probabilities=None,
            model_status="research_only",
            model_manifest={},
            trigger_type="manual_revision",
        )
        db.add(child)
        db.commit()

    response = client.get("/v2/stocks/amzn/forecast-roots")
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "AMZN"
    assert payload["limit"] == 50
    assert [item["id"] for item in payload["roots"]] == [str(newest_root), str(old_root)]
    assert payload["roots"][0] == {
        "id": str(newest_root),
        "decision_at": "2026-09-24T22:00:00Z",
        "target_end_date": "2027-01-29",
        "model_status": "research_only",
        "latest_version_id": str(child.id),
        "latest_version_no": 2,
        "expired": False,
    }
    assert payload["roots"][1]["target_end_date"] == "2025-01-02"
    assert payload["roots"][1]["expired"] is True


def test_monitor_status_is_derived_from_persisted_runs_and_never_invents_health(client):
    _clear_monitor_runs()
    try:
        empty = client.get("/v2/monitor/status")
        assert empty.status_code == 200
        assert empty.json() == {
            "health": "not_recorded",
            "schedule_interval_seconds": 3600,
            "stale_after_seconds": 7200,
            "last_run": None,
            "last_success": None,
        }

        now = datetime.now(UTC)
        with SessionLocal() as db:
            succeeded = OfficialMonitorRunV2(
                status="succeeded",
                started_at=now - timedelta(minutes=30),
                completed_at=now - timedelta(minutes=29),
                next_due_at=now + timedelta(minutes=31),
                per_symbol_results={"AAPL": {"status": "succeeded"}},
                last_success_watermark={"AAPL": "2026-09-25T00:00:00Z"},
            )
            partial = OfficialMonitorRunV2(
                status="partial",
                started_at=now - timedelta(minutes=5),
                completed_at=now - timedelta(minutes=4),
                next_due_at=now + timedelta(minutes=56),
                per_symbol_results={"AAPL": {"status": "succeeded"}, "NVDA": {"status": "failed"}},
                error_summary={"NVDA": "SEC unavailable"},
                evaluation_summary={"examined": 3, "succeeded": 1, "pending": 2},
            )
            db.add_all([succeeded, partial])
            db.commit()
            db.refresh(succeeded)
            db.refresh(partial)

        response = client.get("/v2/monitor/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["health"] == "degraded"
        assert payload["last_run"]["id"] == str(partial.id)
        assert payload["last_run"]["status"] == "partial"
        assert payload["last_run"]["per_symbol_results"]["NVDA"]["status"] == "failed"
        assert payload["last_run"]["evaluation_summary"] == {"examined": 3, "succeeded": 1, "pending": 2}
        assert payload["last_success"] == {
            "id": str(succeeded.id),
            "completed_at": succeeded.completed_at.isoformat().replace("+00:00", "Z"),
            "last_success_watermark": {"AAPL": "2026-09-25T00:00:00Z"},
        }
        assert client.get("/v2/stocks/AAPL/workspace").json()["monitor_status"] == "degraded"
    finally:
        _clear_monitor_runs()


def test_monitor_status_reports_healthy_for_a_recent_complete_persisted_run(client):
    _clear_monitor_runs()
    try:
        now = datetime.now(UTC)
        with SessionLocal() as db:
            succeeded = OfficialMonitorRunV2(
                status="succeeded",
                started_at=now - timedelta(minutes=10),
                completed_at=now - timedelta(minutes=9),
                next_due_at=now + timedelta(minutes=51),
                per_symbol_results={"AAPL": {"status": "succeeded"}},
                last_success_watermark={"AAPL": "2026-09-25T00:00:00Z"},
            )
            db.add(succeeded)
            db.commit()
            db.refresh(succeeded)

        response = client.get("/v2/monitor/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["health"] == "healthy"
        assert payload["last_run"]["id"] == str(succeeded.id)
        assert payload["last_success"]["id"] == str(succeeded.id)
        assert payload["last_success"]["completed_at"] == succeeded.completed_at.isoformat().replace("+00:00", "Z")
    finally:
        _clear_monitor_runs()


def test_evaluations_are_read_only_grouped_by_root_and_separate_persisted_time_modes(client):
    observed_root = _create_version(symbol="AAPL", target_end=date(2025, 1, 2))
    historical_root = _create_version(symbol="AAPL", target_end=date(2025, 1, 2))
    unknown_root = _create_version(symbol="AAPL", target_end=date(2025, 1, 2))
    other_symbol = _create_version(symbol="MSFT", target_end=date(2025, 1, 2))
    with SessionLocal() as db:
        observed = db.get(ForecastVersionV2, observed_root)
        historical = db.get(ForecastVersionV2, historical_root)
        unknown = db.get(ForecastVersionV2, unknown_root)
        other = db.get(ForecastVersionV2, other_symbol)
        observed.research_report = {"time_mode": "observed"}
        historical.research_report = {"time_mode": "historical_research"}
        db.add_all(
            [
                ForecastEvaluationV2(
                    forecast_version_id=observed.id,
                    target_contract_hash=observed.target_contract_hash,
                    actual_target_close=101.0,
                    actual_label="bullish",
                    label_available_at=datetime.now(UTC),
                    brier_score=0.4,
                    log_loss=0.8,
                    direction_correct=True,
                    status="succeeded",
                    result_version=1,
                    price_input_version={"revision": 1},
                ),
                ForecastEvaluationV2(
                    forecast_version_id=observed.id,
                    target_contract_hash=observed.target_contract_hash,
                    actual_target_close=102.0,
                    actual_label="bullish",
                    label_available_at=datetime.now(UTC),
                    brier_score=0.3,
                    log_loss=0.7,
                    direction_correct=True,
                    status="succeeded",
                    result_version=2,
                    price_input_version={"revision": 2},
                ),
                ForecastEvaluationV2(
                    forecast_version_id=historical.id,
                    target_contract_hash=historical.target_contract_hash,
                    actual_target_close=95.0,
                    actual_label="bearish",
                    label_available_at=datetime.now(UTC),
                    # This research_only forecast has a mature real label but
                    # no numeric probability vector. It must not inflate the
                    # model-score denominator.
                    status="succeeded",
                    result_version=1,
                ),
                ForecastEvaluationV2(
                    forecast_version_id=other.id,
                    target_contract_hash=other.target_contract_hash,
                    status="succeeded",
                    result_version=1,
                    actual_label="neutral",
                ),
            ]
        )
        db.commit()
        before = (
            db.query(ForecastVersionV2).count(),
            db.query(ForecastEvaluationV2).count(),
        )

    response = client.get("/v2/evaluations?symbol=AAPL")
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "AAPL"
    assert payload["status"] == "insufficient_samples"
    assert payload["minimum_scored_roots"] == 5

    prospective = payload["cohorts"]["prospective"]
    assert prospective["time_mode"] == "observed"
    assert prospective["status"] == "insufficient_samples"
    assert prospective["sample"] == {
        "root_denominator": 1,
        "labelled_root_count": 1,
        "scored_root_count": 1,
        "unscored_root_count": 0,
        "version_count": 1,
        "labelled_version_count": 1,
        "scored_version_count": 1,
    }
    observed_version = prospective["roots"][0]["versions"][0]
    assert observed_version["id"] == str(observed_root)
    assert observed_version["time_mode"] == "observed"
    assert observed_version["latest_evaluation"]["result_version"] == 2
    assert observed_version["latest_evaluation"]["actual_target_close"] == 102.0
    assert [item["result_version"] for item in observed_version["evaluation_history"]] == [2, 1]

    historical = payload["cohorts"]["historical_research"]
    assert historical["status"] == "pending"
    assert historical["roots"][0]["root_id"] == str(historical_root)
    assert historical["sample"] == {
        "root_denominator": 1,
        "labelled_root_count": 1,
        "scored_root_count": 0,
        "unscored_root_count": 1,
        "version_count": 1,
        "labelled_version_count": 1,
        "scored_version_count": 0,
    }
    historical_evaluation = historical["roots"][0]["versions"][0]["latest_evaluation"]
    assert historical_evaluation["status"] == "succeeded"
    assert historical_evaluation["actual_label"] == "bearish"
    assert historical_evaluation["brier_score"] is None
    assert historical_evaluation["log_loss"] is None

    unknown = payload["cohorts"]["unknown"]
    assert unknown["time_mode"] == "unknown"
    assert unknown["roots"][0]["root_id"] == str(unknown_root)
    assert unknown["roots"][0]["versions"][0]["latest_evaluation"] is None

    with SessionLocal() as db:
        assert (
            db.query(ForecastVersionV2).count(),
            db.query(ForecastEvaluationV2).count(),
        ) == before
    _delete_owned_evaluation_fixture_versions([observed_root, historical_root, unknown_root, other_symbol])


def test_missing_database_returns_503_instead_of_accepting_a_job(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OperationalError("insert", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(api, "enqueue_job", unavailable)
    response = client.post(
        "/v2/forecast-jobs",
        json={"symbol": "AAPL", "kind": "new"},
        headers={"Idempotency-Key": "database-unavailable"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "V2 job store is unavailable"


def test_main_application_exposes_v2_router_without_running_a_forecast():
    with TestClient(main_app) as live_app:
        response = live_app.post(
            "/v2/forecast-jobs",
            json={"symbol": "AMZN", "kind": "new"},
            headers={"Idempotency-Key": f"main-router-{uuid4()}"},
        )
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert response.json()["result_version_id"] is None
