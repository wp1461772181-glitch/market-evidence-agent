from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sqlalchemy import func, select

from app.database import SessionLocal
from app.forecast_archive import archive_forecast_snapshot, load_trusted_model_artifact
from app.models import ForecastSnapshot
from app.training import _probabilities_in_order
from app.training_data import FEATURE_COLUMNS


_TRADING_DATE = date(2026, 4, 10)
_FEATURE_VALUES = {
    "momentum_5d": 0.01,
    "momentum_20d": 0.02,
    "volatility_20d": 0.25,
    "volume_ratio_20d": 1.1,
    "drawdown_20d": -0.05,
    "relative_return_20d": 0.03,
}


@pytest.fixture
def week4_inputs(tmp_path: Path) -> tuple[Path, Path]:
    model_dir = tmp_path / "week4"
    model_dir.mkdir()
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
    joblib.dump(model, model_dir / "last_fold_calibrated_model.joblib")
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
    (model_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    feature_path = tmp_path / "features.json"
    _write_feature_export(feature_path)
    return model_dir, feature_path


def _write_feature_export(path: Path, *, source: str = "yahoo-finance-chart", version: str = "market-features-v1") -> None:
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "as_of_time": "2026-04-10T21:00:00+00:00",
                    "feature_version": version,
                    "source": source,
                    "snapshot_mode": "historical_research",
                },
                "rows": [
                    {"symbol": " AAPL ", "trading_date": _TRADING_DATE.isoformat(), **_FEATURE_VALUES},
                    {"symbol": "MSFT", "trading_date": _TRADING_DATE.isoformat(), **_FEATURE_VALUES},
                ],
            }
        ),
        encoding="utf-8",
    )


def _snapshot_count() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(ForecastSnapshot)) or 0)


def test_archives_real_sklearn_probabilities_and_get_returns_persisted_record(client, week4_inputs):
    model_dir, feature_path = week4_inputs
    artifact = load_trusted_model_artifact(model_dir)
    expected = _probabilities_in_order(
        artifact.model, pd.DataFrame([_FEATURE_VALUES], columns=list(FEATURE_COLUMNS))
    )[0]

    with SessionLocal() as db:
        snapshot = archive_forecast_snapshot(
            model_dir=model_dir,
            feature_path=feature_path,
            symbol=" aapl ",
            trading_date=_TRADING_DATE,
            db=db,
        )
        snapshot_id = snapshot.id

    assert [snapshot.bearish_probability, snapshot.neutral_probability, snapshot.bullish_probability] == pytest.approx(expected)
    assert snapshot.feature_values == _FEATURE_VALUES
    assert snapshot.model_version == f"week4-calibrated-{snapshot.model_sha256[:16]}"
    assert len(snapshot.model_sha256) == len(snapshot.model_manifest_sha256) == len(snapshot.feature_export_sha256) == 64

    feature_path.unlink()
    (model_dir / "last_fold_calibrated_model.joblib").write_bytes(b"changed-after-archive")
    response = client.get(f"/forecast-snapshots/{snapshot_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(snapshot_id)
    assert body["symbol"] == "AAPL"
    assert body["feature_trading_date"] == "2026-04-10"
    assert body["feature_values"] == pytest.approx(_FEATURE_VALUES)
    assert [body[name] for name in ("bearish_probability", "neutral_probability", "bullish_probability")] == pytest.approx(expected)
    assert "model_path" not in body
    assert "feature_path" not in body


def test_rejects_mismatched_or_early_inputs_without_insert_and_allows_distinct_snapshot_ids(client, week4_inputs):
    model_dir, feature_path = week4_inputs
    initial_count = _snapshot_count()
    with SessionLocal() as db:
        first = archive_forecast_snapshot(
            model_dir=model_dir,
            feature_path=feature_path,
            symbol="AAPL",
            trading_date=_TRADING_DATE,
            db=db,
        )
    assert _snapshot_count() == initial_count + 1

    _write_feature_export(feature_path, source="wrong-source")
    with SessionLocal() as db, pytest.raises(ValueError, match="source does not match"):
        archive_forecast_snapshot(
            model_dir=model_dir,
            feature_path=feature_path,
            symbol="AAPL",
            trading_date=_TRADING_DATE,
            db=db,
        )
    assert _snapshot_count() == initial_count + 1

    _write_feature_export(feature_path)
    with SessionLocal() as db, pytest.raises(ValueError, match="predates"):
        archive_forecast_snapshot(
            model_dir=model_dir,
            feature_path=feature_path,
            symbol="AAPL",
            trading_date="2026-04-08",
            db=db,
        )
    assert _snapshot_count() == initial_count + 1

    _write_feature_export(feature_path)
    payload = json.loads(feature_path.read_text(encoding="utf-8"))
    payload["rows"][0]["volatility_20d"] = -0.01
    feature_path.write_text(json.dumps(payload), encoding="utf-8")
    with SessionLocal() as db, pytest.raises(ValueError, match="non-negative"):
        archive_forecast_snapshot(
            model_dir=model_dir,
            feature_path=feature_path,
            symbol="AAPL",
            trading_date=_TRADING_DATE,
            db=db,
        )
    assert _snapshot_count() == initial_count + 1

    _write_feature_export(feature_path)
    with SessionLocal() as db:
        second = archive_forecast_snapshot(
            model_dir=model_dir,
            feature_path=feature_path,
            symbol="AAPL",
            trading_date=_TRADING_DATE,
            db=db,
        )
    assert first.id != second.id
    assert _snapshot_count() == initial_count + 2

    assert client.get("/forecast-snapshots/not-a-uuid").status_code == 422
    assert client.get("/forecast-snapshots/00000000-0000-0000-0000-000000000000").status_code == 404


def test_rejects_manifest_model_path_outside_artifact_directory(week4_inputs):
    model_dir, _ = week4_inputs
    manifest_path = model_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["model_file"] = "../outside.joblib"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="basename"):
        load_trusted_model_artifact(model_dir)


@pytest.mark.parametrize(
    ("invalid_case", "match"),
    [
        ("duplicate", "exactly one row"),
        ("non_finite", "non-finite"),
        ("cutoff_before_close", "later than the feature export cutoff"),
        ("version_mismatch", "feature_version does not match"),
    ],
)
def test_rejects_feature_export_contract_boundaries_without_insert(client, week4_inputs, invalid_case, match):
    model_dir, feature_path = week4_inputs
    payload = json.loads(feature_path.read_text(encoding="utf-8"))
    if invalid_case == "duplicate":
        payload["rows"].append(dict(payload["rows"][0]))
    elif invalid_case == "non_finite":
        payload["rows"][0]["momentum_5d"] = float("nan")
    elif invalid_case == "cutoff_before_close":
        payload["metadata"]["as_of_time"] = "2026-04-10T19:00:00+00:00"
    elif invalid_case == "version_mismatch":
        payload["metadata"]["feature_version"] = "other-features-v1"
    else:  # pragma: no cover - protects future parameter edits.
        raise AssertionError(f"unknown test case {invalid_case}")
    feature_path.write_text(json.dumps(payload), encoding="utf-8")
    before = _snapshot_count()

    with SessionLocal() as db, pytest.raises(ValueError, match=match):
        archive_forecast_snapshot(
            model_dir=model_dir,
            feature_path=feature_path,
            symbol="AAPL",
            trading_date=_TRADING_DATE,
            db=db,
        )
    assert _snapshot_count() == before
