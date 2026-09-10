from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

from app.market_time import _xnys_calendar_for_year, xnys_session_close_at
from app.training_data import (
    CLASS_MAPPING,
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    FORWARD_SESSIONS,
    _classify_target,
    build_training_dataset,
)


@dataclass(frozen=True)
class Price:
    symbol: str
    trading_date: date
    close: float


def _sessions(start: str, count: int) -> list[date]:
    calendar = _xnys_calendar_for_year(date.fromisoformat(start).year)
    return [item.date() for item in calendar.sessions_in_range(start, "2025-06-30")][:count]


def _feature(symbol: str, trading_date: date, *, volatility: float = 0.2) -> dict:
    return {
        "symbol": symbol,
        "trading_date": trading_date.isoformat(),
        "momentum_5d": 0.01,
        "momentum_20d": 0.02,
        "volatility_20d": volatility,
        "volume_ratio_20d": 1.1,
        "drawdown_20d": -0.03,
        "relative_return_20d": 0.01,
    }


def _loader(prices_by_symbol, calls):
    def load(symbol, *, as_of_time, source, mode):
        calls.append((symbol, as_of_time, source, mode))
        return prices_by_symbol[symbol]

    return load


def test_builds_exact_twenty_session_excess_return_and_excludes_spy():
    sessions = _sessions("2025-01-02", 45)
    decision_date, label_end_date = sessions[0], sessions[FORWARD_SESSIONS]
    label_close = xnys_session_close_at(label_end_date)
    calls = []
    prices = {
        "AAPL": [Price("AAPL", decision_date, 100.0), Price("AAPL", label_end_date, 110.0)],
        "SPY": [Price("SPY", decision_date, 100.0), Price("SPY", label_end_date, 105.0)],
    }

    dataset, metadata = build_training_dataset(
        [_feature("AAPL", decision_date), _feature("SPY", decision_date)],
        as_of_time=label_close,
        source="test-source",
        mode="historical_research",
        price_loader=_loader(prices, calls),
    )

    assert list(dataset.columns) == [
        "symbol",
        "trading_date",
        "decision_time",
        "label_end_date",
        "label_available_at",
        "target",
        "forward_excess_return",
        "label_threshold",
        *FEATURE_COLUMNS,
    ]
    assert len(dataset) == 1
    row = dataset.iloc[0]
    assert row.symbol == "AAPL"
    assert row.label_end_date == label_end_date
    assert row.label_available_at == label_close
    assert row.forward_excess_return == pytest.approx(0.05)
    assert row.target == CLASS_MAPPING["bullish"]
    assert all(call[1] == label_close for call in calls)
    assert metadata["excluded_by_reason"] == {}
    assert metadata["label"]["forward_sessions"] == 20


def test_threshold_boundaries_are_neutral():
    threshold = 0.01

    assert _classify_target(threshold, threshold) == CLASS_MAPPING["neutral"]
    assert _classify_target(-threshold, threshold) == CLASS_MAPPING["neutral"]
    assert _classify_target(threshold + 1e-12, threshold) == CLASS_MAPPING["bullish"]
    assert _classify_target(-threshold - 1e-12, threshold) == CLASS_MAPPING["bearish"]


def test_last_twenty_sessions_are_not_labelled_before_maturity():
    sessions = _sessions("2025-01-02", 45)
    decision_date = sessions[20]
    cutoff = xnys_session_close_at(sessions[39])
    calls = []

    dataset, metadata = build_training_dataset(
        [_feature("AAPL", decision_date)],
        as_of_time=cutoff,
        price_loader=_loader({"AAPL": [], "SPY": []}, calls),
    )

    assert dataset.empty
    assert metadata["excluded_by_reason"] == {"label_not_mature_at_cutoff": 1}
    assert calls == []


def test_each_symbol_uses_its_own_endpoint_prices_without_cross_symbol_shift():
    sessions = _sessions("2025-01-02", 45)
    decision_date, label_end_date = sessions[0], sessions[FORWARD_SESSIONS]
    close = xnys_session_close_at(label_end_date)
    calls = []
    prices = {
        "AAPL": [Price("AAPL", decision_date, 100.0), Price("AAPL", label_end_date, 120.0)],
        "MSFT": [Price("MSFT", decision_date, 100.0), Price("MSFT", label_end_date, 90.0)],
        "SPY": [Price("SPY", decision_date, 100.0), Price("SPY", label_end_date, 100.0)],
    }

    dataset, _ = build_training_dataset(
        [_feature("AAPL", decision_date), _feature("MSFT", decision_date)],
        as_of_time=close,
        price_loader=_loader(prices, calls),
    )

    targets = dict(zip(dataset.symbol, dataset.target, strict=True))
    assert targets == {"AAPL": CLASS_MAPPING["bullish"], "MSFT": CLASS_MAPPING["bearish"]}
    assert {call[0] for call in calls} == {"AAPL", "MSFT", "SPY"}


def test_label_cut_at_maturity_is_immune_to_later_price_revision():
    sessions = _sessions("2025-01-02", 45)
    decision_date, label_end_date = sessions[0], sessions[FORWARD_SESSIONS]
    label_close = xnys_session_close_at(label_end_date)
    cutoff_after_revision = label_close + timedelta(days=7)
    calls = []

    def revision_loader(symbol, *, as_of_time, source, mode):
        calls.append((symbol, as_of_time))
        if symbol == "AAPL":
            end_close = 110.0 if as_of_time == label_close else 200.0
        else:
            end_close = 105.0
        return [Price(symbol, decision_date, 100.0), Price(symbol, label_end_date, end_close)]

    dataset, _ = build_training_dataset(
        [_feature("AAPL", decision_date)],
        as_of_time=cutoff_after_revision,
        price_loader=revision_loader,
    )

    assert dataset.iloc[0].forward_excess_return == pytest.approx(0.05)
    assert all(call_time == label_close for _, call_time in calls)


def test_missing_endpoints_are_excluded_without_a_backfilled_target():
    sessions = _sessions("2025-01-02", 45)
    decision_date, label_end_date = sessions[0], sessions[FORWARD_SESSIONS]
    close = xnys_session_close_at(label_end_date)
    prices = {
        "AAPL": [Price("AAPL", decision_date, 100.0)],
        "SPY": [Price("SPY", decision_date, 100.0), Price("SPY", label_end_date, 105.0)],
    }

    dataset, metadata = build_training_dataset(
        [_feature("AAPL", decision_date)],
        as_of_time=close,
        price_loader=_loader(prices, []),
    )

    assert dataset.empty
    assert metadata["excluded_by_reason"] == {"missing_endpoint_price": 1}


def test_rejects_negative_volatility_and_unknown_feature_version():
    sessions = _sessions("2025-01-02", 45)
    decision_date, label_end_date = sessions[0], sessions[FORWARD_SESSIONS]
    close = xnys_session_close_at(label_end_date)
    prices = {
        "AAPL": [Price("AAPL", decision_date, 100.0), Price("AAPL", label_end_date, 110.0)],
        "SPY": [Price("SPY", decision_date, 100.0), Price("SPY", label_end_date, 105.0)],
    }

    with pytest.raises(ValueError, match="non-negative"):
        build_training_dataset(
            [_feature("AAPL", decision_date, volatility=-0.1)],
            as_of_time=close,
            price_loader=_loader(prices, []),
        )
    with pytest.raises(ValueError, match="unsupported feature_version"):
        build_training_dataset(
            [_feature("AAPL", decision_date)],
            as_of_time=close,
            price_loader=_loader(prices, []),
            feature_version="unknown-v0",
        )
