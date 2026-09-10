from __future__ import annotations

from datetime import UTC, datetime, time

import numpy as np
import pandas as pd
import pytest

from app.training import (
    CLASS_LABELS,
    _probabilities_in_order,
    _score_predictions,
    build_walk_forward_folds,
    evaluate_fold,
    run_evaluation,
    save_evaluation,
)
from app.training_data import FEATURE_COLUMNS


def _dataset(days: int = 430) -> pd.DataFrame:
    rows = []
    symbols = ("AAPL", "AMZN", "GOOGL", "MSFT", "NVDA")
    sessions = pd.bdate_range("2023-01-03", periods=days)
    for day_index, session in enumerate(sessions):
        decision_time = pd.Timestamp(datetime.combine(session.date(), time(21), tzinfo=UTC))
        for symbol_index, symbol in enumerate(symbols):
            target = (day_index + symbol_index) % 3
            rows.append(
                {
                    "symbol": symbol,
                    "trading_date": session.date(),
                    "decision_time": decision_time,
                    "label_end_date": (session + pd.offsets.BDay(20)).date(),
                    "label_available_at": decision_time + pd.offsets.BDay(20),
                    "target": target,
                    "forward_excess_return": (-0.03, 0.0, 0.03)[target],
                    "label_threshold": 0.01,
                    "momentum_5d": (symbol_index + 1) * 0.001,
                    "momentum_20d": (day_index % 11) * 0.001,
                    "volatility_20d": 0.2 + symbol_index * 0.01,
                    "volume_ratio_20d": 1.0 + (day_index % 3) * 0.1,
                    "drawdown_20d": -0.02 * (day_index % 4),
                    "relative_return_20d": (-0.02, 0.0, 0.02)[target],
                }
            )
    return pd.DataFrame(rows)


def test_walk_forward_purges_unavailable_labels_and_keeps_dates_in_one_role():
    dataset = _dataset()
    future_row = dataset.iloc[0].copy()
    future_row["symbol"] = "FUTURE"
    future_row["label_available_at"] = pd.Timestamp("2035-01-01", tz=UTC)
    dataset = pd.concat([dataset, pd.DataFrame([future_row])], ignore_index=True)

    folds = build_walk_forward_folds(dataset)

    assert len(folds) == 3
    all_test_dates = []
    for fold in folds:
        train_dates = set(fold.train.trading_date)
        calibration_dates = set(fold.calibration.trading_date)
        test_dates = set(fold.test.trading_date)
        assert not train_dates & calibration_dates
        assert not train_dates & test_dates
        assert not calibration_dates & test_dates
        assert len(test_dates) == 84
        assert (fold.train.label_available_at < fold.calibration.decision_time.min()).all()
        assert (fold.calibration.label_available_at < fold.test.decision_time.min()).all()
        assert "FUTURE" not in fold.train.symbol.tolist()
        all_test_dates.extend(test_dates)
    assert len(all_test_dates) == len(set(all_test_dates))


def test_fold_fits_scaler_only_on_its_training_rows():
    fold = build_walk_forward_folds(_dataset())[0]
    result = evaluate_fold(fold)

    scaler = result.raw_model.named_steps["scaler"]
    assert scaler.mean_ == pytest.approx(fold.train.loc[:, list(FEATURE_COLUMNS)].mean().to_numpy())
    assert result.predictions.model.nunique() == 5
    assert set(result.predictions.target) == set(CLASS_LABELS)


def test_probability_metrics_use_fixed_three_class_order():
    targets = np.array([0, 1, 2])
    probabilities = np.eye(3)

    metrics = _score_predictions(targets, probabilities)

    assert metrics["macro_f1"] == pytest.approx(1.0)
    assert metrics["brier_multiclass"] == pytest.approx(0.0)
    assert metrics["log_loss"] == pytest.approx(0.0)
    assert metrics["confusion_matrix"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_probability_columns_are_reordered_to_bearish_neutral_bullish():
    features = pd.DataFrame({column: [0.0, 1.0, 2.0] for column in FEATURE_COLUMNS})

    class OutOfOrderModel:
        classes_ = np.array([2, 0, 1])

        def predict_proba(self, _features):
            return np.repeat([[0.1, 0.2, 0.7]], len(_features), axis=0)

    probabilities = _probabilities_in_order(OutOfOrderModel(), features)

    assert probabilities.shape == (3, 3)
    assert probabilities[0] == pytest.approx([0.2, 0.7, 0.1])
    assert probabilities.sum(axis=1) == pytest.approx(np.ones(3))


def test_saved_last_fold_model_reloads_with_identical_probabilities(tmp_path):
    dataset = _dataset()
    report, predictions, calibrated_model = run_evaluation(dataset, {"source": "synthetic"})

    paths = save_evaluation(
        output_dir=tmp_path / "week4",
        dataset=dataset,
        report=report,
        oos_predictions=predictions,
        calibrated_model=calibrated_model,
    )

    assert all(path.exists() for path in paths.values())
    assert json_load(paths["report"])["saved_model_reload_verified"] is True
    with pytest.raises(FileExistsError):
        save_evaluation(
            output_dir=tmp_path / "week4",
            dataset=dataset,
            report=report,
            oos_predictions=predictions,
            calibrated_model=calibrated_model,
        )


def json_load(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))
