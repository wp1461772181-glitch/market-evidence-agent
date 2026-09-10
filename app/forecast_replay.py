"""Replay one archived Week 5 forecast using only its saved inputs.

This is deliberately a bounded, local verification command.  It does not read
the original feature export or market-price tables, retrain a model, or alter
the archived prediction.  The retained Week 4 artifact must match the hashes
saved with the selected snapshot before its joblib payload is deserialized.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from .database import SessionLocal
from .features import FEATURE_VERSION, SNAPSHOT_MODES
from .forecast_archive import TrustedModelArtifact, load_trusted_model_artifact
from .models import ForecastSnapshot
from .training import _probabilities_in_order
from .training_data import FEATURE_COLUMNS


PROBABILITY_NAMES = ("bearish", "neutral", "bullish")
DEFAULT_TOLERANCE = 1e-12


@dataclass(frozen=True)
class ForecastReplay:
    """The saved and recomputed probabilities for a single snapshot."""

    snapshot_id: UUID
    model_version: str
    stored_probabilities: dict[str, float]
    replayed_probabilities: dict[str, float]
    matches: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.snapshot_id),
            "matches": self.matches,
            "model_version": self.model_version,
            "replayed_probabilities": self.replayed_probabilities,
            "stored_probabilities": self.stored_probabilities,
        }


def replay_forecast_snapshot(
    *,
    snapshot_id: UUID | str,
    model_dir: str | Path,
    db: Session,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ForecastReplay:
    """Recompute a snapshot from its persisted features and retained artifact.

    ``False`` in ``matches`` is a meaningful verification result: it is not an
    exception and never updates the database.  Broken snapshot contracts and
    wrong or missing artifact files fail clearly instead.
    """
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("tolerance must be a finite non-negative number")
    normalized_id = _parse_snapshot_id(snapshot_id)
    snapshot = db.get(ForecastSnapshot, normalized_id)
    if snapshot is None:
        raise ValueError("forecast snapshot not found")

    feature_values = _validated_feature_values(snapshot.feature_values)
    stored_probabilities = _stored_probabilities(snapshot)
    artifact = load_trusted_model_artifact(
        model_dir,
        expected_model_sha256=_sha256_field(snapshot.model_sha256, "snapshot model_sha256"),
        expected_manifest_sha256=_sha256_field(
            snapshot.model_manifest_sha256, "snapshot model_manifest_sha256"
        ),
    )
    _validate_snapshot_contract(snapshot, artifact)
    frame = pd.DataFrame([feature_values], columns=list(FEATURE_COLUMNS))
    replayed = _probabilities_in_order(artifact.model, frame)[0]
    replayed_probabilities = _validated_probability_values(replayed.tolist(), "replayed model probabilities")
    matches = all(
        math.isclose(
            stored_probabilities[name], replayed_probabilities[name], rel_tol=0.0, abs_tol=tolerance
        )
        for name in PROBABILITY_NAMES
    )
    return ForecastReplay(
        snapshot_id=normalized_id,
        model_version=snapshot.model_version,
        stored_probabilities=stored_probabilities,
        replayed_probabilities=replayed_probabilities,
        matches=matches,
    )


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True, help="Archived forecast UUID to verify")
    parser.add_argument("--model-dir", type=Path, required=True, help="Trusted retained Week 4 artifact directory")
    args = parser.parse_args(argv)
    try:
        with SessionLocal() as db:
            replay = replay_forecast_snapshot(
                snapshot_id=args.snapshot_id,
                model_dir=args.model_dir,
                db=db,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(replay.as_dict(), sort_keys=True))
    if not replay.matches:
        raise SystemExit(1)


def _parse_snapshot_id(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("snapshot_id must be a UUID")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError("snapshot_id must be a UUID") from exc


def _validate_snapshot_contract(snapshot: ForecastSnapshot, artifact: TrustedModelArtifact) -> None:
    if snapshot.model_version != artifact.model_version:
        raise ValueError("snapshot model_version does not match the retained model artifact")
    if snapshot.feature_version != FEATURE_VERSION or snapshot.feature_version != artifact.feature_version:
        raise ValueError("snapshot feature_version does not match the retained model artifact")
    if snapshot.feature_source != artifact.source:
        raise ValueError("snapshot feature_source does not match the retained model artifact")
    if snapshot.feature_snapshot_mode not in SNAPSHOT_MODES:
        raise ValueError("snapshot feature_snapshot_mode is not supported")
    if snapshot.feature_snapshot_mode != artifact.snapshot_mode:
        raise ValueError("snapshot feature_snapshot_mode does not match the retained model artifact")


def _validated_feature_values(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("snapshot feature_values must be an object")
    expected = set(FEATURE_COLUMNS)
    actual = set(value)
    if actual != expected:
        raise ValueError("snapshot feature_values must contain exactly the supported Week 3 feature columns")
    result: dict[str, float] = {}
    for column in FEATURE_COLUMNS:
        raw_value = value[column]
        if isinstance(raw_value, bool):
            raise ValueError(f"snapshot feature_values has invalid {column}")
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"snapshot feature_values has invalid {column}") from exc
        if not math.isfinite(numeric_value):
            raise ValueError(f"snapshot feature_values has non-finite {column}")
        result[column] = numeric_value
    if result["volatility_20d"] < 0.0:
        raise ValueError("snapshot feature_values volatility_20d must be non-negative")
    return result


def _stored_probabilities(snapshot: ForecastSnapshot) -> dict[str, float]:
    return _validated_probability_values(
        (
            snapshot.bearish_probability,
            snapshot.neutral_probability,
            snapshot.bullish_probability,
        ),
        "snapshot probabilities",
    )


def _validated_probability_values(values: object, name: str) -> dict[str, float]:
    if not isinstance(values, (tuple, list)) or len(values) != len(PROBABILITY_NAMES):
        raise ValueError(f"{name} must contain bearish, neutral, and bullish values")
    result: dict[str, float] = {}
    for probability_name, raw_value in zip(PROBABILITY_NAMES, values, strict=True):
        if isinstance(raw_value, bool):
            raise ValueError(f"{name} has invalid {probability_name}")
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} has invalid {probability_name}") from exc
        if not math.isfinite(numeric_value) or not 0.0 <= numeric_value <= 1.0:
            raise ValueError(f"{name} has invalid {probability_name}")
        result[probability_name] = numeric_value
    if not math.isclose(sum(result.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"{name} do not sum to one")
    return result


def _sha256_field(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a SHA-256 hex digest") from exc
    return value


if __name__ == "__main__":
    main()
