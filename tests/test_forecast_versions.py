from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import numpy as np
import pytest
from sqlalchemy import func, select

import app.forecast_archive as archive_module
from app.database import SessionLocal
from app.forecast_archive import TrustedModelArtifact, archive_forecast_snapshot
from app.models import ForecastRevision, ForecastSnapshot


_TRADING_DATE = date(2026, 4, 10)
_CUTOFF = datetime(2026, 4, 10, 21, tzinfo=timezone.utc)
_FEATURE_VALUES = {
    "momentum_5d": 0.01,
    "momentum_20d": 0.02,
    "volatility_20d": 0.25,
    "volume_ratio_20d": 1.1,
    "drawdown_20d": -0.05,
    "relative_return_20d": 0.03,
}


class _FixedProbabilityModel:
    classes_ = np.asarray([0, 1, 2])

    def predict_proba(self, features):
        return np.tile(np.asarray([0.2, 0.5, 0.3]), (len(features), 1))


@pytest.fixture
def archive_snapshot(client, monkeypatch):
    artifact = TrustedModelArtifact(
        model=_FixedProbabilityModel(),
        model_sha256="a" * 64,
        manifest_sha256="b" * 64,
        model_version="week4-calibrated-aaaaaaaaaaaaaaaa",
        feature_version="market-features-v1",
        source="yahoo-finance-chart",
        snapshot_mode="historical_research",
        available_from=date(2026, 4, 9),
    )
    payload = {
        "metadata": {
            "as_of_time": _CUTOFF.isoformat(),
            "feature_version": artifact.feature_version,
            "source": artifact.source,
            "snapshot_mode": artifact.snapshot_mode,
        },
        "rows": [],
    }
    monkeypatch.setattr(archive_module, "load_trusted_model_artifact", lambda _path: artifact)
    monkeypatch.setattr(archive_module, "_load_json_object", lambda _path, _name: (b"fixed-export", payload))
    monkeypatch.setattr(archive_module, "_select_feature_values", lambda *_args: dict(_FEATURE_VALUES))

    def create(db, *, symbol: str = "AAPL", trading_date: date = _TRADING_DATE, **kwargs) -> ForecastSnapshot:
        return archive_forecast_snapshot(
            model_dir="unused-by-mock",
            feature_path="unused-by-mock",
            symbol=symbol,
            trading_date=trading_date,
            db=db,
            **kwargs,
        )

    return create


def _snapshot_count() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(ForecastSnapshot)) or 0)


def _direct_snapshot(
    db,
    *,
    symbol: str = "AAPL",
    trading_date: date = _TRADING_DATE,
    cutoff: datetime = _CUTOFF,
) -> ForecastSnapshot:
    snapshot = ForecastSnapshot(
        symbol=symbol,
        feature_trading_date=trading_date,
        feature_as_of_time=cutoff,
        model_version="week4-calibrated-aaaaaaaaaaaaaaaa",
        model_sha256="a" * 64,
        model_manifest_sha256="b" * 64,
        feature_export_sha256="c" * 64,
        feature_version="market-features-v1",
        feature_source="yahoo-finance-chart",
        feature_snapshot_mode="historical_research",
        feature_values=dict(_FEATURE_VALUES),
        bearish_probability=0.2,
        neutral_probability=0.5,
        bullish_probability=0.3,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def test_revision_timeline_from_middle_is_root_to_latest_and_preserves_parent(client, archive_snapshot):
    with SessionLocal() as db:
        root = archive_snapshot(db)
        root_values = {
            "id": root.id,
            "feature_values": dict(root.feature_values),
            "feature_as_of_time": root.feature_as_of_time,
            "model_sha256": root.model_sha256,
            "probabilities": (root.bearish_probability, root.neutral_probability, root.bullish_probability),
        }
        second = archive_snapshot(db, revises=root.id, reason="late source correction")
        third = archive_snapshot(db, revises=second.id, reason="updated model artifact")
        db.refresh(root)

    assert root.id == root_values["id"]
    assert root.feature_values == root_values["feature_values"]
    assert root.feature_as_of_time == root_values["feature_as_of_time"]
    assert root.model_sha256 == root_values["model_sha256"]
    assert (root.bearish_probability, root.neutral_probability, root.bullish_probability) == root_values["probabilities"]

    response = client.get(f"/forecast-snapshots/{second.id}/timeline")
    assert response.status_code == 200
    body = response.json()
    assert body["root_snapshot_id"] == str(root.id)
    assert [row["id"] for row in body["snapshots"]] == [str(root.id), str(second.id), str(third.id)]
    assert [row["version"] for row in body["snapshots"]] == [1, 2, 3]
    assert [row["parent_snapshot_id"] for row in body["snapshots"]] == [None, str(root.id), str(second.id)]
    assert [row["revision_reason"] for row in body["snapshots"]] == [None, "late source correction", "updated model artifact"]


def test_unlinked_snapshot_is_a_legacy_version_one_root(client):
    with SessionLocal() as db:
        snapshot = _direct_snapshot(db)

    response = client.get(f"/forecast-snapshots/{snapshot.id}/timeline")
    assert response.status_code == 200
    body = response.json()
    assert body["root_snapshot_id"] == str(snapshot.id)
    assert len(body["snapshots"]) == 1
    assert body["snapshots"][0]["id"] == str(snapshot.id)
    assert body["snapshots"][0]["version"] == 1
    assert body["snapshots"][0]["parent_snapshot_id"] is None
    assert body["snapshots"][0]["revision_reason"] is None


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"revises": uuid4(), "reason": "unknown snapshot"}, "not found"),
        ({"revises": "not-a-uuid", "reason": "x"}, "valid forecast snapshot UUID"),
        ({"revises": uuid4(), "reason": "   "}, "reason must be non-empty"),
        ({"revises": uuid4()}, "provided together"),
    ],
)
def test_invalid_revision_requests_do_not_create_snapshots(archive_snapshot, kwargs, match):
    before = _snapshot_count()
    with SessionLocal() as db, pytest.raises(ValueError, match=match):
        archive_snapshot(db, **kwargs)
    assert _snapshot_count() == before


def test_cross_symbol_backdated_and_branch_revisions_are_rejected_atomically(archive_snapshot):
    with SessionLocal() as db:
        root = archive_snapshot(db)
    before = _snapshot_count()
    with SessionLocal() as db, pytest.raises(ValueError, match="symbol must match"):
        archive_snapshot(db, symbol="MSFT", revises=root.id, reason="wrong stock")
    assert _snapshot_count() == before

    with SessionLocal() as db:
        later_parent = _direct_snapshot(
            db,
            trading_date=date(2026, 4, 11),
            cutoff=datetime(2026, 4, 11, 21, tzinfo=timezone.utc),
        )
    before = _snapshot_count()
    with SessionLocal() as db, pytest.raises(ValueError, match="cannot precede"):
        archive_snapshot(db, revises=later_parent.id, reason="backdated")
    assert _snapshot_count() == before

    with SessionLocal() as db:
        later_cutoff_parent = _direct_snapshot(
            db,
            cutoff=datetime(2026, 4, 11, 21, tzinfo=timezone.utc),
        )
    before = _snapshot_count()
    with SessionLocal() as db, pytest.raises(ValueError, match="cutoff cannot precede"):
        archive_snapshot(db, revises=later_cutoff_parent.id, reason="backdated cutoff")
    assert _snapshot_count() == before

    with SessionLocal() as db:
        first_child = archive_snapshot(db, revises=root.id, reason="first revision")
    before = _snapshot_count()
    with SessionLocal() as db, pytest.raises(ValueError, match="already has a revision"):
        archive_snapshot(db, revises=root.id, reason="would branch")
    assert _snapshot_count() == before
    with SessionLocal() as db:
        assert db.get(ForecastRevision, first_child.id) is not None
        assert db.scalar(select(func.count()).select_from(ForecastRevision).where(ForecastRevision.parent_snapshot_id == root.id)) == 1


def test_timeline_returns_404_for_unknown_snapshot(client):
    assert client.get(f"/forecast-snapshots/{UUID(int=0)}/timeline").status_code == 404
