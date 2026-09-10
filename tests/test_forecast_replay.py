from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sqlalchemy import func, select

from app.database import SessionLocal
from app.forecast_archive import load_trusted_model_artifact
from app.forecast_replay import main, replay_forecast_snapshot
from app.models import ForecastSnapshot
from app.training import _probabilities_in_order
from app.training_data import FEATURE_COLUMNS


_FEATURE_VALUES = {
    "momentum_5d": 0.01,
    "momentum_20d": 0.02,
    "volatility_20d": 0.25,
    "volume_ratio_20d": 1.1,
    "drawdown_20d": -0.05,
    "relative_return_20d": 0.03,
}


@pytest.fixture
def retained_snapshot(client, tmp_path: Path) -> tuple[Path, ForecastSnapshot, Path]:
    """Persist one realistic snapshot and retain its exact local artifact."""
    model_dir = tmp_path / "retained-week4"
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
    artifact = load_trusted_model_artifact(model_dir)
    probabilities = _probabilities_in_order(
        artifact.model, pd.DataFrame([_FEATURE_VALUES], columns=list(FEATURE_COLUMNS))
    )[0]

    # The original export is intentionally unrelated to replay and may disappear.
    original_feature_export = tmp_path / "original-week3-export.json"
    original_feature_export.write_text("temporary source that is not read by replay", encoding="utf-8")
    with SessionLocal() as db:
        snapshot = ForecastSnapshot(
            symbol="AAPL",
            feature_trading_date=date(2026, 4, 10),
            feature_as_of_time=datetime(2026, 4, 10, 21, tzinfo=timezone.utc),
            model_version=artifact.model_version,
            model_sha256=artifact.model_sha256,
            model_manifest_sha256=artifact.manifest_sha256,
            feature_export_sha256="0" * 64,
            feature_version=artifact.feature_version,
            feature_source=artifact.source,
            feature_snapshot_mode=artifact.snapshot_mode,
            feature_values=deepcopy(_FEATURE_VALUES),
            bearish_probability=float(probabilities[0]),
            neutral_probability=float(probabilities[1]),
            bullish_probability=float(probabilities[2]),
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        snapshot_id = snapshot.id
        detached_snapshot = ForecastSnapshot(
            id=snapshot.id,
            symbol=snapshot.symbol,
            feature_trading_date=snapshot.feature_trading_date,
            feature_as_of_time=snapshot.feature_as_of_time,
            model_version=snapshot.model_version,
            model_sha256=snapshot.model_sha256,
            model_manifest_sha256=snapshot.model_manifest_sha256,
            feature_export_sha256=snapshot.feature_export_sha256,
            feature_version=snapshot.feature_version,
            feature_source=snapshot.feature_source,
            feature_snapshot_mode=snapshot.feature_snapshot_mode,
            feature_values=deepcopy(snapshot.feature_values),
            bearish_probability=snapshot.bearish_probability,
            neutral_probability=snapshot.neutral_probability,
            bullish_probability=snapshot.bullish_probability,
        )
    assert detached_snapshot.id == snapshot_id
    return model_dir, detached_snapshot, original_feature_export


def _snapshot_count() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(ForecastSnapshot)) or 0)


def test_replay_uses_only_saved_features_after_original_export_is_removed(retained_snapshot):
    model_dir, snapshot, original_feature_export = retained_snapshot
    original_feature_export.unlink()
    before_count = _snapshot_count()
    with SessionLocal() as db:
        replay = replay_forecast_snapshot(snapshot_id=snapshot.id, model_dir=model_dir, db=db)
    assert replay.matches is True
    assert replay.model_version == snapshot.model_version
    assert replay.stored_probabilities == pytest.approx(replay.replayed_probabilities, abs=1e-12)
    assert _snapshot_count() == before_count


def test_replay_reports_probability_mismatch_without_writing_and_cli_exits_one(retained_snapshot, capsys):
    model_dir, snapshot, _ = retained_snapshot
    with SessionLocal() as db:
        persisted = db.get(ForecastSnapshot, snapshot.id)
        assert persisted is not None
        persisted.bearish_probability += 1e-6
        persisted.neutral_probability -= 1e-6
        db.commit()
    before_count = _snapshot_count()
    with SessionLocal() as db:
        replay = replay_forecast_snapshot(snapshot_id=snapshot.id, model_dir=model_dir, db=db)
    assert replay.matches is False
    assert replay.stored_probabilities["bearish"] != replay.replayed_probabilities["bearish"]
    assert _snapshot_count() == before_count

    with pytest.raises(SystemExit, match="1"):
        main(["--snapshot-id", str(snapshot.id), "--model-dir", str(model_dir)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == str(snapshot.id)
    assert payload["matches"] is False


def test_hash_mismatch_stops_before_joblib_deserialization(retained_snapshot, monkeypatch):
    model_dir, snapshot, _ = retained_snapshot
    (model_dir / "last_fold_calibrated_model.joblib").write_bytes(b"not a joblib payload")
    called = False

    def forbidden_load(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("joblib.load must not run when the retained bytes fail the saved hash")

    monkeypatch.setattr("app.forecast_archive.joblib.load", forbidden_load)
    with SessionLocal() as db, pytest.raises(ValueError, match="model SHA-256 does not match"):
        replay_forecast_snapshot(snapshot_id=snapshot.id, model_dir=model_dir, db=db)
    assert called is False


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("feature_values", {"unknown": 1.0}, "exactly the supported"),
        ("feature_version", "wrong-feature-version", "feature_version"),
        ("feature_source", "wrong-source", "feature_source"),
        ("feature_snapshot_mode", "wrong-mode", "feature_snapshot_mode"),
    ],
)
def test_replay_rejects_corrupt_persisted_contract_without_writing(retained_snapshot, field, value, match):
    model_dir, snapshot, _ = retained_snapshot
    with SessionLocal() as db:
        persisted = db.get(ForecastSnapshot, snapshot.id)
        assert persisted is not None
        setattr(persisted, field, value)
        db.commit()
    before_count = _snapshot_count()
    with SessionLocal() as db, pytest.raises(ValueError, match=match):
        replay_forecast_snapshot(snapshot_id=snapshot.id, model_dir=model_dir, db=db)
    assert _snapshot_count() == before_count


def test_unknown_snapshot_uuid_fails_cleanly_without_writing(retained_snapshot):
    model_dir, _, _ = retained_snapshot
    before_count = _snapshot_count()
    with SessionLocal() as db, pytest.raises(ValueError, match="not found"):
        replay_forecast_snapshot(snapshot_id=uuid4(), model_dir=model_dir, db=db)
    assert _snapshot_count() == before_count
