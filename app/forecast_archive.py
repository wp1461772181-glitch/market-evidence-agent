"""Archive one real, offline Week 4-model prediction with its exact inputs.

The command accepts only a trusted local Week 4 artifact directory and a Week
3 JSON export with matching provenance.  It does not refresh market data,
retrain a model, or claim that a historical-research export was live data.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import UUID

import joblib
import pandas as pd
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import SessionLocal, engine
from .features import FEATURE_VERSION, SNAPSHOT_MODES
from .market_time import normalize_utc, xnys_session_close_at
from .models import ForecastRevision, ForecastSnapshot
from .services import is_valid_symbol, normalize_symbol
from .training import CLASS_LABELS, _probabilities_in_order
from .training_data import FEATURE_COLUMNS


ARTIFACT_VERSION = "week4-training-artifacts-v1"
MODEL_VERSION_PREFIX = "week4-calibrated-"
BENCHMARK_SYMBOL = "SPY"


@dataclass(frozen=True)
class TrustedModelArtifact:
    model: Any
    model_sha256: str
    manifest_sha256: str
    model_version: str
    feature_version: str
    source: str
    snapshot_mode: str
    available_from: date


def archive_forecast_snapshot(
    *,
    model_dir: str | Path,
    feature_path: str | Path,
    symbol: str,
    trading_date: date | str,
    db: Session,
    revises: UUID | str | None = None,
    reason: str | None = None,
) -> ForecastSnapshot:
    """Load one checked local artifact and append its offline prediction.

    A new row is intentionally created for every successful call.  The record
    is a snapshot of the file inputs at creation time; later changes to either
    source file cannot alter it.
    """
    parent_id, revision_reason = _validate_revision_request(revises=revises, reason=reason)
    artifact = load_trusted_model_artifact(model_dir)
    normalized_symbol = _validated_symbol(symbol)
    if normalized_symbol == BENCHMARK_SYMBOL:
        raise ValueError("SPY is the benchmark and cannot receive a stock forecast snapshot")
    requested_date = _parse_date(trading_date, "trading_date")
    if requested_date < artifact.available_from:
        raise ValueError(
            "feature trading_date predates this model's conservative availability bound "
            f"({artifact.available_from.isoformat()})"
        )

    feature_bytes, payload = _load_json_object(feature_path, "feature export")
    metadata = _mapping(payload.get("metadata"), "feature export metadata")
    _validate_feature_export_metadata(metadata, artifact)
    cutoff = _parse_timestamp(metadata.get("as_of_time"), "feature export metadata.as_of_time")
    session_close = xnys_session_close_at(requested_date)
    if session_close > cutoff:
        raise ValueError("feature trading_date session close is later than the feature export cutoff")

    feature_values = _select_feature_values(payload.get("rows"), normalized_symbol, requested_date)
    frame = pd.DataFrame([feature_values], columns=list(FEATURE_COLUMNS))
    probabilities = _probabilities_in_order(artifact.model, frame)[0]
    bearish, neutral, bullish = (float(value) for value in probabilities)
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in probabilities):
        raise ValueError("model produced probabilities outside the closed interval [0, 1]")
    if not math.isclose(float(sum(probabilities)), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("model probabilities do not sum to one")

    snapshot = ForecastSnapshot(
        symbol=normalized_symbol,
        feature_trading_date=requested_date,
        feature_as_of_time=cutoff,
        model_version=artifact.model_version,
        model_sha256=artifact.model_sha256,
        model_manifest_sha256=artifact.manifest_sha256,
        feature_export_sha256=_sha256(feature_bytes),
        feature_version=artifact.feature_version,
        feature_source=artifact.source,
        feature_snapshot_mode=artifact.snapshot_mode,
        feature_values=feature_values,
        bearish_probability=bearish,
        neutral_probability=neutral,
        bullish_probability=bullish,
    )
    try:
        parent: ForecastSnapshot | None = None
        root_snapshot_id: UUID | None = None
        if parent_id is not None:
            parent = db.get(ForecastSnapshot, parent_id)
            if parent is None:
                raise ValueError("revised forecast snapshot was not found")
            _validate_revision_compatibility(parent, snapshot)
            parent_link = db.get(ForecastRevision, parent.id)
            root_snapshot_id = parent_link.root_snapshot_id if parent_link else parent.id
            existing_child = db.query(ForecastRevision).filter_by(parent_snapshot_id=parent.id).one_or_none()
            if existing_child is not None:
                raise ValueError("snapshot already has a revision; revise latest snapshot")

        db.add(snapshot)
        if parent is not None and root_snapshot_id is not None and revision_reason is not None:
            db.flush()
            db.add(
                ForecastRevision(
                    snapshot_id=snapshot.id,
                    parent_snapshot_id=parent.id,
                    root_snapshot_id=root_snapshot_id,
                    reason=revision_reason,
                )
            )
        db.commit()
        db.refresh(snapshot)
    except IntegrityError as exc:
        db.rollback()
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint_name in {"forecast_revisions_parent_snapshot_id_key", "uq_forecast_revision_parent"}:
            raise ValueError("snapshot already has a revision; revise latest snapshot") from exc
        raise
    except Exception:
        db.rollback()
        raise
    return snapshot


def load_trusted_model_artifact(
    model_dir: str | Path,
    *,
    expected_model_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> TrustedModelArtifact:
    """Load the model file declared by a validated manifest in one directory.

    Joblib is only safe for local artifacts that the caller trusts.  This
    function constrains the declared filename to that supplied directory so a
    manifest cannot redirect it to another path.
    """
    directory = Path(model_dir).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError(f"model_dir is not a directory: {directory}")
    manifest_bytes, manifest = _load_json_object(directory / "manifest.json", "model manifest")
    manifest_sha256 = _sha256(manifest_bytes)
    _validate_expected_digest(expected_manifest_sha256, manifest_sha256, "manifest")
    if manifest.get("artifact_version") != ARTIFACT_VERSION:
        raise ValueError(f"unsupported artifact_version; expected {ARTIFACT_VERSION!r}")
    if manifest.get("feature_order") != list(FEATURE_COLUMNS):
        raise ValueError("model manifest feature_order does not match the supported Week 3 feature contract")
    if manifest.get("classes") != list(CLASS_LABELS):
        raise ValueError("model manifest classes must be [0, 1, 2]")

    data_metadata = _mapping(manifest.get("data_metadata"), "model manifest data_metadata")
    feature_version = _required_string(data_metadata.get("feature_version"), "model feature_version")
    if feature_version != FEATURE_VERSION:
        raise ValueError(f"unsupported model feature_version {feature_version!r}")
    source = _required_string(data_metadata.get("source"), "model source")
    snapshot_mode = _required_string(data_metadata.get("snapshot_mode"), "model snapshot_mode")
    if snapshot_mode not in SNAPSHOT_MODES:
        raise ValueError("model snapshot_mode is not supported")

    last_fold = _mapping(manifest.get("last_fold"), "model manifest last_fold")
    windows = _mapping(last_fold.get("windows"), "model manifest last_fold.windows")
    calibration_window = _mapping(windows.get("calibration"), "model manifest last_fold.windows.calibration")
    test_window = _mapping(windows.get("test"), "model manifest last_fold.windows.test")
    calibration_end = _parse_date(
        calibration_window.get("end"), "model manifest last_fold.windows.calibration.end"
    )
    available_from = _parse_date(test_window.get("start"), "model manifest last_fold.windows.test.start")
    if calibration_end >= available_from:
        raise ValueError("model manifest calibration window must end before its test window starts")

    model_file = _required_string(manifest.get("model_file"), "model manifest model_file")
    model_name = Path(model_file)
    if model_name.name != model_file or model_name.suffix != ".joblib":
        raise ValueError("model manifest model_file must be a .joblib basename in model_dir")
    model_path = (directory / model_name).resolve(strict=True)
    if model_path.parent != directory:
        raise ValueError("model manifest model_file must remain inside model_dir")
    model_bytes = model_path.read_bytes()
    model_sha256 = _sha256(model_bytes)
    _validate_expected_digest(expected_model_sha256, model_sha256, "model")
    model = joblib.load(io.BytesIO(model_bytes))
    if not hasattr(model, "predict_proba") or not hasattr(model, "classes_"):
        raise ValueError("model artifact must expose predict_proba and classes_")
    if list(model.classes_) != list(CLASS_LABELS):
        raise ValueError("model artifact classes must be ordered [0, 1, 2]")

    return TrustedModelArtifact(
        model=model,
        model_sha256=model_sha256,
        manifest_sha256=manifest_sha256,
        model_version=f"{MODEL_VERSION_PREFIX}{model_sha256[:16]}",
        feature_version=feature_version,
        source=source,
        snapshot_mode=snapshot_mode,
        available_from=available_from,
    )


def create_forecast_snapshot_table() -> None:
    """Create the additive snapshot and revision tables when the CLI is first used."""
    ForecastSnapshot.__table__.create(bind=engine, checkfirst=True)
    ForecastRevision.__table__.create(bind=engine, checkfirst=True)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True, help="Trusted Week 4 artifact directory")
    parser.add_argument("--features", type=Path, required=True, help="Week 3 JSON feature export")
    parser.add_argument("--symbol", required=True, help="Stock symbol in the export")
    parser.add_argument("--trading-date", required=True, help="Feature trading date, YYYY-MM-DD")
    parser.add_argument("--revises", help="Existing snapshot UUID to revise")
    parser.add_argument("--reason", help="Short non-empty reason for this revision")
    args = parser.parse_args(argv)
    try:
        create_forecast_snapshot_table()
        with SessionLocal() as db:
            snapshot = archive_forecast_snapshot(
                model_dir=args.model_dir,
                feature_path=args.features,
                symbol=args.symbol,
                trading_date=args.trading_date,
                db=db,
                revises=args.revises,
                reason=args.reason,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "id": str(snapshot.id),
                "symbol": snapshot.symbol,
                "feature_trading_date": snapshot.feature_trading_date.isoformat(),
                "feature_as_of_time": snapshot.feature_as_of_time.isoformat(),
                "model_version": snapshot.model_version,
                "probabilities": {
                    "bearish": snapshot.bearish_probability,
                    "neutral": snapshot.neutral_probability,
                    "bullish": snapshot.bullish_probability,
                },
            },
            sort_keys=True,
        )
    )


def _load_json_object(path: str | Path, name: str) -> tuple[bytes, Mapping[str, Any]]:
    raw_bytes = Path(path).read_bytes()
    try:
        value = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON") from exc
    return raw_bytes, _mapping(value, name)


def _validate_feature_export_metadata(metadata: Mapping[str, Any], artifact: TrustedModelArtifact) -> None:
    if _required_string(metadata.get("feature_version"), "feature export feature_version") != artifact.feature_version:
        raise ValueError("feature export feature_version does not match the model artifact")
    if _required_string(metadata.get("source"), "feature export source") != artifact.source:
        raise ValueError("feature export source does not match the model artifact")
    if _required_string(metadata.get("snapshot_mode"), "feature export snapshot_mode") != artifact.snapshot_mode:
        raise ValueError("feature export snapshot_mode does not match the model artifact")


def _select_feature_values(rows: object, symbol: str, trading_date: date) -> dict[str, float]:
    if not isinstance(rows, list):
        raise ValueError("feature export rows must be a list")
    matches: list[Mapping[str, Any]] = []
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            continue
        raw_symbol = raw_row.get("symbol")
        raw_date = raw_row.get("trading_date")
        if isinstance(raw_symbol, str) and normalize_symbol(raw_symbol) == symbol and raw_date == trading_date.isoformat():
            matches.append(raw_row)
    if len(matches) != 1:
        raise ValueError(
            f"feature export must contain exactly one row for {symbol} on {trading_date.isoformat()}; found {len(matches)}"
        )
    values: dict[str, float] = {}
    for column in FEATURE_COLUMNS:
        raw_value = matches[0].get(column)
        if isinstance(raw_value, bool):
            raise ValueError(f"feature export row has invalid {column}")
        try:
            value = float(raw_value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"feature export row has invalid {column}") from exc
        if not math.isfinite(value):
            raise ValueError(f"feature export row has non-finite {column}")
        values[column] = value
    if values["volatility_20d"] < 0.0:
        raise ValueError("feature export row volatility_20d must be non-negative")
    return values


def _validated_symbol(value: str) -> str:
    symbol = normalize_symbol(value)
    if not is_valid_symbol(symbol):
        raise ValueError("symbol must contain 1-5 ASCII letters")
    return symbol


def _parse_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp with a timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    return normalize_utc(parsed, name=name)


def _parse_date(value: date | str | object, name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{name} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_revision_request(
    *, revises: UUID | str | None, reason: str | None
) -> tuple[UUID | None, str | None]:
    if (revises is None) != (reason is None):
        raise ValueError("revises and reason must be provided together")
    if revises is None:
        return None, None
    try:
        parent_id = revises if isinstance(revises, UUID) else UUID(str(revises))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("revises must be a valid forecast snapshot UUID") from exc
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("revision reason must be non-empty")
    normalized_reason = reason.strip()
    if len(normalized_reason) > 280:
        raise ValueError("revision reason must be at most 280 characters")
    return parent_id, normalized_reason


def _validate_revision_compatibility(parent: ForecastSnapshot, child: ForecastSnapshot) -> None:
    if child.symbol != parent.symbol:
        raise ValueError("revised forecast symbol must match its parent")
    if child.feature_trading_date < parent.feature_trading_date:
        raise ValueError("revised forecast feature trading_date cannot precede its parent")
    if child.feature_as_of_time < parent.feature_as_of_time:
        raise ValueError("revised forecast feature cutoff cannot precede its parent")
    if child.feature_source != parent.feature_source:
        raise ValueError("revised forecast feature source must match its parent")
    if child.feature_snapshot_mode != parent.feature_snapshot_mode:
        raise ValueError("revised forecast snapshot mode must match its parent")


def _validate_expected_digest(expected: str | None, actual: str, name: str) -> None:
    if expected is None:
        return
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"expected {name} SHA-256 must be a 64-character hexadecimal digest")
    try:
        int(expected, 16)
    except ValueError as exc:
        raise ValueError(f"expected {name} SHA-256 must be a 64-character hexadecimal digest") from exc
    if expected.lower() != actual:
        raise ValueError(f"{name} SHA-256 does not match the expected value")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    main()
