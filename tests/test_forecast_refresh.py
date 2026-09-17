from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sqlalchemy import delete, event, func, select

from app.database import Base, SessionLocal, engine
from app.event_provider import ProviderResult
from app.forecast_refresh import (
    ForecastRefreshError,
    create_forecast_refresh_tables,
    get_forecast_refresh_report,
    run_forecast_refresh,
    target_window,
)
from app.market_data import YAHOO_SOURCE
from app.market_time import xnys_session_close_at
from app.models import EventExtraction, ForecastRevision, ForecastRevisionEvidence, ForecastSnapshot, MarketPriceRevision, ResearchRun
from app.training_data import FEATURE_COLUMNS


_BEFORE = datetime(2026, 7, 29, 20, tzinfo=UTC)
_AFTER = datetime(2026, 7, 31, 20, tzinfo=UTC)
_QUOTE = "Apple today announced financial results for its fiscal 2026 third quarter ended June 27, 2026."
_TEXT = _QUOTE + " Quarterly revenue was $109.4 billion. Operating expenses increased."


class FakeProvider:
    def __init__(self, *, fail_research: bool = False):
        self.calls = 0
        self.fail_research = fail_research

    def extract(self, *, system_prompt: str, document_payload: str, model: str) -> ProviderResult:
        self.calls += 1
        if "You extract a small set" in system_prompt:
            return ProviderResult(
                content=json.dumps(
                    {
                        "events": [
                            {
                                "event_type": "earnings_release",
                                "event_date": "2026-07-30",
                                "impact_direction": "positive",
                                "summary": "Apple reported fiscal 2026 third-quarter results and quarterly revenue of $109.4 billion.",
                                "evidence_quote": _QUOTE,
                            }
                        ]
                    }
                ),
                response_model="fake",
                usage={"total_tokens": 1},
            )
        if self.fail_research:
            return ProviderResult(content="not-json", response_model="fake", usage={"total_tokens": 1})
        claim = "Revenue supports the business case." if "supports a constructive" in system_prompt else "Expenses qualify the business case."
        quote = "Quarterly revenue was $109.4 billion." if "supports a constructive" in system_prompt else "Operating expenses increased."
        return ProviderResult(
            content=json.dumps({"claims": [{"claim": claim, "source_id": "aapl-2026-q3", "evidence_quote": quote}]}),
            response_model="fake",
            usage={"total_tokens": 1},
        )


@pytest.fixture
def trusted_model(tmp_path: Path) -> Path:
    directory = tmp_path / "week4"
    directory.mkdir()
    matrix = np.array(
        [
            [-0.03, -0.02, 0.10, 0.8, -0.10, -0.02],
            [-0.01, 0.00, 0.15, 1.0, -0.04, 0.00],
            [0.02, 0.03, 0.30, 1.2, -0.01, 0.04],
            [0.04, 0.06, 0.35, 1.4, 0.00, 0.07],
            [-0.04, -0.01, 0.20, 0.9, -0.08, -0.03],
            [0.01, 0.01, 0.22, 1.1, -0.03, 0.02],
        ]
    )
    model = LogisticRegression(random_state=42, max_iter=1000).fit(
        pd.DataFrame(matrix, columns=list(FEATURE_COLUMNS)), [0, 1, 2, 2, 0, 1]
    )
    joblib.dump(model, directory / "last_fold_calibrated_model.joblib")
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "artifact_version": "week4-training-artifacts-v1",
                "model_file": "last_fold_calibrated_model.joblib",
                "feature_order": list(FEATURE_COLUMNS),
                "classes": [0, 1, 2],
                "data_metadata": {
                    "feature_version": "market-features-v1",
                    "source": YAHOO_SOURCE,
                    "snapshot_mode": "historical_research",
                },
                "last_fold": {
                    "windows": {
                        "calibration": {"start": "2026-01-07", "end": "2026-04-08"},
                        "test": {"start": "2026-04-09", "end": "2026-08-07"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def source_set(tmp_path: Path) -> tuple[Path, Path]:
    documents = tmp_path / "documents"
    documents.mkdir()
    document = {
        "document_id": "aapl-2026-q3",
        "company": "Apple",
        "ticker": "AAPL",
        "source_url": "https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/",
        "source_domain": "www.apple.com",
        "published_date": "2026-07-30",
        "title": "Apple reports third quarter results",
        "text": _TEXT,
        "sha256": hashlib.sha256(_TEXT.encode()).hexdigest(),
    }
    (documents / "aapl-2026-q3.json").write_text(json.dumps(document), encoding="utf-8")
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"documents": [{key: value for key, value in document.items() if key != "text"}]}), encoding="utf-8")
    return documents, manifest


@pytest.fixture
def seeded_prices():
    Base.metadata.create_all(bind=engine)
    create_forecast_refresh_tables()
    with SessionLocal() as db:
        snapshot_ids = select(ForecastSnapshot.id).where(
            ForecastSnapshot.feature_trading_date.in_([_BEFORE.date(), _AFTER.date()])
        )
        db.execute(delete(ForecastRevisionEvidence).where(ForecastRevisionEvidence.snapshot_id.in_(snapshot_ids)))
        db.execute(delete(ForecastRevision).where(ForecastRevision.snapshot_id.in_(snapshot_ids)))
        db.execute(delete(ForecastSnapshot).where(ForecastSnapshot.id.in_(snapshot_ids)))
        db.execute(delete(ResearchRun).where(ResearchRun.symbol == "AAPL", ResearchRun.as_of_time == _AFTER))
        db.execute(delete(EventExtraction).where(EventExtraction.document_id == "aapl-2026-q3"))
        db.execute(delete(MarketPriceRevision).where(MarketPriceRevision.symbol.in_(["AAPL", "SPY"])))
        db.commit()
    sessions: list[date] = []
    candidate = date(2026, 5, 1)
    while candidate <= date(2026, 8, 31):
        try:
            xnys_session_close_at(candidate)
        except ValueError:
            candidate += timedelta(days=1)
            continue
        sessions.append(candidate)
        candidate += timedelta(days=1)
    with SessionLocal() as db:
        for index, trading_date in enumerate(sessions):
            for symbol, growth in (("AAPL", 0.003), ("SPY", 0.001)):
                close = 100.0 * (1.0 + growth) ** index
                db.add(
                    MarketPriceRevision(
                        symbol=symbol,
                        trading_date=trading_date,
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        volume=1_000_000 + index,
                        source=YAHOO_SOURCE,
                        revision_number=1,
                        content_hash=hashlib.sha256(f"{symbol}-{trading_date}".encode()).hexdigest(),
                        available_at=xnys_session_close_at(trading_date),
                        observed_at=datetime(2026, 9, 9, tzinfo=UTC),
                        is_initial_backfill=True,
                    )
                )
        db.commit()


def _run(*, model: Path, documents: Path, manifest: Path, provider: FakeProvider):
    with SessionLocal() as db:
        return run_forecast_refresh(
            symbol="AAPL",
            before_as_of_time=_BEFORE,
            after_as_of_time=_AFTER,
            document_id="aapl-2026-q3",
            db=db,
            extraction_provider_factory=lambda: provider,
            research_provider_factory=lambda: provider,
            model_directory=model,
            document_directory=documents,
            source_manifest=manifest,
            model="deepseek-flash",
        )


def _refresh_snapshot_count(db) -> int:
    return db.scalar(
        select(func.count())
        .select_from(ForecastSnapshot)
        .where(
            ForecastSnapshot.symbol == "AAPL",
            ForecastSnapshot.feature_trading_date.in_([_BEFORE.date(), _AFTER.date()]),
        )
    )


def test_refresh_persists_two_versions_delta_evidence_and_rolling_windows(
    seeded_prices, trusted_model, source_set
):
    documents, manifest = source_set
    provider = FakeProvider()
    result = _run(model=trusted_model, documents=documents, manifest=manifest, provider=provider)

    assert provider.calls == 3
    assert result.original_snapshot.feature_as_of_time == _BEFORE
    assert result.revised_snapshot.feature_as_of_time == _AFTER
    assert result.evidence.parent_snapshot_id == result.original_snapshot.id
    assert result.evidence.snapshot_id == result.revised_snapshot.id
    assert result.evidence.revision_mode == "rolling_refresh"
    assert result.original_target.as_dict() == {"start": "2026-07-30", "end": "2026-08-26"}
    assert result.revised_target.as_dict() == {"start": "2026-08-03", "end": "2026-08-28"}
    payload = result.as_dict()
    assert payload["probability_delta"]["bearish"] == pytest.approx(
        result.revised_snapshot.bearish_probability - result.original_snapshot.bearish_probability
    )
    assert "not an LLM adjustment" in payload["limitations"][1]
    with SessionLocal() as db:
        assert _refresh_snapshot_count(db) == 2
        assert db.scalar(select(func.count()).select_from(ForecastRevisionEvidence)) == 1
        saved = get_forecast_refresh_report(snapshot_id=result.revised_snapshot.id, db=db)
        assert saved.as_dict()["probability_delta"] == pytest.approx(payload["probability_delta"])


def test_research_failure_keeps_auditable_failure_but_no_forecast_chain(
    seeded_prices, trusted_model, source_set
):
    documents, manifest = source_set
    with pytest.raises(ForecastRefreshError, match="research failed"):
        _run(model=trusted_model, documents=documents, manifest=manifest, provider=FakeProvider(fail_research=True))
    with SessionLocal() as db:
        assert _refresh_snapshot_count(db) == 0
        run = db.scalar(select(ResearchRun))
        assert run is not None and run.status == "failed" and run.report is None


def test_repeat_same_parent_and_event_is_rejected_without_new_provider_calls(
    seeded_prices, trusted_model, source_set
):
    documents, manifest = source_set
    provider = FakeProvider()
    _run(model=trusted_model, documents=documents, manifest=manifest, provider=provider)
    calls_before = provider.calls
    with pytest.raises(ForecastRefreshError, match="already has a rolling refresh"):
        _run(model=trusted_model, documents=documents, manifest=manifest, provider=provider)
    assert provider.calls == calls_before
    with SessionLocal() as db:
        assert _refresh_snapshot_count(db) == 2


def test_sidecar_failure_rolls_back_the_new_root_child_and_link(seeded_prices, trusted_model, source_set):
    documents, manifest = source_set

    def reject_sidecar(*_: object) -> None:
        raise RuntimeError("sidecar write failed")

    event.listen(ForecastRevisionEvidence, "before_insert", reject_sidecar)
    try:
        with pytest.raises(RuntimeError, match="sidecar write failed"):
            _run(model=trusted_model, documents=documents, manifest=manifest, provider=FakeProvider())
    finally:
        event.remove(ForecastRevisionEvidence, "before_insert", reject_sidecar)

    with SessionLocal() as db:
        assert _refresh_snapshot_count(db) == 0
        assert db.scalar(select(func.count()).select_from(ForecastRevision)) == 0
        assert db.scalar(select(func.count()).select_from(ForecastRevisionEvidence)) == 0


def test_api_rejects_fixed_target_mode_before_constructing_a_provider(client):
    response = client.post(
        "/forecast-refresh-runs",
        json={
            "symbol": "AAPL",
            "before_as_of_time": "2026-07-29T20:00:00Z",
            "after_as_of_time": "2026-07-31T20:00:00Z",
            "document_id": "aapl-2026-q3",
            "revision_mode": "fixed_target",
        },
    )
    assert response.status_code == 422


def test_target_window_fails_closed_when_no_session_is_found(monkeypatch):
    monkeypatch.setattr("app.forecast_refresh.xnys_session_close_at", lambda _: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(ForecastRefreshError, match="sixty"):
        target_window(date(2026, 7, 29))
