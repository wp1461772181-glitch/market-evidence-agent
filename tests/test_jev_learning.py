from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.jev_learning import (
    CLASSES,
    _Example,
    _acceptance,
    _feature_vector,
    _fit_candidate,
    _training_readiness,
    apply_jev_calibrator,
    _digest,
)


def _synthetic_examples(month_count: int = 72) -> list[_Example]:
    rows = []
    labels = ("bearish", "neutral", "bullish")
    start_year, start_month = 2018, 1
    for month_index in range(month_count):
        year = start_year + (start_month - 1 + month_index) // 12
        month_num = (start_month - 1 + month_index) % 12 + 1
        month = f"{year:04d}-{month_num:02d}"
        for stock_index in range(7):
            label = labels[(month_index + stock_index) % len(labels)]
            if label == "bearish":
                probabilities = {"bearish": 0.80, "neutral": 0.15, "bullish": 0.05}
            elif label == "neutral":
                probabilities = {"bearish": 0.10, "neutral": 0.80, "bullish": 0.10}
            else:
                probabilities = {"bearish": 0.05, "neutral": 0.15, "bullish": 0.80}
            rows.append(_Example(
                version_id=f"version-{month}-{stock_index}",
                root_id=f"root-{month}-{stock_index}",
                symbol=("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")[stock_index],
                decision_at=datetime(year, month_num, 5, tzinfo=UTC),
                month=month,
                label=label,
                probabilities=probabilities,
            ))
    return rows


def test_training_readiness_requires_a_large_chronological_holdout():
    rows = _synthetic_examples()
    ready = _training_readiness(rows)
    too_few = _training_readiness(rows[:419])

    assert ready["ready"] is True
    assert ready["matured_roots"] == 504
    assert ready["validation_months"] == 22
    assert too_few["ready"] is False
    assert "需要更多已到期的真实观察预测" in too_few["reasons"]


def test_calibrator_fits_only_earlier_months_and_emits_valid_probabilities():
    rows = _synthetic_examples()
    candidate, validation = _fit_candidate(rows)
    assert candidate["split"]["method"] == "chronological_by_calendar_month"
    assert candidate["split"]["training_last_month"] < candidate["split"]["validation_first_month"]
    assert len(validation) == candidate["split"]["validation_roots"]
    prior = candidate["metrics"]["training_prior_baseline"]
    assert sum(prior["probabilities"].values()) == pytest.approx(1.0)
    assert sum(prior["class_counts"].values()) == candidate["split"]["training_roots"]
    assert "candidate_brier_improvement_vs_training_prior_ci95" in candidate["metrics"]

    parameters = candidate["parameters"]
    model = SimpleNamespace(status="active", model_parameters=parameters, parameters_sha256=_digest(parameters))
    adjusted = apply_jev_calibrator(model, {"bearish": 0.25, "neutral": 0.50, "bullish": 0.25})
    assert set(adjusted) == set(CLASSES)
    assert all(0.0 <= value <= 1.0 for value in adjusted.values())
    assert sum(adjusted.values()) == pytest.approx(1.0)


def test_candidate_must_beat_training_class_prior_to_activate():
    metrics = {
        "raw_jev": {"brier": 1.16, "log_loss": 3.62},
        "candidate": {"brier": 0.636, "log_loss": 1.07},
        "candidate_brier_improvement_ci95": {"lower": 0.40},
        "training_prior_baseline": {"brier": 0.612, "log_loss": 1.02},
        "candidate_brier_improvement_vs_training_prior_ci95": {"lower": -0.04},
    }

    decision = _acceptance(
        metrics,
        None,
        candidate_parameters={},
        active_model=None,
        validation=[],
    )

    assert decision["beats_raw_jev"] is True
    assert decision["beats_training_prior_baseline"] is False
    assert decision["eligible"] is False


def test_feature_vector_rejects_invalid_probability_contracts():
    with pytest.raises(ValueError, match="sum to one"):
        _feature_vector({"bearish": 0.4, "neutral": 0.4, "bullish": 0.4})
