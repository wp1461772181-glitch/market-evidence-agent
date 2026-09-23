from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.market_data import YAHOO_SOURCE
from app.models import (
    ForecastRevision,
    ForecastRevisionEvidence,
    ForecastSnapshot,
    MarketPrice,
    MarketPriceRevision,
    ResearchRun,
)


def _snapshot(*, symbol: str, moment: datetime, probability: float) -> ForecastSnapshot:
    return ForecastSnapshot(
        symbol=symbol,
        feature_trading_date=moment.date(),
        feature_as_of_time=moment,
        model_version="week4-calibrated-test",
        model_sha256="a" * 64,
        model_manifest_sha256="b" * 64,
        feature_export_sha256="c" * 64,
        feature_version="market-features-v1",
        feature_source="yahoo-finance-chart",
        feature_snapshot_mode="historical_research",
        feature_values={"momentum_5d": 0.01},
        bearish_probability=probability,
        neutral_probability=1.0 - probability,
        bullish_probability=0.0,
    )


def _table_counts() -> tuple[int, int, int, int, int]:
    with SessionLocal() as db:
        return tuple(
            int(db.scalar(select(func.count()).select_from(model)) or 0)
            for model in (ForecastSnapshot, ForecastRevision, ForecastRevisionEvidence, ResearchRun, MarketPrice)
        )


def _price(*, symbol: str, trading_date: date, close: float) -> MarketPrice:
    return MarketPrice(
        symbol=symbol,
        trading_date=trading_date,
        open=close - 1.0,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        volume=int(close * 100),
        source=YAHOO_SOURCE,
        fetched_at=datetime(2035, 1, 1, tzinfo=UTC),
    )


def _revision(
    *,
    symbol: str,
    trading_date: date,
    close: float,
    revision_number: int,
    observed_at: datetime,
) -> MarketPriceRevision:
    return MarketPriceRevision(
        symbol=symbol,
        trading_date=trading_date,
        open=close - 1.0,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        volume=int(close * 100),
        source=YAHOO_SOURCE,
        revision_number=revision_number,
        content_hash=(str(revision_number) * 64)[:64],
        available_at=datetime(2035, 1, 3, 21, tzinfo=UTC),
        observed_at=observed_at,
        is_initial_backfill=False,
    )


@pytest.fixture
def trusted_evaluation(monkeypatch, tmp_path: Path) -> Path:
    import app.dashboard as dashboard

    report = {
        "experiment": {"purpose": "Offline baseline evaluation only."},
        "data_metadata": {
            "as_of_time": "2026-09-04T21:00:00+00:00",
            "feature_version": "market-features-v1",
            "snapshot_mode": "historical_research",
        },
        "folds": [{"row_counts": {"test": 10}}, {"row_counts": {"test": 11}}, {"row_counts": {"test": 12}}],
        "pooled_oos": {
            "logistic_calibrated": {
                "accuracy": 0.45,
                "balanced_accuracy": 0.35,
                "brier_multiclass": 0.69,
                "log_loss": 1.22,
                "macro_f1": 0.29,
            },
            "logistic_raw": {
                "accuracy": 0.46,
                "balanced_accuracy": 0.34,
                "brier_multiclass": 0.62,
                "log_loss": 1.04,
                "macro_f1": 0.26,
            },
            "baseline_class_prior": {
                "accuracy": 0.44,
                "balanced_accuracy": 0.33,
                "brier_multiclass": 0.65,
                "log_loss": 1.07,
                "macro_f1": 0.20,
            },
        },
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifact_version": "week4-training-artifacts-v1"}), encoding="utf-8"
    )
    monkeypatch.setattr(dashboard, "TRUSTED_EVALUATION_DIRECTORY", tmp_path)
    return tmp_path


def test_dashboard_normalizes_symbol_and_returns_empty_read_only_result(client, trusted_evaluation):
    before = _table_counts()

    response = client.get("/dashboard/%20msft%20")

    assert response.status_code == 200
    assert response.json()["symbol"] == "MSFT"
    assert response.json()["snapshots"] == []
    assert response.json()["refresh_reports"] == []
    assert response.json()["price_history"] == {
        "source": YAHOO_SOURCE,
        "latest_trading_date": None,
        "candles": [],
    }
    evaluation = response.json()["evaluation"]
    assert evaluation["scope"].startswith("Pooled out-of-sample")
    assert evaluation["fold_count"] == 3
    assert evaluation["test_rows"] == 33
    assert evaluation["models"]["logistic_calibrated"]["brier_multiclass"] == 0.69
    assert evaluation["models"]["logistic_raw"]["brier_multiclass"] == 0.62
    assert evaluation["models"]["baseline_class_prior"]["brier_multiclass"] == 0.65
    assert _table_counts() == before


def test_dashboard_returns_all_chains_and_saved_evidence_without_model_calls(
    client, trusted_evaluation, monkeypatch
):
    from app import main

    def unexpected_provider(*_: object) -> object:
        raise AssertionError("dashboard must not construct an LLM provider")

    monkeypatch.setattr(main, "create_deepseek_provider_from_env", unexpected_provider)
    monkeypatch.setattr(main, "configured_deepseek_model", unexpected_provider)
    early = datetime(2026, 7, 29, 20, tzinfo=UTC)
    late = datetime(2026, 7, 31, 20, tzinfo=UTC)
    separate = datetime(2026, 9, 4, 20, tzinfo=UTC)
    with SessionLocal() as db:
        original = _snapshot(symbol="DASH", moment=early, probability=0.7)
        revised = _snapshot(symbol="DASH", moment=late, probability=0.2)
        unlinked = _snapshot(symbol="DASH", moment=separate, probability=0.6)
        research = ResearchRun(
            symbol="DASH",
            as_of_time=late,
            source_ids=["dashboard-source"],
            source_snapshot=[],
            provider="test-provider",
            request_model="test-model",
            status="succeeded",
            current_stage="complete",
            node_trace=[],
            report={"conclusion": "human review required"},
            error=None,
            completed_at=late,
        )
        db.add_all([original, revised, unlinked, research])
        db.flush()
        db.add(
            ForecastRevision(
                snapshot_id=revised.id,
                parent_snapshot_id=original.id,
                root_snapshot_id=original.id,
                reason="Saved source became eligible",
            )
        )
        db.add(
            ForecastRevisionEvidence(
                snapshot_id=revised.id,
                parent_snapshot_id=original.id,
                revision_mode="rolling_refresh",
                parent_target_start=date(2026, 7, 30),
                parent_target_end=date(2026, 8, 26),
                child_target_start=date(2026, 8, 3),
                child_target_end=date(2026, 8, 28),
                event_document_id="dashboard-source",
                event_document_sha256="d" * 64,
                event_cache_key="e" * 64,
                event_type="earnings_release",
                event_date=date(2026, 7, 30),
                event_summary="A saved earnings release became eligible.",
                evidence_quote="The company reported quarterly results.",
                source_url="https://example.com/source",
                research_run_id=research.id,
            )
        )
        db.commit()

    try:
        before = _table_counts()
        response = client.get("/dashboard/dash")

        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body["snapshots"]] == [
            str(original.id),
            str(revised.id),
            str(unlinked.id),
        ]
        assert body["snapshots"][0]["version"] == 1
        assert body["snapshots"][1]["version"] == 2
        assert body["snapshots"][0]["root_snapshot_id"] == str(original.id)
        assert body["snapshots"][1]["root_snapshot_id"] == str(original.id)
        assert body["snapshots"][2]["version"] == 1
        assert body["snapshots"][2]["root_snapshot_id"] == str(unlinked.id)
        assert body["refresh_reports"][0]["revised_snapshot"]["id"] == str(revised.id)
        assert body["refresh_reports"][0]["trigger"]["source_url"] == "https://example.com/source"
        assert _table_counts() == before
    finally:
        with SessionLocal() as db:
            db.query(ForecastRevisionEvidence).filter(
                ForecastRevisionEvidence.snapshot_id.in_([original.id, revised.id, unlinked.id])
            ).delete(synchronize_session=False)
            db.query(ForecastRevision).filter(
                ForecastRevision.snapshot_id.in_([original.id, revised.id, unlinked.id])
            ).delete(synchronize_session=False)
            db.query(ForecastSnapshot).filter(
                ForecastSnapshot.id.in_([original.id, revised.id, unlinked.id])
            ).delete(synchronize_session=False)
            db.query(ResearchRun).filter(ResearchRun.id == research.id).delete(synchronize_session=False)
            db.commit()


def test_dashboard_returns_sorted_ohlcv_candles_and_same_day_spy_close(client, trusted_evaluation):
    symbol = "CNDL"
    dates = [date(2035, 1, 2), date(2035, 1, 3), date(2035, 1, 6)]
    with SessionLocal() as db:
        db.add_all(
            [
                _price(symbol=symbol, trading_date=dates[2], close=303.0),
                _price(symbol=symbol, trading_date=dates[0], close=101.0),
                _price(symbol=symbol, trading_date=dates[1], close=202.0),
                _price(symbol="SPY", trading_date=dates[0], close=501.0),
                _price(symbol="SPY", trading_date=dates[2], close=503.0),
            ]
        )
        db.commit()

    try:
        before = _table_counts()
        response = client.get(f"/dashboard/{symbol}")

        assert response.status_code == 200
        history = response.json()["price_history"]
        assert history["source"] == YAHOO_SOURCE
        assert history["latest_trading_date"] == "2035-01-06"
        assert history["candles"] == [
            {
                "trading_date": "2035-01-02",
                "open": 100.0,
                "high": 103.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 10100,
                "benchmark_close": 501.0,
            },
            {
                "trading_date": "2035-01-03",
                "open": 201.0,
                "high": 204.0,
                "low": 200.0,
                "close": 202.0,
                "volume": 20200,
                "benchmark_close": None,
            },
            {
                "trading_date": "2035-01-06",
                "open": 302.0,
                "high": 305.0,
                "low": 301.0,
                "close": 303.0,
                "volume": 30300,
                "benchmark_close": 503.0,
            },
        ]
        assert _table_counts() == before
    finally:
        with SessionLocal() as db:
            db.query(MarketPrice).filter(
                MarketPrice.symbol.in_([symbol, "SPY"]),
                MarketPrice.trading_date.in_(dates),
                MarketPrice.source == YAHOO_SOURCE,
            ).delete(synchronize_session=False)
            db.commit()


def test_dashboard_uses_latest_observed_revision_without_mutating_forecast_snapshot(
    client, trusted_evaluation, monkeypatch
):
    import app.dashboard as dashboard

    now = datetime(2035, 1, 8, 22, tzinfo=UTC)
    monkeypatch.setattr(dashboard, "_utc_now", lambda: now)
    symbol = "REVW"
    trading_date = date(2035, 1, 3)
    snapshot = _snapshot(symbol=symbol, moment=datetime(2035, 1, 3, 21, tzinfo=UTC), probability=0.7)
    with SessionLocal() as db:
        db.add_all(
            [
                _price(symbol=symbol, trading_date=trading_date, close=100.0),
                _revision(symbol=symbol, trading_date=trading_date, close=101.0, revision_number=1, observed_at=datetime(2035, 1, 4, tzinfo=UTC)),
                _revision(symbol=symbol, trading_date=trading_date, close=111.0, revision_number=2, observed_at=datetime(2035, 1, 5, tzinfo=UTC)),
                _revision(symbol="SPY", trading_date=trading_date, close=501.0, revision_number=1, observed_at=datetime(2035, 1, 4, tzinfo=UTC)),
                snapshot,
            ]
        )
        db.commit()
        snapshot_id = snapshot.id
        stored_probability = snapshot.bearish_probability

    try:
        response = client.get(f"/dashboard/{symbol}")
        assert response.status_code == 200
        candle = response.json()["price_history"]["candles"]
        assert len(candle) == 1
        assert candle[0]["close"] == 111.0
        assert candle[0]["benchmark_close"] == 501.0
        assert response.json()["snapshots"][0]["id"] == str(snapshot_id)

        with SessionLocal() as db:
            persisted = db.get(ForecastSnapshot, snapshot_id)
        assert persisted is not None
        assert persisted.bearish_probability == stored_probability
    finally:
        with SessionLocal() as db:
            db.query(MarketPriceRevision).filter(
                MarketPriceRevision.symbol.in_([symbol, "SPY"]),
                MarketPriceRevision.trading_date == trading_date,
                MarketPriceRevision.source == YAHOO_SOURCE,
            ).delete(synchronize_session=False)
            db.query(MarketPrice).filter(
                MarketPrice.symbol == symbol,
                MarketPrice.trading_date == trading_date,
                MarketPrice.source == YAHOO_SOURCE,
            ).delete(synchronize_session=False)
            db.query(ForecastSnapshot).filter(ForecastSnapshot.id == snapshot_id).delete(synchronize_session=False)
            db.commit()


def test_dashboard_limits_price_history_to_250_most_recent_candles(client, trusted_evaluation):
    symbol = "LIMT"
    first_date = date(2030, 1, 1)
    dates = [first_date + timedelta(days=index) for index in range(251)]
    with SessionLocal() as db:
        db.add_all(
            [_price(symbol=symbol, trading_date=trading_date, close=float(index)) for index, trading_date in enumerate(dates)]
        )
        db.commit()

    try:
        response = client.get(f"/dashboard/{symbol}")

        assert response.status_code == 200
        candles = response.json()["price_history"]["candles"]
        assert len(candles) == 250
        assert candles[0]["trading_date"] == "2030-01-02"
        assert candles[-1]["trading_date"] == "2030-09-08"
    finally:
        with SessionLocal() as db:
            db.query(MarketPrice).filter(
                MarketPrice.symbol == symbol,
                MarketPrice.trading_date.in_(dates),
                MarketPrice.source == YAHOO_SOURCE,
            ).delete(synchronize_session=False)
            db.commit()


def test_dashboard_returns_null_evaluation_when_trusted_artifacts_are_missing(client, monkeypatch, tmp_path: Path):
    import app.dashboard as dashboard

    monkeypatch.setattr(dashboard, "TRUSTED_EVALUATION_DIRECTORY", tmp_path)

    response = client.get("/dashboard/none")

    assert response.status_code == 200
    assert response.json()["evaluation"] is None


def test_dashboard_returns_null_evaluation_when_metrics_are_nonfinite(client, trusted_evaluation):
    report_path = trusted_evaluation / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["pooled_oos"]["logistic_calibrated"]["accuracy"] = float("nan")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    response = client.get("/dashboard/none")

    assert response.status_code == 200
    assert response.json()["evaluation"] is None


def test_dashboard_rejects_invalid_symbol(client):
    response = client.get("/dashboard/AAPL%21")

    assert response.status_code == 422
    assert response.json()["detail"] == "symbol must contain 1-5 ASCII letters"
