"""Point-in-time Jev probability learning from matured forward outcomes.

The local learner calibrates Jev's three probabilities; it does not train Jev,
rewrite its reasoning, or infer outcomes from historical filings. Only one
root forecast per target is eligible, and only observed-mode outcomes can
activate a calibrator. Historical replays remain visible as research data but
never influence live predictions.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session
from sklearn.linear_model import LogisticRegression

from .forecast_v2_models import ForecastEvaluationV2, ForecastVersionV2, JevCalibrationModelV2


CLASSES = ("bearish", "neutral", "bullish")
MIN_TRAINING_ROOTS = 420
MIN_TRAINING_PER_CLASS = 20
MIN_VALIDATION_PER_CLASS = 8
MIN_VALIDATION_MONTHS = 18
VALIDATION_FRACTION = 0.30
BOOTSTRAP_ROUNDS = 1_000
BOOTSTRAP_SEED = 42
ALGORITHM_VERSION = "jev-log-probability-calibrator-v1"


@dataclass(frozen=True)
class _Example:
    version_id: str
    root_id: str
    symbol: str
    decision_at: datetime
    month: str
    label: str
    probabilities: dict[str, float]


def run_jev_learning_cycle(*, db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Evaluate already persisted mature samples, train eligible cohorts once.

    The caller runs the ordinary point-in-time evaluator first. This function
    only consumes its persisted results; it never fetches prices or calls an
    external model.
    """
    instant = _utc(now or datetime.now(UTC))
    examples, cohort_meta, counts = _load_examples(db)
    cohort_results: list[dict[str, Any]] = []
    trained = activated = rejected = 0

    for cohort_key, cohort_examples in sorted(examples.items()):
        existing = db.scalar(
            select(JevCalibrationModelV2)
            .where(
                JevCalibrationModelV2.cohort_key == cohort_key,
                JevCalibrationModelV2.dataset_sha256 == _dataset_hash(cohort_examples),
            )
            .limit(1)
        )
        decision = "collecting"
        if existing is not None:
            decision = "active" if existing.status == "active" else "already_evaluated"
        elif _training_readiness(cohort_examples)["ready"]:
            candidate, validation = _fit_candidate(cohort_examples)
            active = _active_model(db, cohort_key)
            active_metrics = _score_existing(active, validation) if active is not None else None
            acceptance = _acceptance(
                candidate["metrics"],
                active_metrics,
                candidate_parameters=candidate["parameters"],
                active_model=active,
                validation=validation,
            )
            params = candidate["parameters"]
            params_hash = _digest(params)
            model = JevCalibrationModelV2(
                cohort_key=cohort_key,
                dataset_sha256=_dataset_hash(cohort_examples),
                status="rejected",
                sample_count=len(cohort_examples),
                training_manifest={
                    **cohort_meta[cohort_key],
                    "algorithm_version": ALGORITHM_VERSION,
                    "dataset_sha256": _dataset_hash(cohort_examples),
                    "sample_count": len(cohort_examples),
                    "split": candidate["split"],
                    "metrics": candidate["metrics"],
                    "active_model_metrics_on_same_holdout": active_metrics,
                    "acceptance": acceptance,
                    "created_at": instant.isoformat(),
                },
                model_parameters=params,
                parameters_sha256=params_hash,
            )
            db.add(model)
            db.flush()
            trained += 1
            if acceptance["eligible"]:
                if active is not None:
                    active.status = "retired"
                model.status = "active"
                model.activated_at = instant
                activated += 1
                decision = "activated"
            else:
                rejected += 1
                decision = "rejected"

        cohort_results.append({
            "cohort_key": cohort_key,
            "actual_model": cohort_meta[cohort_key]["actual_model"],
            "question_version": cohort_meta[cohort_key]["question_version"],
            "matured_roots": len(cohort_examples),
            "forecast_roots": cohort_meta[cohort_key]["forecast_roots"],
            "pending_roots": cohort_meta[cohort_key]["pending_roots"],
            "eligible_roots": len(cohort_examples),
            "decision": decision,
        })

    db.commit()
    return {
        "status": "succeeded",
        "evaluated_at": instant.isoformat(),
        "observed_mature_roots": counts["observed_mature_roots"],
        "observed_forecast_roots": counts["observed_forecast_roots"],
        "observed_pending_roots": counts["observed_pending_roots"],
        "historical_replay_mature_roots_excluded": counts["historical_mature_roots"],
        "observed_roots_missing_raw_probabilities": counts["missing_raw_probabilities"],
        "trained_candidates": trained,
        "activated_models": activated,
        "rejected_candidates": rejected,
        "cohorts": cohort_results,
    }


def jev_learning_status(*, db: Session, symbol: str | None = None) -> dict[str, Any]:
    """Read training readiness and active models without mutating the database."""
    examples, metadata, counts = _load_examples(db)
    active_models = list(db.scalars(
        select(JevCalibrationModelV2)
        .where(JevCalibrationModelV2.status == "active")
        .order_by(JevCalibrationModelV2.created_at.desc())
    ))
    active_by_cohort = {model.cohort_key: model for model in active_models}
    cohorts = []
    for key, rows in sorted(examples.items()):
        readiness = _training_readiness(rows)
        active = active_by_cohort.get(key)
        cohorts.append({
            **metadata[key],
            "matured_roots": len(rows),
            "training": readiness,
            "active_model": _active_payload(active),
        })

    # Include cohorts with only pending observations so the UI can explain
    # why the learner is waiting before the first 20-session outcomes mature.
    for key, meta in metadata.items():
        if key in examples:
            continue
        cohorts.append({
            **meta,
            "matured_roots": 0,
            "training": _training_readiness([]),
            "active_model": _active_payload(active_by_cohort.get(key)),
        })
    cohorts.sort(key=lambda item: (item["actual_model"], item["question_version"]))

    historical_count = counts["historical_mature_roots"]
    if symbol:
        normalized = symbol.strip().upper()
        cohorts = [cohort for cohort in cohorts if cohort["symbol_forecast_counts"].get(normalized, 0) > 0]
    return {
        "status": "ready" if any(item["active_model"] is not None for item in cohorts) else "collecting",
        "symbol": symbol.strip().upper() if symbol else None,
        "required_mature_roots": MIN_TRAINING_ROOTS,
        "validation_fraction": VALIDATION_FRACTION,
        "minimum_validation_months": MIN_VALIDATION_MONTHS,
        "observed_mature_roots": counts["observed_mature_roots"],
        "observed_forecast_roots": counts["observed_forecast_roots"],
        "observed_pending_roots": counts["observed_pending_roots"],
        "historical_replay_mature_roots_excluded": historical_count,
        "observed_roots_missing_raw_probabilities": counts["missing_raw_probabilities"],
        "cohorts": cohorts,
        "policy": (
            "Only matured observed-mode root forecasts from the same OpenRouter model, question version, and target contract "
            "are trained. A model activates only after a chronological holdout beats the training-period class-prior baseline, "
            "raw Jev, and any current calibrator; "
            "historical replays and revisions never train the live calibrator."
        ),
    }


def active_jev_calibrator(
    *, db: Session, actual_model: str, question_version: str, target_contract: dict[str, Any]
) -> JevCalibrationModelV2 | None:
    """Load the active observed-mode calibrator for one exact Jev cohort."""
    cohort_key = _cohort_key("openrouter", actual_model, question_version, target_contract)
    model = _active_model(db, cohort_key)
    if model is not None:
        _validate_model_record(model)
    return model


def apply_jev_calibrator(model: JevCalibrationModelV2, probabilities: dict[str, float]) -> dict[str, float]:
    """Apply a hash-verified softmax calibration model to one Jev vector."""
    _validate_model_record(model)
    params = model.model_parameters
    x = _feature_vector(probabilities)
    logits = np.asarray(params["intercept"], dtype=float) + np.asarray(params["coefficients"], dtype=float) @ x
    shifted = logits - float(np.max(logits))
    values = np.exp(shifted)
    values = values / values.sum()
    result = {label: float(values[index]) for index, label in enumerate(CLASSES)}
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in result.values()):
        raise ValueError("calibrator produced invalid probabilities")
    return result


def _load_examples(db: Session):
    versions = list(db.scalars(
        select(ForecastVersionV2)
        .where(ForecastVersionV2.model_status == "experimental_jev")
        .order_by(ForecastVersionV2.decision_at, ForecastVersionV2.id)
    ))
    evaluations = list(db.scalars(
        select(ForecastEvaluationV2)
        .order_by(ForecastEvaluationV2.forecast_version_id, ForecastEvaluationV2.result_version)
    ))
    latest_eval: dict[str, ForecastEvaluationV2] = {}
    for result in evaluations:
        latest_eval[str(result.forecast_version_id)] = result

    grouped: dict[str, list[_Example]] = defaultdict(list)
    metadata: dict[str, dict[str, Any]] = {}
    counts = Counter()
    for version in versions:
        if version.id != version.root_id:
            continue
        manifest = version.model_manifest if isinstance(version.model_manifest, dict) else {}
        mode = manifest.get("time_mode")
        result = latest_eval.get(str(version.id))
        if mode == "historical_research":
            if result is not None and result.status == "succeeded" and result.actual_label:
                counts["historical_mature_roots"] += 1
            continue
        if mode != "observed":
            continue
        provider = manifest.get("decision_provider")
        if not isinstance(provider, dict) or provider.get("provider") != "openrouter":
            continue
        actual_model = provider.get("actual_model")
        question_version = provider.get("question_version")
        if not isinstance(actual_model, str) or not actual_model.strip():
            continue
        if not isinstance(question_version, str) or not question_version.strip():
            continue
        contract = version.target_contract if isinstance(version.target_contract, dict) else {}
        key = _cohort_key("openrouter", actual_model, question_version, contract)
        meta = metadata.setdefault(key, {
            "cohort_key": key,
            "provider": "openrouter",
            "actual_model": actual_model,
            "question_version": question_version,
            "target_spec_version": contract.get("target_spec_version"),
            "forecast_roots": 0,
            "pending_roots": 0,
            "symbol_forecast_counts": {},
        })
        meta["forecast_roots"] += 1
        meta["symbol_forecast_counts"][version.symbol] = meta["symbol_forecast_counts"].get(version.symbol, 0) + 1
        counts["observed_forecast_roots"] += 1
        if result is None or result.status != "succeeded" or not result.actual_label:
            meta["pending_roots"] += 1
            counts["observed_pending_roots"] += 1
            continue
        counts["observed_mature_roots"] += 1
        raw = provider.get("raw_probabilities")
        if raw is None:
            # Old rows predate the local learner. They are known to contain raw
            # Jev values because no local correction was available at publish time.
            calibration = manifest.get("local_calibration")
            if isinstance(calibration, dict) and calibration.get("status") == "active":
                counts["missing_raw_probabilities"] += 1
                continue
            raw = version.decision_probabilities
        try:
            _feature_vector(raw)
        except (TypeError, ValueError, KeyError):
            counts["missing_raw_probabilities"] += 1
            continue
        grouped[key].append(_Example(
            version_id=str(version.id),
            root_id=str(version.root_id),
            symbol=version.symbol,
            decision_at=_utc(version.decision_at),
            month=version.decision_at.strftime("%Y-%m"),
            label=result.actual_label,
            probabilities={label: float(raw[label]) for label in CLASSES},
        ))
    for rows in grouped.values():
        rows.sort(key=lambda row: (row.decision_at, row.root_id))
    return grouped, metadata, counts


def _training_readiness(rows: list[_Example]) -> dict[str, Any]:
    months = sorted({row.month for row in rows})
    split_index = int(len(months) * (1.0 - VALIDATION_FRACTION))
    split_index = min(max(split_index, 1), max(1, len(months) - 1)) if len(months) > 1 else 0
    train_months = months[:split_index]
    validation_months = months[split_index:]
    train_rows = [row for row in rows if row.month in set(train_months)]
    validation_rows = [row for row in rows if row.month in set(validation_months)]
    train_counts = Counter(row.label for row in train_rows)
    validation_counts = Counter(row.label for row in validation_rows)
    reasons = []
    if len(rows) < MIN_TRAINING_ROOTS:
        reasons.append("需要更多已到期的真实观察预测")
    if len(validation_months) < MIN_VALIDATION_MONTHS:
        reasons.append("时间外验证期需要至少 18 个不同月份")
    if any(train_counts[label] < MIN_TRAINING_PER_CLASS for label in CLASSES):
        reasons.append("训练期至少需要每类 20 个成熟样本")
    if any(validation_counts[label] < MIN_VALIDATION_PER_CLASS for label in CLASSES):
        reasons.append("验证期至少需要每类 8 个成熟样本")
    return {
        "ready": not reasons,
        "matured_roots": len(rows),
        "required_roots": MIN_TRAINING_ROOTS,
        "training_roots": len(train_rows),
        "validation_roots": len(validation_rows),
        "training_months": len(train_months),
        "validation_months": len(validation_months),
        "training_class_counts": {label: train_counts.get(label, 0) for label in CLASSES},
        "validation_class_counts": {label: validation_counts.get(label, 0) for label in CLASSES},
        "reasons": reasons,
    }


def _fit_candidate(rows: list[_Example]):
    readiness = _training_readiness(rows)
    if not readiness["ready"]:
        raise ValueError("Jev cohort does not satisfy training readiness gates")
    cutoff = readiness["training_months"]
    months = sorted({row.month for row in rows})
    training_months = set(months[:cutoff])
    training = [row for row in rows if row.month in training_months]
    validation = [row for row in rows if row.month not in training_months]
    x_train = np.vstack([_feature_vector(row.probabilities) for row in training])
    y_train = np.asarray([CLASSES.index(row.label) for row in training], dtype=int)
    estimator = LogisticRegression(C=0.25, solver="lbfgs", max_iter=2_000, random_state=42)
    estimator.fit(x_train, y_train)
    parameters = {
        "algorithm_version": ALGORITHM_VERSION,
        "classes": list(CLASSES),
        "feature_definition": "centered natural log of Jev bearish/neutral/bullish probabilities; floor=1e-6",
        "regularization_c": 0.25,
        "coefficients": estimator.coef_.astype(float).tolist(),
        "intercept": estimator.intercept_.astype(float).tolist(),
    }
    validation_probs = [_predict(parameters, row.probabilities) for row in validation]
    raw_probs = [row.probabilities for row in validation]
    labels = [row.label for row in validation]
    months_by_row = [row.month for row in validation]
    training_counts = Counter(row.label for row in training)
    prior_probabilities = {label: training_counts[label] / len(training) for label in CLASSES}
    prior_probs = [dict(prior_probabilities) for _ in validation]
    metrics = {
        "raw_jev": _metrics(labels, raw_probs),
        "candidate": _metrics(labels, validation_probs),
        "training_prior_baseline": {
            **_metrics(labels, prior_probs),
            "class_counts": {label: training_counts[label] for label in CLASSES},
            "probabilities": prior_probabilities,
        },
        "candidate_brier_improvement_ci95": _cluster_bootstrap_improvement(
            labels, raw_probs, validation_probs, months_by_row
        ),
        "candidate_brier_improvement_vs_training_prior_ci95": _cluster_bootstrap_improvement(
            labels, prior_probs, validation_probs, months_by_row
        ),
        "validation_roots": len(validation),
        "validation_months": len({row.month for row in validation}),
    }
    return {"parameters": parameters, "metrics": metrics, "split": {
        "method": "chronological_by_calendar_month",
        "training_first_month": min(training_months),
        "training_last_month": max(training_months),
        "validation_first_month": min(row.month for row in validation),
        "validation_last_month": max(row.month for row in validation),
        "training_roots": len(training),
        "validation_roots": len(validation),
        "training_class_counts": {label: int((y_train == index).sum()) for index, label in enumerate(CLASSES)},
    }}, validation


def _acceptance(
    candidate_metrics: dict[str, Any],
    active_metrics: dict[str, Any] | None,
    *,
    candidate_parameters: dict[str, Any],
    active_model: JevCalibrationModelV2 | None,
    validation: list[_Example],
) -> dict[str, Any]:
    candidate = candidate_metrics["candidate"]
    raw = candidate_metrics["raw_jev"]
    confidence = candidate_metrics["candidate_brier_improvement_ci95"]
    training_prior = candidate_metrics["training_prior_baseline"]
    training_prior_confidence = candidate_metrics["candidate_brier_improvement_vs_training_prior_ci95"]
    raw_improves = (
        candidate["brier"] < raw["brier"]
        and candidate["log_loss"] <= raw["log_loss"] + 0.01
        and confidence["lower"] > 0
    )
    training_prior_improves = (
        candidate["brier"] < training_prior["brier"]
        and candidate["log_loss"] <= training_prior["log_loss"] + 0.01
        and training_prior_confidence["lower"] > 0
    )
    active_improves = True
    active_ci = None
    if active_metrics is not None and active_model is not None:
        labels = [row.label for row in validation]
        months = [row.month for row in validation]
        candidate_probs = [_predict(candidate_parameters, row.probabilities) for row in validation]
        active_probs = [_predict(active_model.model_parameters, row.probabilities) for row in validation]
        active_ci = _cluster_bootstrap_improvement(labels, active_probs, candidate_probs, months)
        candidate_on_validation = _metrics(labels, candidate_probs)
        active_improves = (
            candidate_on_validation["brier"] < active_metrics["candidate"]["brier"]
            and candidate_on_validation["log_loss"] <= active_metrics["candidate"]["log_loss"] + 0.01
            and active_ci["lower"] > 0
        )
    return {
        "eligible": raw_improves and training_prior_improves and active_improves,
        "beats_raw_jev": raw_improves,
        "beats_training_prior_baseline": training_prior_improves,
        "beats_current_calibrator": active_improves,
        "raw_brier_improvement_ci95": confidence,
        "training_prior_brier_improvement_ci95": training_prior_confidence,
        "current_model_brier_improvement_ci95": active_ci,
        "rule": (
            "positive 95% month-block bootstrap Brier improvement over the training-period class-prior baseline, "
            "raw Jev, and active model; log-loss no more than 0.01 worse"
        ),
    }


def _score_existing(model: JevCalibrationModelV2, rows: list[_Example]) -> dict[str, Any]:
    _validate_model_record(model)
    params = model.model_parameters
    calibrated = [_predict(params, row.probabilities) for row in rows]
    raw = [row.probabilities for row in rows]
    labels = [row.label for row in rows]
    months = [row.month for row in rows]
    return {
        "raw_jev": _metrics(labels, raw),
        "candidate": _metrics(labels, calibrated),
        "candidate_brier_improvement_ci95": _cluster_bootstrap_improvement(labels, raw, calibrated, months),
    }


def _active_model(db: Session, cohort_key: str) -> JevCalibrationModelV2 | None:
    return db.scalar(
        select(JevCalibrationModelV2)
        .where(JevCalibrationModelV2.cohort_key == cohort_key, JevCalibrationModelV2.status == "active")
        .order_by(JevCalibrationModelV2.created_at.desc(), JevCalibrationModelV2.id.desc())
        .limit(1)
    )


def _active_payload(model: JevCalibrationModelV2 | None) -> dict[str, Any] | None:
    if model is None:
        return None
    manifest = model.training_manifest if isinstance(model.training_manifest, dict) else {}
    return {
        "id": str(model.id),
        "activated_at": model.activated_at.isoformat() if model.activated_at else None,
        "sample_count": model.sample_count,
        "parameters_sha256": model.parameters_sha256,
        "test_metrics": manifest.get("metrics"),
        "acceptance": manifest.get("acceptance"),
    }


def _validate_model_record(model: JevCalibrationModelV2) -> None:
    if model.status != "active":
        raise ValueError("calibration model is not active")
    if _digest(model.model_parameters) != model.parameters_sha256:
        raise ValueError("calibration model integrity check failed")
    params = model.model_parameters
    if params.get("algorithm_version") != ALGORITHM_VERSION or params.get("classes") != list(CLASSES):
        raise ValueError("calibration model contract is unsupported")
    coefficients = params.get("coefficients")
    intercept = params.get("intercept")
    if not isinstance(coefficients, list) or len(coefficients) != 3 or any(len(row) != 3 for row in coefficients):
        raise ValueError("calibration model coefficients are malformed")
    if not isinstance(intercept, list) or len(intercept) != 3:
        raise ValueError("calibration model intercept is malformed")


def _feature_vector(probabilities: dict[str, float]) -> np.ndarray:
    if not isinstance(probabilities, dict) or set(probabilities) != set(CLASSES):
        raise ValueError("probabilities must contain the three Jev classes")
    values = [probabilities[label] for label in CLASSES]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > 1 for value in values):
        raise ValueError("probabilities must be finite values between zero and one")
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("probabilities must sum to one")
    logs = np.log(np.maximum(np.asarray(values, dtype=float), 1e-6))
    return logs - logs.mean()


def _predict(parameters: dict[str, Any], probabilities: dict[str, float]) -> dict[str, float]:
    x = _feature_vector(probabilities)
    logits = np.asarray(parameters["intercept"], dtype=float) + np.asarray(parameters["coefficients"], dtype=float) @ x
    exponentials = np.exp(logits - np.max(logits))
    result = exponentials / exponentials.sum()
    return {label: float(result[index]) for index, label in enumerate(CLASSES)}


def _metrics(labels: list[str], vectors: list[dict[str, float]]) -> dict[str, float]:
    if not labels:
        raise ValueError("metrics require at least one labelled sample")
    brier = 0.0
    log_loss = 0.0
    for label, vector in zip(labels, vectors, strict=True):
        brier += sum((vector[name] - (1.0 if name == label else 0.0)) ** 2 for name in CLASSES)
        log_loss -= math.log(max(vector[label], 1e-15))
    return {"brier": brier / len(labels), "log_loss": log_loss / len(labels)}


def _cluster_bootstrap_improvement(
    labels: list[str], raw: list[dict[str, float]], candidate: list[dict[str, float]], months: list[str]
) -> dict[str, float]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, month in enumerate(months):
        grouped[month].append(index)
    month_keys = sorted(grouped)
    if len(month_keys) < MIN_VALIDATION_MONTHS:
        return {"lower": 0.0, "median": 0.0, "upper": 0.0, "blocks": float(len(month_keys))}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    improvements: list[float] = []
    for _ in range(BOOTSTRAP_ROUNDS):
        sampled_months = rng.choice(month_keys, size=len(month_keys), replace=True)
        indices = [index for month in sampled_months for index in grouped[str(month)]]
        sampled_labels = [labels[index] for index in indices]
        raw_score = _metrics(sampled_labels, [raw[index] for index in indices])["brier"]
        candidate_score = _metrics(sampled_labels, [candidate[index] for index in indices])["brier"]
        improvements.append(raw_score - candidate_score)
    return {
        "lower": float(np.quantile(improvements, 0.025)),
        "median": float(np.quantile(improvements, 0.5)),
        "upper": float(np.quantile(improvements, 0.975)),
        "blocks": float(len(month_keys)),
    }


def _cohort_key(provider: str, actual_model: str, question_version: str, target_contract: dict[str, Any]) -> str:
    contract = {
        "target_spec_version": target_contract.get("target_spec_version"),
        "horizon_sessions": target_contract.get("horizon_sessions"),
        "threshold": target_contract.get("threshold"),
    }
    return _digest({
        "provider": provider,
        "actual_model": actual_model,
        "question_version": question_version,
        "target_contract": contract,
        "time_mode": "observed",
    })


def _dataset_hash(rows: list[_Example]) -> str:
    return _digest([
        {
            "version_id": row.version_id,
            "root_id": row.root_id,
            "decision_at": row.decision_at.isoformat(),
            "label": row.label,
            "raw_probabilities": row.probabilities,
        }
        for row in rows
    ])


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
