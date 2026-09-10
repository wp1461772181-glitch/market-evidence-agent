"""Train and evaluate the fixed Week 4 market-feature baseline.

This module intentionally uses one small scikit-learn pipeline and fixed
walk-forward splits.  It is an offline experiment, not a production forecast
service: no test fold is used to choose a hyperparameter or a model version.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.dummy import DummyClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .training_data import FEATURE_COLUMNS, load_training_dataset


CLASS_LABELS = (0, 1, 2)
CLASS_NAMES = {0: "bearish", 1: "neutral", 2: "bullish"}
N_FOLDS = 3
TEST_SESSIONS = 84
CALIBRATION_SESSIONS = 63
CALIBRATION_METHOD = "sigmoid"


@dataclass(frozen=True)
class WalkForwardFold:
    """One expanding date-based training, calibration, and test split."""

    number: int
    train: pd.DataFrame
    calibration: pd.DataFrame
    test: pd.DataFrame
    train_window: tuple[date, date]
    calibration_window: tuple[date, date]
    test_window: tuple[date, date]


@dataclass(frozen=True)
class FoldResult:
    fold: WalkForwardFold
    report: dict[str, Any]
    predictions: pd.DataFrame
    raw_model: Pipeline
    calibrated_model: CalibratedClassifierCV


def build_walk_forward_folds(
    dataset: pd.DataFrame,
    *,
    n_folds: int = N_FOLDS,
    test_sessions: int = TEST_SESSIONS,
    calibration_sessions: int = CALIBRATION_SESSIONS,
) -> tuple[WalkForwardFold, ...]:
    """Return date-aligned folds with actual label-maturity purges.

    Roles are assigned by the unique trading-date sequence, so all symbols on
    a date always have the same role.  Rows whose labels were not available
    before the following role starts are purged instead of being silently used.
    """
    _require_columns(dataset, (*FEATURE_COLUMNS, "trading_date", "decision_time", "label_available_at", "target"))
    if n_folds < 1 or test_sessions < 1 or calibration_sessions < 1:
        raise ValueError("n_folds, test_sessions, and calibration_sessions must be positive")

    frame = dataset.copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"]).dt.date
    frame["decision_time"] = pd.to_datetime(frame["decision_time"], utc=True)
    frame["label_available_at"] = pd.to_datetime(frame["label_available_at"], utc=True)
    dates = tuple(sorted(frame["trading_date"].unique()))
    required_dates = n_folds * test_sessions + calibration_sessions + 1
    if len(dates) < required_dates:
        raise ValueError(
            f"need at least {required_dates} unique trading dates for {n_folds} folds; got {len(dates)}"
        )

    folds: list[WalkForwardFold] = []
    first_test_index = len(dates) - n_folds * test_sessions
    for number in range(1, n_folds + 1):
        test_start_index = first_test_index + (number - 1) * test_sessions
        calibration_start_index = test_start_index - calibration_sessions
        if calibration_start_index <= 0:
            raise ValueError("not enough dates before the first test block for a training window")

        train_dates = dates[:calibration_start_index]
        calibration_dates = dates[calibration_start_index:test_start_index]
        test_dates = dates[test_start_index : test_start_index + test_sessions]
        calibration_start = _earliest_decision_time(frame, calibration_dates, "calibration")
        test_start = _earliest_decision_time(frame, test_dates, "test")

        train = frame[frame["trading_date"].isin(train_dates)]
        calibration = frame[frame["trading_date"].isin(calibration_dates)]
        test = frame[frame["trading_date"].isin(test_dates)]
        train = train[train["label_available_at"] < calibration_start].copy()
        calibration = calibration[calibration["label_available_at"] < test_start].copy()

        if train.empty or calibration.empty or test.empty:
            raise ValueError(f"fold {number} is empty after the label-maturity purge")
        _assert_no_cross_role_dates(train, calibration, test, number)
        _assert_mature_before(train, calibration_start, f"fold {number} training")
        _assert_mature_before(calibration, test_start, f"fold {number} calibration")
        folds.append(
            WalkForwardFold(
                number=number,
                train=train.sort_values(["trading_date", "symbol"]).reset_index(drop=True),
                calibration=calibration.sort_values(["trading_date", "symbol"]).reset_index(drop=True),
                test=test.sort_values(["trading_date", "symbol"]).reset_index(drop=True),
                train_window=(train_dates[0], train_dates[-1]),
                calibration_window=(calibration_dates[0], calibration_dates[-1]),
                test_window=(test_dates[0], test_dates[-1]),
            )
        )
    return tuple(folds)


def evaluate_fold(fold: WalkForwardFold) -> FoldResult:
    """Fit fixed models for one fold and score only its held-out test block."""
    _require_all_classes(fold.train, f"fold {fold.number} training")
    _require_all_classes(fold.calibration, f"fold {fold.number} calibration")
    raw_model = _new_logistic_pipeline()
    raw_model.fit(_features(fold.train), _targets(fold.train))
    calibrated_model = CalibratedClassifierCV(
        estimator=FrozenEstimator(raw_model),
        method=CALIBRATION_METHOD,
        ensemble="auto",
    )
    calibrated_model.fit(_features(fold.calibration), _targets(fold.calibration))

    fitted_models: dict[str, Any] = {
        "logistic_raw": raw_model,
        "logistic_calibrated": calibrated_model,
        "baseline_class_prior": DummyClassifier(strategy="prior").fit(
            _features(fold.train), _targets(fold.train)
        ),
        "baseline_majority_class": DummyClassifier(strategy="most_frequent").fit(
            _features(fold.train), _targets(fold.train)
        ),
    }
    reports: dict[str, Any] = {}
    prediction_frames: list[pd.DataFrame] = []
    for name, model in fitted_models.items():
        probabilities = _probabilities_in_order(model, _features(fold.test))
        reports[name] = _score_predictions(_targets(fold.test), probabilities)
        prediction_frames.append(_prediction_frame(fold, name, probabilities))

    momentum_probabilities = momentum_baseline_probabilities(fold.test)
    reports["baseline_relative_momentum"] = _score_predictions(_targets(fold.test), momentum_probabilities)
    prediction_frames.append(_prediction_frame(fold, "baseline_relative_momentum", momentum_probabilities))

    report = {
        "fold": fold.number,
        "windows": {
            "train": _window_payload(fold.train_window),
            "calibration": _window_payload(fold.calibration_window),
            "test": _window_payload(fold.test_window),
        },
        "row_counts": {
            "train": len(fold.train),
            "calibration": len(fold.calibration),
            "test": len(fold.test),
        },
        "models": reports,
    }
    return FoldResult(
        fold=fold,
        report=report,
        predictions=pd.concat(prediction_frames, ignore_index=True),
        raw_model=raw_model,
        calibrated_model=calibrated_model,
    )


def run_evaluation(dataset: pd.DataFrame, metadata: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame, CalibratedClassifierCV]:
    """Run the pre-declared evaluation and return a JSON-ready report."""
    folds = build_walk_forward_folds(dataset)
    results = tuple(evaluate_fold(fold) for fold in folds)
    oos_predictions = pd.concat([result.predictions for result in results], ignore_index=True)
    pooled = {
        model: _score_predictions(
            group["target"].to_numpy(dtype=int),
            group[["probability_bearish", "probability_neutral", "probability_bullish"]].to_numpy(dtype=float),
        )
        for model, group in oos_predictions.groupby("model", sort=True)
    }
    report = {
        "experiment": {
            "name": "week4-fixed-logistic-walk-forward-v1",
            "purpose": "Offline baseline evaluation only; no test-fold model selection or hyperparameter search.",
            "feature_columns": list(FEATURE_COLUMNS),
            "class_mapping": {str(key): value for key, value in CLASS_NAMES.items()},
            "split_design": {
                "n_folds": N_FOLDS,
                "test_sessions_per_fold": TEST_SESSIONS,
                "calibration_sessions_before_purge": CALIBRATION_SESSIONS,
                "purge_rule": "label_available_at must be strictly earlier than the next role's earliest decision_time",
            },
            "models": {
                "logistic_raw": "Pipeline(StandardScaler, LogisticRegression(C=1, max_iter=1000, random_state=42))",
                "logistic_calibrated": "sigmoid CalibratedClassifierCV(FrozenEstimator(fitted raw pipeline)) on the held-out calibration block",
                "baseline_class_prior": "DummyClassifier(strategy=prior)",
                "baseline_majority_class": "DummyClassifier(strategy=most_frequent)",
                "baseline_relative_momentum": "relative_return_20d compared with each row's precomputed label threshold",
            },
        },
        "data_metadata": _json_safe(metadata),
        "folds": [result.report for result in results],
        "pooled_oos": pooled,
    }
    return report, oos_predictions, results[-1].calibrated_model


def momentum_baseline_probabilities(dataset: pd.DataFrame) -> np.ndarray:
    """Convert the already-known relative 20-day feature into a one-hot baseline."""
    _require_columns(dataset, ("relative_return_20d", "label_threshold"))
    relative_returns = dataset["relative_return_20d"].to_numpy(dtype=float)
    thresholds = dataset["label_threshold"].to_numpy(dtype=float)
    predictions = np.where(relative_returns > thresholds, 2, np.where(relative_returns < -thresholds, 0, 1))
    return np.eye(len(CLASS_LABELS), dtype=float)[predictions]


def save_evaluation(
    *,
    output_dir: Path,
    dataset: pd.DataFrame,
    report: dict[str, Any],
    oos_predictions: pd.DataFrame,
    calibrated_model: CalibratedClassifierCV,
) -> dict[str, Path]:
    """Persist a reviewable local run and verify the saved model reloads."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "dataset": output_dir / "training_dataset.csv",
        "predictions": output_dir / "oos_predictions.csv",
        "report": output_dir / "report.json",
        "model": output_dir / "last_fold_calibrated_model.joblib",
        "manifest": output_dir / "manifest.json",
    }
    dataset.to_csv(paths["dataset"], index=False)
    oos_predictions.to_csv(paths["predictions"], index=False)
    joblib.dump(calibrated_model, paths["model"])
    reloaded_model = joblib.load(paths["model"])
    last_model_rows = oos_predictions[oos_predictions["model"] == "logistic_calibrated"]
    last_fold = int(last_model_rows["fold"].max())
    last_fold_rows = last_model_rows[last_model_rows["fold"] == last_fold]
    expected = last_fold_rows[["probability_bearish", "probability_neutral", "probability_bullish"]].to_numpy()
    actual = _probabilities_in_order(reloaded_model, last_fold_rows.loc[:, list(FEATURE_COLUMNS)])
    if not np.allclose(expected, actual, rtol=0, atol=1e-12):
        raise RuntimeError("reloaded calibrated model did not reproduce its saved probabilities")

    report = dict(report)
    report["saved_model_reload_verified"] = True
    paths["report"].write_text(json.dumps(_json_safe(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "artifact_version": "week4-training-artifacts-v1",
        "model_file": paths["model"].name,
        "feature_order": list(FEATURE_COLUMNS),
        "classes": list(CLASS_LABELS),
        "library_versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "joblib": joblib.__version__,
        },
        "last_fold": report["folds"][-1],
        "data_metadata": report["data_metadata"],
        "saved_model_reload_verified": True,
    }
    paths["manifest"].write_text(json.dumps(_json_safe(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return paths


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path, help="Week 3 JSON feature export")
    parser.add_argument("--output-dir", required=True, type=Path, help="New or empty local artifact directory")
    args = parser.parse_args(argv)
    dataset, metadata = load_training_dataset(args.features)
    report, oos_predictions, calibrated_model = run_evaluation(dataset, metadata)
    paths = save_evaluation(
        output_dir=args.output_dir,
        dataset=dataset,
        report=report,
        oos_predictions=oos_predictions,
        calibrated_model=calibrated_model,
    )
    print(json.dumps({key: str(path) for key, path in paths.items()}, sort_keys=True))


def _new_logistic_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            ("logistic", LogisticRegression(C=1, max_iter=1000, random_state=42)),
        ]
    )


def _score_predictions(target: np.ndarray, probabilities: np.ndarray) -> dict[str, Any]:
    predictions = np.asarray(CLASS_LABELS)[np.argmax(probabilities, axis=1)]
    reliability: dict[str, Any] = {}
    for index, class_id in enumerate(CLASS_LABELS):
        fraction_positive, mean_predicted = calibration_curve(
            (target == class_id).astype(int), probabilities[:, index], n_bins=5, strategy="uniform"
        )
        reliability[CLASS_NAMES[class_id]] = {
            "fraction_positive": fraction_positive.tolist(),
            "mean_predicted_value": mean_predicted.tolist(),
        }
    return {
        "accuracy": float(accuracy_score(target, predictions)),
        "balanced_accuracy": float(balanced_accuracy_score(target, predictions)),
        "macro_f1": float(f1_score(target, predictions, labels=CLASS_LABELS, average="macro", zero_division=0)),
        "brier_multiclass": float(brier_score_loss(target, probabilities, labels=CLASS_LABELS, scale_by_half=False)),
        "log_loss": float(log_loss(target, probabilities, labels=CLASS_LABELS)),
        "confusion_matrix": confusion_matrix(target, predictions, labels=CLASS_LABELS).tolist(),
        "reliability_bins": reliability,
    }


def _prediction_frame(fold: WalkForwardFold, model_name: str, probabilities: np.ndarray) -> pd.DataFrame:
    result = fold.test.copy()
    result["fold"] = fold.number
    result["model"] = model_name
    result["predicted_target"] = np.asarray(CLASS_LABELS)[np.argmax(probabilities, axis=1)]
    result["probability_bearish"] = probabilities[:, 0]
    result["probability_neutral"] = probabilities[:, 1]
    result["probability_bullish"] = probabilities[:, 2]
    return result


def _probabilities_in_order(model: Any, features: pd.DataFrame) -> np.ndarray:
    raw_probabilities = model.predict_proba(features)
    ordered = np.zeros((len(features), len(CLASS_LABELS)), dtype=float)
    for index, class_id in enumerate(model.classes_):
        if int(class_id) not in CLASS_LABELS:
            raise ValueError(f"unexpected model class: {class_id}")
        ordered[:, CLASS_LABELS.index(int(class_id))] = raw_probabilities[:, index]
    if not np.allclose(ordered.sum(axis=1), 1.0):
        raise ValueError("model probabilities do not sum to one")
    return ordered


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[:, list(FEATURE_COLUMNS)]


def _targets(frame: pd.DataFrame) -> np.ndarray:
    return frame["target"].to_numpy(dtype=int)


def _require_all_classes(frame: pd.DataFrame, context: str) -> None:
    observed = set(_targets(frame))
    missing = set(CLASS_LABELS) - observed
    if missing:
        raise ValueError(f"{context} is missing required target classes: {sorted(missing)}")


def _require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"dataset is missing columns: {sorted(missing)}")


def _earliest_decision_time(frame: pd.DataFrame, dates: Iterable[date], role: str) -> pd.Timestamp:
    selected = frame[frame["trading_date"].isin(dates)]["decision_time"]
    if selected.empty:
        raise ValueError(f"{role} date block has no rows")
    return selected.min()


def _assert_no_cross_role_dates(train: pd.DataFrame, calibration: pd.DataFrame, test: pd.DataFrame, number: int) -> None:
    roles = (set(train["trading_date"]), set(calibration["trading_date"]), set(test["trading_date"]))
    if roles[0] & roles[1] or roles[0] & roles[2] or roles[1] & roles[2]:
        raise AssertionError(f"fold {number} has an overlapping date across roles")


def _assert_mature_before(frame: pd.DataFrame, boundary: pd.Timestamp, context: str) -> None:
    if not (frame["label_available_at"] < boundary).all():
        raise AssertionError(f"{context} contains a label unavailable at the following boundary")


def _window_payload(window: tuple[date, date]) -> dict[str, str]:
    return {"start": window[0].isoformat(), "end": window[1].isoformat()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


if __name__ == "__main__":
    main()
