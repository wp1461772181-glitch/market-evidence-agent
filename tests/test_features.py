from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.features import (
    FEATURE_VERSION,
    RETURN_LOOKBACK_DAYS,
    TRADING_DAYS_PER_YEAR,
    build_features,
    build_features_from_snapshot,
    metadata,
)
from app.market_time import xnys_session_close_at
from app.database import SessionLocal
from app.models import MarketPriceRevision


@dataclass(frozen=True)
class Price:
    symbol: str
    trading_date: date
    close: float
    volume: float


def _series(
    symbol: str,
    *,
    daily_growth: float,
    start: date = date(2025, 1, 2),
    count: int = 25,
    volume_start: float = 1_000.0,
) -> list[Price]:
    return [
        Price(
            symbol=symbol,
            trading_date=start + timedelta(days=index),
            close=100.0 * (1.0 + daily_growth) ** index,
            volume=volume_start + index,
        )
        for index in range(count)
    ]


def _row(report, symbol: str, day: date):
    return next(row for row in report.rows if row.symbol == symbol and row.trading_date == day)


def test_twenty_return_features_match_hand_calculated_fixture():
    stock = _series("AAPL", daily_growth=0.01)
    spy = _series("SPY", daily_growth=0.005)

    report = build_features({"AAPL": stock, "SPY": spy})
    row = _row(report, "AAPL", stock[20].trading_date)

    assert row.feature_version == FEATURE_VERSION
    assert row.momentum_5d == pytest.approx(1.01**5 - 1.0)
    assert row.momentum_20d == pytest.approx(1.01**20 - 1.0)
    assert row.volatility_20d == pytest.approx(0.0)
    assert row.volume_ratio_20d == pytest.approx(1020.0 / (sum(range(1000, 1020)) / 20))
    assert row.drawdown_20d == pytest.approx(0.0)
    assert row.relative_return_20d == pytest.approx((1.01**20 - 1.0) - (1.005**20 - 1.0))


def test_volatility_is_annualized_sample_standard_deviation_of_twenty_returns():
    close_returns = [0.01 if index % 2 == 0 else -0.01 for index in range(RETURN_LOOKBACK_DAYS)]
    closes = [100.0]
    for value in close_returns:
        closes.append(closes[-1] * (1.0 + value))
    start = date(2025, 1, 2)
    stock = [Price("AAPL", start + timedelta(days=index), close, 1_000) for index, close in enumerate(closes)]
    spy = _series("SPY", daily_growth=0.0, count=len(stock))

    row = _row(build_features({"AAPL": stock, "SPY": spy}), "AAPL", stock[-1].trading_date)
    mean = sum(close_returns) / RETURN_LOOKBACK_DAYS
    expected = math.sqrt(sum((value - mean) ** 2 for value in close_returns) / 19) * math.sqrt(TRADING_DAYS_PER_YEAR)
    assert row.volatility_20d == pytest.approx(expected)


def test_future_stock_or_benchmark_bars_do_not_change_past_features():
    stock = _series("AAPL", daily_growth=0.01, count=31)
    spy = _series("SPY", daily_growth=0.005, count=31)
    baseline = build_features({"AAPL": stock, "SPY": spy})

    changed_stock = stock + [Price("AAPL", stock[-1].trading_date + timedelta(days=1), 1_000_000.0, 9_000_000)]
    changed_spy = spy + [Price("SPY", spy[-1].trading_date + timedelta(days=1), 0.01, 1.0)]
    with_future_data = build_features({"AAPL": changed_stock, "SPY": changed_spy})

    baseline_rows = {(row.symbol, row.trading_date): row for row in baseline.rows}
    future_rows = {(row.symbol, row.trading_date): row for row in with_future_data.rows}
    assert {key: future_rows[key] for key in baseline_rows} == baseline_rows


def test_missing_benchmark_date_is_skipped_without_fill():
    stock = _series("AAPL", daily_growth=0.01, count=22)
    spy = _series("SPY", daily_growth=0.005, count=22)
    del spy[20]

    report = build_features({"AAPL": stock, "SPY": spy})

    assert ("AAPL", stock[20].trading_date) not in {(row.symbol, row.trading_date) for row in report.rows}
    assert ("AAPL", stock[20].trading_date, "missing_benchmark_date") in {
        (skip.symbol, skip.trading_date, skip.reason) for skip in report.skips
    }


@pytest.mark.parametrize(
    ("bad_rows", "match"),
    [
        (
            [
                Price("AAPL", date(2025, 1, 2), 100.0, 1_000),
                Price("AAPL", date(2025, 1, 2), 101.0, 1_000),
            ],
            "duplicate trading_date",
        ),
        ([Price("AAPL", date(2025, 1, 2), math.nan, 1_000)], "finite"),
        ([Price("AAPL", date(2025, 1, 2), 100.0, math.inf)], "finite"),
    ],
)
def test_invalid_or_duplicate_input_is_rejected(bad_rows, match):
    with pytest.raises(ValueError, match=match):
        build_features({"AAPL": bad_rows, "SPY": _series("SPY", daily_growth=0.0)})


def test_warmup_and_zero_prior_volume_are_reported():
    stock = [
        Price("AAPL", date(2025, 1, 2) + timedelta(days=index), 100.0 + index, 0.0)
        for index in range(21)
    ]
    spy = _series("SPY", daily_growth=0.0, count=21)

    report = build_features({"AAPL": stock, "SPY": spy})

    assert report.skipped_by_reason["insufficient_history"] == 40
    assert report.skipped_by_reason["non_positive_prior_volume_mean"] == 1


def test_snapshot_builder_uses_each_day_session_close_not_final_cutoff():
    from app.market_time import _xnys_calendar_for_year

    calendar = _xnys_calendar_for_year(2025)
    sessions = [timestamp.date() for timestamp in calendar.sessions_in_range("2025-01-02", "2025-02-15")][:25]
    stock = [Price("AAPL", trading_date, 100.0 + index, 1_000) for index, trading_date in enumerate(sessions)]
    spy = [Price("SPY", trading_date, 200.0 + index, 2_000) for index, trading_date in enumerate(sessions)]
    final_cutoff = datetime(2025, 3, 1, 21, 0, tzinfo=UTC)
    calls = []

    def loader(symbol, *, as_of_time, source, mode):
        calls.append((symbol, as_of_time))
        return [row for row in {"AAPL": stock, "SPY": spy}[symbol] if row.trading_date <= as_of_time.date()]

    report = build_features_from_snapshot(
        ["AAPL"],
        as_of_time=final_cutoff,
        source="test-source",
        mode="historical_research",
        data_loader=loader,
    )

    assert _row(report, "AAPL", sessions[20]).momentum_20d == pytest.approx(0.2)
    assert ("AAPL", xnys_session_close_at(sessions[20])) in calls


def test_xnys_early_close_is_used_for_day_specific_cutoff():
    assert xnys_session_close_at(date(2025, 11, 28)) == datetime(2025, 11, 28, 18, 0, tzinfo=UTC)


def test_later_database_revision_does_not_rewrite_past_historical_feature(client):
    """This exercises the real revision selector, not only a fake data loader."""
    from app.market_time import _xnys_calendar_for_year

    source = f"feature-revision-{uuid4().hex}"
    calendar = _xnys_calendar_for_year(2025)
    sessions = [timestamp.date() for timestamp in calendar.sessions_in_range("2025-01-02", "2025-02-15")][:21]
    last_close = xnys_session_close_at(sessions[-1])
    revision_seen_later = last_close + timedelta(days=1)

    def add_revision(*, symbol: str, trading_date: date, close: float, revision_number: int, initial: bool, observed_at: datetime):
        with SessionLocal() as db:
            db.add(
                MarketPriceRevision(
                    market_price_id=None,
                    ingestion_run_id=None,
                    symbol=symbol,
                    trading_date=trading_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1_000,
                    source=source,
                    revision_number=revision_number,
                    content_hash=(f"{symbol}{trading_date.isoformat()}{revision_number}").ljust(64, "0"),
                    available_at=xnys_session_close_at(trading_date),
                    observed_at=observed_at,
                    is_initial_backfill=initial,
                )
            )
            db.commit()

    try:
        for index, trading_date in enumerate(sessions):
            add_revision(
                symbol="AAPL",
                trading_date=trading_date,
                close=100.0 + index,
                revision_number=1,
                initial=True,
                observed_at=last_close + timedelta(days=7),
            )
            add_revision(
                symbol="SPY",
                trading_date=trading_date,
                close=200.0 + index,
                revision_number=1,
                initial=True,
                observed_at=last_close + timedelta(days=7),
            )
        add_revision(
            symbol="AAPL",
            trading_date=sessions[-1],
            close=240.0,
            revision_number=2,
            initial=False,
            observed_at=revision_seen_later,
        )

        report = build_features_from_snapshot(
            ["AAPL"],
            as_of_time=revision_seen_later + timedelta(minutes=1),
            source=source,
            mode="historical_research",
        )

        row = _row(report, "AAPL", sessions[-1])
        assert row.momentum_20d == pytest.approx(0.20)
    finally:
        with SessionLocal() as db:
            db.query(MarketPriceRevision).filter(MarketPriceRevision.source == source).delete()
            db.commit()


def test_export_metadata_records_snapshot_semantics():
    cutoff = datetime(2025, 3, 1, 21, 0, tzinfo=UTC)
    payload = metadata(as_of_time=cutoff, source="test-source", mode="historical_research")

    assert payload["feature_version"] == FEATURE_VERSION
    assert payload["snapshot_mode"] == "historical_research"
    assert "later provider revisions" in payload["historical_research_limit"]
    observed_payload = metadata(as_of_time=cutoff, source="test-source", mode="observed")
    assert "actually observed" in observed_payload["observed_limit"]
