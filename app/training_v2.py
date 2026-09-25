"""Fit and preserve the V2 market-only comparison baseline.

The V2 baseline deliberately has a narrow job: it learns only from frozen
market root rows.  It is an offline, historical-research artifact and does
not claim that evidence has entered the numerical model.  In particular,
``joint_probabilities`` are never produced here.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .training_data_v2 import V2MarketTrainingDataset, V2MarketTrainingRow
from .forecast_contract import (
    HORIZON_SESSIONS,
    PRICE_BASIS,
    TARGET_SPEC_VERSION,
    absolute_return,
    classify_absolute_return,
    future_xnys_sessions,
)


ARTIFACT_VERSION = "v2-market-training-artifact-v1"
CLASS_LABELS = ("bearish", "neutral", "bullish")
MARKET_FEATURE_COLUMNS = (
    "momentum_5d",
    "momentum_20d",
    "volatility_20d",
    "volume_ratio_20d",
    "drawdown_20d",
    "relative_return_20d",
    "remaining_sessions",
    "realized_return_from_anchor",
)
PARTITIONS = ("train", "calibration", "test")
MODEL_FILENAME = "market_model.joblib"
MANIFEST_FILENAME = "manifest.json"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = _PROJECT_ROOT / "artifacts" / "v2"


class MarketTrainingError(ValueError):
    """A V2 market-training contract was not safe to execute."""


@dataclass(frozen=True)
class MarketTrainingPlan:
    """Read-only account of the frozen root rows that a run would consume."""

    row_counts: dict[str, int]
    class_counts: dict[str, dict[str, int]]
    feature_columns: tuple[str, ...]
    root_row_contract: dict[str, Any]


@dataclass(frozen=True)
class MarketTrainingResult:
    """One fixed train/calibration/test run before it is serialized."""

    model: CalibratedClassifierCV
    plan: MarketTrainingPlan
    test_metrics: dict[str, dict[str, float]]
    dataset_rows_sha256: str
    data_manifest_sha256: str
    data_manifest_summary: dict[str, Any]
    feature_versions: tuple[str, ...]


@dataclass(frozen=True)
class TrustedMarketArtifact:
    """A hash-checked model loaded only from an explicitly trusted root."""

    model: CalibratedClassifierCV
    path: Path
    model_sha256: str
    manifest_sha256: str
    manifest: dict[str, Any]


def build_market_training_plan(dataset: V2MarketTrainingDataset) -> MarketTrainingPlan:
    """Validate and describe root rows without fitting a model."""
    frame = _root_frame(dataset.rows)
    row_counts = {partition: int((frame["partition"] == partition).sum()) for partition in PARTITIONS}
    class_counts = {
        partition: {
            label: int(((frame["partition"] == partition) & (frame["label"] == label)).sum())
            for label in CLASS_LABELS
        }
        for partition in PARTITIONS
    }
    return MarketTrainingPlan(
        row_counts=row_counts,
        class_counts=class_counts,
        feature_columns=MARKET_FEATURE_COLUMNS,
        root_row_contract={"remaining_sessions": 20, "realized_return_from_anchor": 0.0},
    )


def train_market_baseline(
    dataset: V2MarketTrainingDataset,
    *,
    manifest_path: str | Path,
) -> MarketTrainingResult:
    """Fit on train, calibrate only on calibration, then score test once.

    This function intentionally has no parameter-search or test-driven branch.
    Its sole model choice is the predeclared imputer/scaler/logistic pipeline
    with sigmoid calibration on the independently held-out calibration rows.
    """
    plan = build_market_training_plan(dataset)
    _require_complete_three_class_partitions(plan)
    frame = _root_frame(dataset.rows)
    train = _partition_frame(frame, "train")
    calibration = _partition_frame(frame, "calibration")
    test = _partition_frame(frame, "test")

    raw_model = _new_market_pipeline()
    raw_model.fit(_features(train), _targets(train))
    calibrated_model = CalibratedClassifierCV(
        # FrozenEstimator prevents any calibration fold from refitting the
        # train-fitted market pipeline.  sklearn's supported 1.7 API learns
        # calibration from the held-out calibration rows only.
        estimator=FrozenEstimator(raw_model), method="sigmoid", ensemble="auto"
    )
    calibrated_model.fit(_features(calibration), _targets(calibration))

    # Test is deliberately reached only after both fitting stages are complete.
    # Neither fitting object receives test features or labels.
    probabilities = _ordered_probabilities(calibrated_model, _features(test))
    prior = _class_prior(train)
    prior_probabilities = np.tile(prior, (len(test), 1))
    test_target = _targets(test)
    metrics = {
        "baseline_class_prior": _score_probabilities(test_target, prior_probabilities),
        "market_logistic_calibrated": _score_probabilities(test_target, probabilities),
    }

    manifest_bytes, manifest_summary = _read_training_manifest(manifest_path)
    _validate_manifest_matches_dataset(dataset, manifest_bytes, manifest_summary)
    return MarketTrainingResult(
        model=calibrated_model,
        plan=plan,
        test_metrics=metrics,
        dataset_rows_sha256=_dataset_rows_sha256(dataset.rows),
        data_manifest_sha256=_sha256(manifest_bytes),
        data_manifest_summary=manifest_summary,
        feature_versions=tuple(sorted({row.feature_version for row in dataset.rows})),
    )


def write_market_artifact(
    result: MarketTrainingResult,
    *,
    artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
    run_id: str,
) -> Path:
    """Append a new immutable V2 market artifact and return its directory."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise MarketTrainingError("run_id must contain only letters, numbers, dot, underscore, or dash")
    root = Path(artifact_root)
    output_dir = root / f"market-{run_id}"
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite existing V2 artifact: {output_dir}") from exc

    model_path = output_dir / MODEL_FILENAME
    try:
        joblib.dump(result.model, model_path)
        model_sha256 = _sha256(model_path.read_bytes())
        manifest = {
            "artifact_version": ARTIFACT_VERSION,
            "artifact_kind": "market_only",
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "available_at": datetime.now(UTC).isoformat(),
            "availability": {
                "kind": "historical_research_artifact",
                "prospective_eligible": False,
                "reason": "Historical frozen data supports research only; deployment needs a separate release decision.",
            },
            "model_file": MODEL_FILENAME,
            "model_sha256": model_sha256,
            "feature_contract": {
                "feature_columns": list(MARKET_FEATURE_COLUMNS),
                "feature_versions": list(result.feature_versions),
                "imputation": "SimpleImputer(strategy=median), fit on train only",
                "scaling": "StandardScaler(), fit on train only",
            },
            "target_contract": {
                "target_spec_version": result.data_manifest_summary.get("target_spec_version"),
                "price_basis": result.data_manifest_summary.get("price_basis"),
                "classes": list(CLASS_LABELS),
                "definition": "absolute 20-session stock price return with inclusive neutral boundaries",
            },
            "split_contract": {
                "partitions": list(PARTITIONS),
                "row_counts": result.plan.row_counts,
                "class_counts": result.plan.class_counts,
                "root_row_contract": result.plan.root_row_contract,
                "calibration": "sigmoid calibration of a frozen train-fitted pipeline on calibration only",
                "test": "scored once after fixed training and calibration; never used for fitting or selection",
            },
            "data_manifest": result.data_manifest_summary,
            "data_manifest_sha256": result.data_manifest_sha256,
            "dataset_rows_sha256": result.dataset_rows_sha256,
            "model_status": "research_only",
            "supported_channels": {"market": True, "official_evidence": False, "uploaded_media": False},
            "test_metrics": result.test_metrics,
            "joint_probabilities": None,
            "library_versions": _library_versions(),
        }
        manifest_path = output_dir / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Verify the exact persisted bytes through the safe loader before
        # declaring this artifact usable.
        load_trusted_market_artifact(output_dir, trusted_root=root)
    except Exception:
        # Never delete a partially written directory: append-only storage keeps
        # the failure inspectable and still refuses reuse of its run id.
        raise
    return output_dir


def load_trusted_market_artifact(
    artifact_path: str | Path,
    *,
    trusted_root: str | Path = DEFAULT_ARTIFACT_ROOT,
) -> TrustedMarketArtifact:
    """Load a hash-checked artifact from the local trusted artifact root.

    ``joblib`` can execute code while deserializing.  Hash checking detects
    corruption but does not establish provenance, so this loader refuses paths
    outside the repository-controlled (or explicitly configured) artifact root.
    """
    root = Path(trusted_root).resolve()
    requested = Path(artifact_path)
    if requested.is_symlink():
        raise MarketTrainingError("trusted artifact directory cannot be a symlink")
    location = requested.resolve()
    if location.parent != root or not location.name.startswith("market-"):
        raise MarketTrainingError("artifact must be a direct market-* child of the trusted artifact root")
    if not location.is_dir():
        raise MarketTrainingError("trusted artifact directory is unavailable")
    manifest_path = location / MANIFEST_FILENAME
    model_path = location / MODEL_FILENAME
    if not manifest_path.is_file() or not model_path.is_file() or manifest_path.is_symlink() or model_path.is_symlink():
        raise MarketTrainingError("trusted artifact is missing regular manifest or model files")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise MarketTrainingError("trusted artifact manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("artifact_version") != ARTIFACT_VERSION:
        raise MarketTrainingError("trusted artifact manifest has an unsupported version")
    if manifest.get("artifact_kind") != "market_only" or manifest.get("model_file") != MODEL_FILENAME:
        raise MarketTrainingError("trusted artifact manifest has an invalid model contract")
    _validate_loaded_manifest_contract(manifest)
    expected_hash = manifest.get("model_sha256")
    actual_hash = _sha256(model_path.read_bytes())
    if not isinstance(expected_hash, str) or not _is_sha256(expected_hash) or expected_hash != actual_hash:
        raise MarketTrainingError("trusted artifact model hash does not match its manifest")
    if manifest.get("joint_probabilities") is not None:
        raise MarketTrainingError("market-only artifact must not expose joint probabilities")
    model = joblib.load(model_path)
    if not isinstance(model, CalibratedClassifierCV):
        raise MarketTrainingError("trusted artifact model is not the expected calibrated classifier")
    if tuple(model.classes_) != tuple(sorted(CLASS_LABELS)):
        raise MarketTrainingError("trusted artifact model classes are incompatible with the V2 market contract")
    return TrustedMarketArtifact(
        model=model,
        path=location,
        model_sha256=actual_hash,
        manifest_sha256=_sha256(manifest_bytes),
        manifest=manifest,
    )


def replay_market_probabilities(
    artifact: TrustedMarketArtifact,
    feature_values: Mapping[str, Any],
) -> dict[str, float]:
    """Reproduce one artifact's marginal market probabilities exactly."""
    if set(feature_values) != set(MARKET_FEATURE_COLUMNS):
        missing = sorted(set(MARKET_FEATURE_COLUMNS) - set(feature_values))
        extra = sorted(set(feature_values) - set(MARKET_FEATURE_COLUMNS))
        raise MarketTrainingError(f"feature values must exactly match market contract; missing={missing}, extra={extra}")
    frame = pd.DataFrame([[feature_values[name] for name in MARKET_FEATURE_COLUMNS]], columns=MARKET_FEATURE_COLUMNS)
    probabilities = _ordered_probabilities(artifact.model, frame)[0]
    return {label: float(probabilities[index]) for index, label in enumerate(CLASS_LABELS)}


def _root_frame(rows: Sequence[V2MarketTrainingRow]) -> pd.DataFrame:
    if not rows:
        raise MarketTrainingError("V2 market dataset has no rows")
    frame = pd.DataFrame([asdict(row) for row in rows])
    required = {"symbol", "partition", "anchor_date", "label", *MARKET_FEATURE_COLUMNS}
    missing = required - set(frame.columns)
    if missing:
        raise MarketTrainingError(f"V2 market rows are missing columns: {sorted(missing)}")
    invalid_partitions = set(frame["partition"]) - set(PARTITIONS)
    if invalid_partitions:
        raise MarketTrainingError(f"V2 market rows have invalid partitions: {sorted(invalid_partitions)}")
    invalid_labels = set(frame["label"]) - set(CLASS_LABELS)
    if invalid_labels:
        raise MarketTrainingError(f"V2 market rows have invalid labels: {sorted(invalid_labels)}")
    if not (frame["remaining_sessions"] == 20).all() or not (frame["realized_return_from_anchor"] == 0.0).all():
        raise MarketTrainingError("P3 accepts only initial root rows (remaining_sessions=20, realized_return_from_anchor=0)")
    if frame.duplicated(["symbol", "anchor_date"]).any():
        raise MarketTrainingError("V2 market root rows duplicate a symbol and anchor_date")
    for row in rows:
        expected_target = future_xnys_sessions(row.anchor_date, HORIZON_SESSIONS)[-1]
        if row.target_end_date != expected_target:
            raise MarketTrainingError("V2 market root target_end_date is not the fixed twentieth XNYS session")
        try:
            expected_return = absolute_return(anchor_close=row.anchor_close, target_close=row.target_close)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise MarketTrainingError("V2 market root has invalid anchor or target close") from exc
        if not math.isclose(row.absolute_return, expected_return, rel_tol=0.0, abs_tol=1e-12):
            raise MarketTrainingError("V2 market root absolute_return does not match its frozen closes")
        if row.label != classify_absolute_return(expected_return):
            raise MarketTrainingError("V2 market root label does not match the absolute-return contract")
    return frame.loc[:, ["symbol", "partition", "anchor_date", "label", *MARKET_FEATURE_COLUMNS]].copy()


def _partition_frame(frame: pd.DataFrame, partition: str) -> pd.DataFrame:
    selected = frame.loc[frame["partition"] == partition].copy()
    if selected.empty:
        raise MarketTrainingError(f"V2 market {partition} partition is empty")
    return selected


def _require_complete_three_class_partitions(plan: MarketTrainingPlan) -> None:
    for partition, count in plan.row_counts.items():
        if count == 0:
            raise MarketTrainingError(f"V2 market {partition} partition is empty")
        missing = [label for label, value in plan.class_counts[partition].items() if value == 0]
        if missing:
            raise MarketTrainingError(f"V2 market {partition} partition is missing classes: {missing}")


def _new_market_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("logistic", LogisticRegression(C=1, max_iter=1000, random_state=42)),
        ]
    )


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[:, list(MARKET_FEATURE_COLUMNS)]


def _targets(frame: pd.DataFrame) -> np.ndarray:
    return frame["label"].to_numpy(dtype=str)


def _class_prior(train: pd.DataFrame) -> np.ndarray:
    counts = Counter(_targets(train))
    probabilities = np.array([counts[label] / len(train) for label in CLASS_LABELS], dtype=float)
    return probabilities


def _ordered_probabilities(model: Any, features: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(features)
    ordered = np.zeros((len(features), len(CLASS_LABELS)), dtype=float)
    for index, label in enumerate(model.classes_):
        if label not in CLASS_LABELS:
            raise MarketTrainingError(f"unexpected model class: {label}")
        ordered[:, CLASS_LABELS.index(label)] = raw[:, index]
    if not np.all(np.isfinite(ordered)) or not np.allclose(ordered.sum(axis=1), 1.0, rtol=0.0, atol=1e-12):
        raise MarketTrainingError("market model probabilities are invalid")
    return ordered


def _score_probabilities(target: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    predicted = np.asarray(CLASS_LABELS)[np.argmax(probabilities, axis=1)]
    target_indices = np.asarray([CLASS_LABELS.index(label) for label in target], dtype=int)
    one_hot = np.eye(len(CLASS_LABELS), dtype=float)[target_indices]
    return {
        "accuracy": float(accuracy_score(target, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(target, predicted)),
        "macro_f1": float(f1_score(target, predicted, labels=CLASS_LABELS, average="macro", zero_division=0)),
        "brier_multiclass": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        # sklearn expects its labels and probability columns in lexical order.
        "log_loss": float(
            log_loss(
                target,
                probabilities[:, [CLASS_LABELS.index(label) for label in sorted(CLASS_LABELS)]],
                labels=sorted(CLASS_LABELS),
            )
        ),
    }


def _read_training_manifest(manifest_path: str | Path) -> tuple[bytes, dict[str, Any]]:
    location = Path(manifest_path)
    try:
        raw = location.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketTrainingError(f"cannot read V2 data manifest: {location}") from exc
    if not isinstance(payload, dict):
        raise MarketTrainingError("V2 data manifest must be a JSON object")
    return raw, _manifest_summary(payload)


def _manifest_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the source, availability, feature, target, and split contract reviewable."""
    def files(name: str) -> list[dict[str, Any]]:
        entries = payload.get(name, [])
        if not isinstance(entries, list):
            return []
        allowed = ("filename", "symbol", "sha256", "published_at", "observed_at", "extracted_at", "available_at")
        return [{key: entry[key] for key in allowed if isinstance(entry, Mapping) and key in entry} for entry in entries]

    return {
        "schema_version": payload.get("schema_version"),
        "mode": payload.get("mode"),
        "symbols": payload.get("symbols"),
        "training_range": payload.get("training_range"),
        "target_spec_version": payload.get("target_spec_version"),
        "price_basis": payload.get("price_basis"),
        "partitions": payload.get("partitions"),
        "price_snapshot_files": files("price_snapshot_files"),
        "source_snapshot_files": files("source_snapshot_files"),
    }


def _validate_manifest_matches_dataset(
    dataset: V2MarketTrainingDataset, manifest_bytes: bytes, summary: Mapping[str, Any]
) -> None:
    expected_hash = dataset.audit.get("manifest_sha256")
    actual_hash = _sha256(manifest_bytes)
    if not isinstance(expected_hash, str) or not _is_sha256(expected_hash) or expected_hash != actual_hash:
        raise MarketTrainingError("dataset audit manifest_sha256 does not match the supplied data manifest")
    if summary.get("target_spec_version") != TARGET_SPEC_VERSION or summary.get("price_basis") != PRICE_BASIS:
        raise MarketTrainingError("data manifest target or price-basis contract is incompatible with V2 market rows")
    symbols = summary.get("symbols")
    if not isinstance(symbols, list) or {row.symbol for row in dataset.rows} - set(symbols):
        raise MarketTrainingError("data manifest symbols do not cover the V2 market rows")
    partitions = summary.get("partitions")
    if not isinstance(partitions, Mapping):
        raise MarketTrainingError("data manifest has no partition contract")
    for row in dataset.rows:
        bounds = partitions.get(row.partition)
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise MarketTrainingError(f"data manifest partition is invalid: {row.partition}")
        try:
            start, end = (datetime.fromisoformat(f"{value}T00:00:00+00:00").date() for value in bounds)
        except (TypeError, ValueError) as exc:
            raise MarketTrainingError(f"data manifest partition has invalid dates: {row.partition}") from exc
        if not start <= row.anchor_date <= end or not start <= row.target_end_date <= end:
            raise MarketTrainingError("V2 market rows do not match their manifest partition boundaries")


def _validate_loaded_manifest_contract(manifest: Mapping[str, Any]) -> None:
    feature_contract = manifest.get("feature_contract")
    target_contract = manifest.get("target_contract")
    split_contract = manifest.get("split_contract")
    if not isinstance(feature_contract, Mapping) or feature_contract.get("feature_columns") != list(MARKET_FEATURE_COLUMNS):
        raise MarketTrainingError("trusted artifact feature contract is incompatible with V2 market inference")
    if not isinstance(target_contract, Mapping) or target_contract.get("target_spec_version") != TARGET_SPEC_VERSION:
        raise MarketTrainingError("trusted artifact target contract is incompatible with V2 market inference")
    if target_contract.get("price_basis") != PRICE_BASIS or target_contract.get("classes") != list(CLASS_LABELS):
        raise MarketTrainingError("trusted artifact price basis or class contract is incompatible with V2 market inference")
    if not isinstance(split_contract, Mapping) or split_contract.get("partitions") != list(PARTITIONS):
        raise MarketTrainingError("trusted artifact split contract is incompatible with V2 market inference")
    if manifest.get("model_status") != "research_only":
        raise MarketTrainingError("trusted artifact has an unsupported V2 market model status")


def _dataset_rows_sha256(rows: Sequence[V2MarketTrainingRow]) -> str:
    payload = [row.as_dict() for row in rows]
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _library_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
        "joblib": joblib.__version__,
    }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))
