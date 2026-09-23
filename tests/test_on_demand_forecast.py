from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select
from sklearn.linear_model import LogisticRegression

import app.on_demand_forecast as on_demand
from app.database import SessionLocal
from app.forecast_replay import replay_forecast_snapshot
from app.market_time import _xnys_calendar_for_year, xnys_session_close_at
from app.models import ForecastSnapshot, MarketPriceRevision
from app.training_data import FEATURE_COLUMNS


_NOW = datetime(2026, 9, 15, 21, tzinfo=UTC)
_OBSERVED_AFTER_REFRESH = datetime(2026, 9, 15, 21, 1, tzinfo=UTC)
_TEST_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "NVDA", "SPY")


@pytest.fixture(autouse=True)
def remove_on_demand_price_revisions_after_test():
    """Keep later dashboard tests independent of this module's observed bars."""
    yield
    _delete_prices(*_TEST_SYMBOLS)


@pytest.fixture
def trusted_model_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "trusted-week4"
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
    manifest = {
        "artifact_version": "week4-training-artifacts-v1",
        "model_file": "last_fold_calibrated_model.joblib",
        "feature_order": list(FEATURE_COLUMNS),
        "classes": [0, 1, 2],
        "data_metadata": {
            "feature_version": "market-features-v1",
            "source": "yahoo-finance-chart",
            "snapshot_mode": "historical_research",
        },
        "last_fold": {
            "windows": {
                "calibration": {"start": "2026-01-07", "end": "2026-04-08"},
                "test": {"start": "2026-04-09", "end": "2026-08-07"},
            }
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


@pytest.fixture
def prepared_market_data(monkeypatch, trusted_model_dir: Path):
    monkeypatch.setattr(on_demand, "TRUSTED_MODEL_DIRECTORY", trusted_model_dir)
    monkeypatch.setattr(on_demand, "_utc_now", lambda: _NOW)
    monkeypatch.setattr(on_demand, "_refresh_requested_market_data", lambda *args, **kwargs: None)
    _delete_prices("AAPL", "SPY")
    dates = [session.date() for session in _xnys_calendar_for_year(2026).sessions_in_range("2026-07-01", "2026-09-14")][-30:]
    _insert_prices("AAPL", dates, base=190.0)
    _insert_prices("SPY", dates, base=500.0)
    return dates


def test_post_forecast_run_archives_observed_current_features_and_readback(client, prepared_market_data):
    response = client.post("/forecast-runs", json={"symbol": " aapl "})
    assert response.status_code == 201
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["feature_trading_date"] == prepared_market_data[-1].isoformat()
    assert body["feature_as_of_time"] == _NOW.isoformat().replace("+00:00", "Z")
    assert body["feature_snapshot_mode"] == "observed"
    assert body["model_version"].startswith("week4-calibrated-")
    assert body["cutoff_date"] == prepared_market_data[-1].isoformat()
    assert set(body["target_window"]) == {"start", "end"}
    assert body["model_status"] == "experimental_offline_model"
    probabilities = [body[name] for name in ("bearish_probability", "neutral_probability", "bullish_probability")]
    assert all(0.0 <= value <= 1.0 for value in probabilities)
    assert sum(probabilities) == pytest.approx(1.0)
    assert len(body["feature_export_sha256"]) == 64

    with SessionLocal() as db:
        stored = db.get(ForecastSnapshot, body["id"])
    assert stored is not None
    assert stored.feature_values == pytest.approx(body["feature_values"])
    assert stored.feature_snapshot_mode == "observed"

    readback = client.get(f"/forecast-snapshots/{body['id']}")
    assert readback.status_code == 200
    assert readback.json() == {
        key: value
        for key, value in body.items()
        if key not in {"cutoff_date", "target_window", "model_status", "limitations"}
    }

    with SessionLocal() as db:
        replay = replay_forecast_snapshot(
            snapshot_id=body["id"], model_dir=on_demand.TRUSTED_MODEL_DIRECTORY, db=db
        )
    assert replay.matches is True

    duplicate = client.post("/forecast-runs", json={"symbol": "AAPL"})
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == body["id"]


def test_post_forecast_run_rejects_missing_benchmark_without_archiving(client, monkeypatch, trusted_model_dir: Path):
    monkeypatch.setattr(on_demand, "TRUSTED_MODEL_DIRECTORY", trusted_model_dir)
    monkeypatch.setattr(on_demand, "_utc_now", lambda: _NOW)
    monkeypatch.setattr(on_demand, "_refresh_requested_market_data", lambda *args, **kwargs: "provider unavailable")
    _delete_prices("MSFT", "SPY")
    dates = [session.date() for session in _xnys_calendar_for_year(2026).sessions_in_range("2026-07-01", "2026-09-14")][-30:]
    _insert_prices("MSFT", dates, base=410.0)
    before = _snapshot_count()

    response = client.post("/forecast-runs", json={"symbol": "MSFT"})
    assert response.status_code == 422
    assert "missing observed stock or SPY" in response.json()["detail"]
    assert _snapshot_count() == before


def test_post_forecast_run_refuses_a_time_before_fixed_artifact_publication(client, monkeypatch):
    before = _snapshot_count()
    monkeypatch.setattr(on_demand, "_utc_now", lambda: datetime(2026, 9, 10, 23, 59, tzinfo=UTC))

    def refresh_must_not_run(*args, **kwargs):
        raise AssertionError("a pre-publication request must stop before market refresh")

    monkeypatch.setattr(on_demand, "_refresh_requested_market_data", refresh_must_not_run)
    response = client.post("/forecast-runs", json={"symbol": "AAPL"})

    assert response.status_code == 422
    assert "not published until" in response.json()["detail"]
    assert _snapshot_count() == before


def test_post_forecast_run_uses_observation_cutoff_after_refresh(client, monkeypatch, trusted_model_dir: Path):
    """Rows observed during this click must be visible to the same forecast."""
    monkeypatch.setattr(on_demand, "TRUSTED_MODEL_DIRECTORY", trusted_model_dir)
    _delete_prices("GOOGL", "SPY")
    clock_values = iter((_NOW, _OBSERVED_AFTER_REFRESH))
    monkeypatch.setattr(on_demand, "_utc_now", lambda: next(clock_values))
    dates = [
        session.date()
        for session in _xnys_calendar_for_year(2026).sessions_in_range("2026-07-01", "2026-09-15")
    ][-30:]

    def refresh(symbol: str, *, completed_date: date, run_time: datetime) -> None:
        assert symbol == "GOOGL"
        assert completed_date == date(2026, 9, 15)
        assert run_time == _NOW
        _insert_prices("GOOGL", dates, base=220.0, observed_at=_OBSERVED_AFTER_REFRESH)
        _insert_prices("SPY", dates, base=500.0, observed_at=_OBSERVED_AFTER_REFRESH)
        return None

    monkeypatch.setattr(on_demand, "_refresh_requested_market_data", refresh)
    response = client.post("/forecast-runs", json={"symbol": "GOOGL"})
    assert response.status_code == 201
    assert response.json()["feature_as_of_time"] == _OBSERVED_AFTER_REFRESH.isoformat().replace("+00:00", "Z")
    assert response.json()["feature_trading_date"] == date(2026, 9, 15).isoformat()


def test_post_forecast_run_rejects_stale_data_when_refresh_fails(client, monkeypatch, trusted_model_dir: Path):
    monkeypatch.setattr(on_demand, "TRUSTED_MODEL_DIRECTORY", trusted_model_dir)
    monkeypatch.setattr(on_demand, "_utc_now", lambda: _NOW)
    monkeypatch.setattr(on_demand, "_refresh_requested_market_data", lambda *args, **kwargs: "Yahoo Finance request failed")
    _delete_prices("NVDA", "SPY")
    dates = [session.date() for session in _xnys_calendar_for_year(2026).sessions_in_range("2026-05-01", "2026-07-31")][-30:]
    _insert_prices("NVDA", dates, base=140.0)
    _insert_prices("SPY", dates, base=490.0)
    before = _snapshot_count()

    response = client.post("/forecast-runs", json={"symbol": "NVDA"})
    assert response.status_code == 422
    assert "market data are stale" in response.json()["detail"]
    assert "Yahoo Finance request failed" in response.json()["detail"]
    assert _snapshot_count() == before


def _insert_prices(
    symbol: str,
    dates: list[date],
    *,
    base: float,
    observed_at: datetime = _NOW,
) -> None:
    source = "yahoo-finance-chart"
    with SessionLocal() as db:
        for offset, trading_date in enumerate(dates):
            close = base + offset
            canonical = json.dumps(
                {"symbol": symbol, "trading_date": trading_date.isoformat(), "close": close},
                sort_keys=True,
            )
            db.add(
                MarketPriceRevision(
                    symbol=symbol,
                    trading_date=trading_date,
                    open=close - 0.5,
                    high=close + 1.0,
                    low=close - 1.0,
                    close=close,
                    volume=1_000_000 + offset,
                    source=source,
                    revision_number=1,
                    content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    available_at=xnys_session_close_at(trading_date),
                    observed_at=observed_at,
                    is_initial_backfill=False,
                )
            )
        db.commit()


def _snapshot_count() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(ForecastSnapshot)) or 0)


def _delete_prices(*symbols: str) -> None:
    with SessionLocal() as db:
        db.query(MarketPriceRevision).filter(
            MarketPriceRevision.source == "yahoo-finance-chart",
            MarketPriceRevision.symbol.in_(symbols),
        ).delete(synchronize_session=False)
        db.commit()
