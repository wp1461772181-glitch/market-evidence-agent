import json
import hashlib
from dataclasses import replace
from datetime import date, timedelta

import pytest

import app.training_v2 as training_v2
from app.training_data_v2 import V2MarketTrainingDataset, V2MarketTrainingRow
from app.forecast_contract import HORIZON_SESSIONS, future_xnys_sessions
from app.training_v2 import (
    CLASS_LABELS,
    MARKET_FEATURE_COLUMNS,
    MarketTrainingError,
    build_market_training_plan,
    load_trusted_market_artifact,
    replay_market_probabilities,
    train_market_baseline,
    write_market_artifact,
)


def _row(partition: str, index: int, label: str) -> V2MarketTrainingRow:
    year = {"train": 2023, "calibration": 2024, "test": 2025}[partition]
    partition_offset = {"train": 0.0, "calibration": 10.0, "test": 20.0}[partition]
    first_session = date(year, 1, 3)
    anchor = first_session if index == 0 else future_xnys_sessions(first_session, index)[-1]
    target = future_xnys_sessions(anchor, HORIZON_SESSIONS)[-1]
    direction = {"bearish": -1.0, "neutral": 0.0, "bullish": 1.0}[label]
    price_delta = direction * 3.0
    return V2MarketTrainingRow(
        symbol="AAPL",
        partition=partition,
        anchor_date=anchor,
        target_end_date=target,
        anchor_close=100.0,
        target_close=100.0 + price_delta,
        absolute_return=price_delta / 100.0,
        label=label,
        remaining_sessions=20,
        realized_return_from_anchor=0.0,
        momentum_5d=partition_offset + direction + index * 0.01,
        momentum_20d=partition_offset + direction * 2 + index * 0.01,
        volatility_20d=partition_offset + 1.0 + index * 0.1,
        volume_ratio_20d=partition_offset + 0.8 + index * 0.1,
        drawdown_20d=-0.5 + direction * 0.1,
        relative_return_20d=direction * 0.4,
        feature_version="market-features-v1",
        stock_price_sha256="a" * 64,
        benchmark_price_sha256="b" * 64,
    )


@pytest.fixture
def dataset(manifest_path) -> V2MarketTrainingDataset:
    rows = []
    for partition in ("train", "calibration", "test"):
        for index, label in enumerate(CLASS_LABELS * 5):
            rows.append(_row(partition, index, label))
    return V2MarketTrainingDataset(
        rows=tuple(rows),
        audit={"fixture": True, "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
    )


@pytest.fixture
def manifest_path(tmp_path):
    path = tmp_path / "dataset-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "v2-corpus-manifest-v1",
                "mode": "historical_research",
                "symbols": ["AAPL"],
                "training_range": {"start": "2021-01-01", "end": "2026-08-31"},
                "target_spec_version": "absolute-close-v1",
                "price_basis": "provider_quote_close_v1",
                "partitions": {
                    "train": ["2021-01-01", "2023-12-31"],
                    "calibration": ["2024-01-01", "2024-12-31"],
                    "test": ["2025-01-01", "2026-08-31"],
                },
                "price_snapshot_files": [
                    {"filename": "prices-AAPL.json", "symbol": "AAPL", "sha256": "a" * 64, "observed_at": "2026-09-25T00:00:00+00:00"}
                ],
                "source_snapshot_files": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_plan_uses_only_fixed_root_rows_and_has_fixed_partition_counts(dataset):
    plan = build_market_training_plan(dataset)

    assert plan.row_counts == {"train": 15, "calibration": 15, "test": 15}
    assert plan.class_counts["train"] == {"bearish": 5, "neutral": 5, "bullish": 5}
    assert plan.feature_columns == MARKET_FEATURE_COLUMNS

    invalid = V2MarketTrainingDataset(rows=(replace(dataset.rows[0], remaining_sessions=19), *dataset.rows[1:]), audit={})
    with pytest.raises(MarketTrainingError, match="initial root rows"):
        build_market_training_plan(invalid)

    wrong_target = V2MarketTrainingDataset(
        rows=(replace(dataset.rows[0], target_end_date=dataset.rows[0].target_end_date + timedelta(days=1)), *dataset.rows[1:]),
        audit=dataset.audit,
    )
    with pytest.raises(MarketTrainingError, match="twentieth XNYS"):
        build_market_training_plan(wrong_target)

    wrong_label = V2MarketTrainingDataset(rows=(replace(dataset.rows[0], label="neutral"), *dataset.rows[1:]), audit=dataset.audit)
    with pytest.raises(MarketTrainingError, match="label does not match"):
        build_market_training_plan(wrong_label)


def test_train_fits_only_train_then_calibrates_only_calibration(dataset, manifest_path, monkeypatch):
    observed = {}
    original = training_v2.CalibratedClassifierCV.fit

    def spy(self, features, targets, *args, **kwargs):
        observed["calibration_features"] = features.copy()
        observed["calibration_targets"] = tuple(targets)
        return original(self, features, targets, *args, **kwargs)

    monkeypatch.setattr(training_v2.CalibratedClassifierCV, "fit", spy)
    result = train_market_baseline(dataset, manifest_path=manifest_path)

    expected_calibration = {row.momentum_5d for row in dataset.rows if row.partition == "calibration"}
    expected_test = {row.momentum_5d for row in dataset.rows if row.partition == "test"}
    assert set(observed["calibration_features"]["momentum_5d"]) == expected_calibration
    assert not (set(observed["calibration_features"]["momentum_5d"]) & expected_test)
    assert set(observed["calibration_targets"]) == set(CLASS_LABELS)
    assert set(result.test_metrics) == {"baseline_class_prior", "market_logistic_calibrated"}
    assert all(0.0 <= value <= 1.0 or name in {"brier_multiclass", "log_loss"} for report in result.test_metrics.values() for name, value in report.items())


def test_artifact_replays_and_cannot_be_overwritten_or_tampered(dataset, manifest_path, tmp_path):
    result = train_market_baseline(dataset, manifest_path=manifest_path)
    root = tmp_path / "artifacts" / "v2"
    output = write_market_artifact(result, artifact_root=root, run_id="fixture-001")

    artifact = load_trusted_market_artifact(output, trusted_root=root)
    test_row = next(row for row in dataset.rows if row.partition == "test")
    values = {name: getattr(test_row, name) for name in MARKET_FEATURE_COLUMNS}
    first = replay_market_probabilities(artifact, values)
    second = replay_market_probabilities(load_trusted_market_artifact(output, trusted_root=root), values)
    assert first == pytest.approx(second, abs=0.0)
    assert sum(first.values()) == pytest.approx(1.0)
    assert artifact.manifest["joint_probabilities"] is None
    assert artifact.manifest["model_status"] == "research_only"

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_market_artifact(result, artifact_root=root, run_id="fixture-001")

    (output / "market_model.joblib").write_bytes(b"changed")
    with pytest.raises(MarketTrainingError, match="hash"):
        load_trusted_market_artifact(output, trusted_root=root)


def test_loader_rejects_an_artifact_outside_its_trusted_root(dataset, manifest_path, tmp_path):
    result = train_market_baseline(dataset, manifest_path=manifest_path)
    output = write_market_artifact(result, artifact_root=tmp_path / "controlled", run_id="fixture-002")

    with pytest.raises(MarketTrainingError, match="trusted artifact root"):
        load_trusted_market_artifact(output, trusted_root=tmp_path / "different-controlled")


def test_training_rejects_a_manifest_that_did_not_build_the_rows(dataset, manifest_path):
    mismatched = V2MarketTrainingDataset(rows=dataset.rows, audit={"manifest_sha256": "0" * 64})

    with pytest.raises(MarketTrainingError, match="manifest_sha256"):
        train_market_baseline(mismatched, manifest_path=manifest_path)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["partitions"]["test"] = ["2025-02-01", "2026-08-31"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    bounded = V2MarketTrainingDataset(
        rows=dataset.rows,
        audit={"manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
    )
    with pytest.raises(MarketTrainingError, match="partition boundaries"):
        train_market_baseline(bounded, manifest_path=manifest_path)
