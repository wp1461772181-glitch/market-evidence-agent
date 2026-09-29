"""Fit and score a research-only Jev calibrator from matured monthly replays.

This artifact never activates or changes the live observed-mode calibrator.
Historical Jev calls run with today's model on old inputs, so results are
retrospective research and may contain model-knowledge lookahead.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from calendar import monthrange
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
from exchange_calendars import get_calendar
from sqlalchemy import select

from app.database import SessionLocal
from app.forecast_v2_models import ForecastEvaluationV2, ForecastVersionV2
from app.jev_learning import (
    _Example,
    _acceptance,
    _dataset_hash,
    _digest,
    _fit_candidate,
    _training_readiness,
)


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "artifacts" / "jev-historical-calibration.json"


def _load_monthly_examples():
    calendar = get_calendar("XNYS")
    with SessionLocal() as db:
        versions = list(db.scalars(
            select(ForecastVersionV2)
            .where(ForecastVersionV2.root_id == ForecastVersionV2.id)
            .order_by(ForecastVersionV2.decision_at, ForecastVersionV2.id)
        ))
        evaluations = list(db.scalars(
            select(ForecastEvaluationV2)
            .order_by(ForecastEvaluationV2.forecast_version_id, ForecastEvaluationV2.result_version)
        ))

    latest_eval = {}
    for evaluation in evaluations:
        latest_eval[str(evaluation.forecast_version_id)] = evaluation

    rows = []
    counts = Counter()
    seen_targets = set()
    model_ids = set()
    for version in versions:
        manifest = version.model_manifest if isinstance(version.model_manifest, dict) else {}
        if manifest.get("time_mode") != "historical_research":
            continue
        anchor = (version.target_contract or {}).get("anchor_date")
        target_end = (version.target_contract or {}).get("target_end_date")
        if not isinstance(anchor, str) or not isinstance(target_end, str):
            continue
        anchor_date = datetime.fromisoformat(anchor).date()
        sessions = calendar.sessions_in_range(
            pd.Timestamp(date(anchor_date.year, anchor_date.month, 1)),
            pd.Timestamp(date(anchor_date.year, anchor_date.month, monthrange(anchor_date.year, anchor_date.month)[1])),
        )
        if not len(sessions) or sessions[-1].date() != anchor_date:
            counts["non_month_end_roots_skipped"] += 1
            continue
        # User-created one-off replays are useful to inspect, but the batch
        # trainer only consumes the monthly, calendar-aligned research set.
        key = (version.symbol, target_end)
        if key in seen_targets:
            counts["duplicate_target_roots_skipped"] += 1
            continue
        seen_targets.add(key)

        provider = manifest.get("decision_provider")
        if not isinstance(provider, dict) or provider.get("provider") != "openrouter":
            counts["historical_roots_without_jev"] += 1
            continue
        actual_model = provider.get("actual_model")
        question_version = provider.get("question_version")
        raw = provider.get("raw_probabilities")
        if not isinstance(actual_model, str) or not actual_model.strip() or not isinstance(question_version, str):
            counts["historical_roots_missing_model_metadata"] += 1
            continue
        if not isinstance(raw, dict) or set(raw) != {"bearish", "neutral", "bullish"}:
            counts["historical_roots_missing_raw_probabilities"] += 1
            continue
        evaluation = latest_eval.get(str(version.id))
        if evaluation is None or evaluation.status != "succeeded" or not evaluation.actual_label:
            counts["jev_roots_without_mature_label"] += 1
            continue
        target = (version.target_contract or {})
        month = version.decision_at.strftime("%Y-%m")
        rows.append(_Example(
            version_id=str(version.id),
            root_id=str(version.root_id),
            symbol=version.symbol,
            decision_at=version.decision_at,
            month=month,
            label=evaluation.actual_label,
            probabilities={label: float(raw[label]) for label in ("bearish", "neutral", "bullish")},
        ))
        model_ids.add((actual_model, question_version, target.get("target_spec_version"),
                       target.get("horizon_sessions"), target.get("threshold")))

    rows.sort(key=lambda row: (row.decision_at, row.symbol, row.root_id))
    return rows, counts, sorted(model_ids, key=str)


def train_artifact(*, output: Path) -> dict:
    rows, counts, model_ids = _load_monthly_examples()
    readiness = _training_readiness(rows)
    readiness["reasons"] = [
        reason.replace("已到期的真实观察预测", "已到期的历史回放")
        for reason in readiness["reasons"]
    ]
    artifact = {
        "schema_version": "jev-historical-calibration-research-v1",
        "status": "awaiting_more_historical_samples",
        "created_at": datetime.now(UTC).isoformat(),
        "sample_count": len(rows),
        "label_counts": dict(Counter(row.label for row in rows)),
        "sample_audit": dict(counts),
        "cohorts": [
            {"actual_model": item[0], "question_version": item[1], "target_spec_version": item[2],
             "horizon_sessions": item[3], "threshold": item[4]}
            for item in model_ids
        ],
        "dataset_sha256": _dataset_hash(rows) if rows else None,
        "readiness": readiness,
        "research_only": True,
        "live_calibrator_activated": False,
        "limitations": [
            "Jev calls are made now against old point-in-time inputs; Jev may encode knowledge learned after each historical target date.",
            "The historical market and SEC inputs are explicit initial-backfill assumptions, not proof the system observed them at the time.",
            "This artifact is isolated from the live observed-mode learning cohort and is not applied to real-time probabilities.",
        ],
    }
    if len(model_ids) > 1:
        artifact["status"] = "multiple_jev_model_cohorts"
        artifact["readiness"] = {
            **readiness,
            "reasons": [*readiness["reasons"], "historical roots contain multiple Jev model/question cohorts"],
        }
    elif readiness["ready"]:
        candidate, validation = _fit_candidate(rows)
        acceptance = _acceptance(
            candidate["metrics"], None,
            candidate_parameters=candidate["parameters"],
            active_model=None,
            validation=validation,
        )
        artifact.update({
            "status": "trained_research_candidate",
            "split": candidate["split"],
            "metrics": candidate["metrics"],
            "historical_holdout_beats_raw_jev": acceptance["beats_raw_jev"],
            "historical_holdout_acceptance": acceptance,
            "model_parameters_sha256": _digest(candidate["parameters"]),
            "model_parameters": candidate["parameters"],
        })

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    artifact = train_artifact(output=args.output)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "status": artifact["status"],
        "samples": artifact["sample_count"],
        "readiness": artifact["readiness"],
        "metrics": artifact.get("metrics"),
        "live_calibrator_activated": artifact["live_calibrator_activated"],
    }, ensure_ascii=False, indent=2))
    return 0 if artifact["status"] == "trained_research_candidate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
