from datetime import UTC, date, datetime, timedelta

import pytest

from app.forecast_contract import (
    HORIZON_SESSIONS,
    PRICE_BASIS,
    RETURN_THRESHOLD,
    CorporateAction,
    ForecastContractError,
    PriceBasisError,
    classify_absolute_return,
    create_root_contract,
    future_xnys_sessions,
    is_child_target_compatible,
    latest_completed_xnys_session,
    revision_state,
    revision_state_at,
    target_expired,
    validate_child_target,
)
from app.market_time import xnys_session_close_at


def _metadata(*, actions=(), verified=True, basis=PRICE_BASIS, adjusted_close_present=True):
    return {
        "basis": basis,
        "provider_behavior_verified": verified,
        "adjusted_close_present": adjusted_close_present,
        "corporate_actions": actions,
        "verification_notes": ("fixture response checked",),
    }


def _root(anchor_date: date = date(2025, 12, 3)):
    return create_root_contract(
        anchor_date=anchor_date,
        anchor_close=100.0,
        price_source="yahoo-finance-chart",
        price_version="chart-response-v1",
        price_hash="a" * 64,
        price_basis_metadata=_metadata(),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-0.0200001, "bearish"),
        (-RETURN_THRESHOLD, "neutral"),
        (0.0, "neutral"),
        (RETURN_THRESHOLD, "neutral"),
        (0.0200001, "bullish"),
    ],
)
def test_absolute_return_classes_have_inclusive_neutral_boundaries(value, expected):
    assert classify_absolute_return(value) == expected


def test_root_contract_uses_twenty_future_xnys_sessions_across_holidays_and_new_year():
    contract = _root()

    sessions = future_xnys_sessions(contract.anchor_date)
    assert contract.horizon_sessions == HORIZON_SESSIONS
    assert contract.target_end_date == sessions[-1]
    assert len(sessions) == 20
    assert date(2025, 12, 25) not in sessions
    assert date(2026, 1, 1) not in sessions
    assert contract.classify(102.0) == "neutral"
    assert contract.classify(102.01) == "bullish"


def test_after_hours_uses_the_same_day_only_after_the_actual_close():
    assert latest_completed_xnys_session(datetime(2025, 3, 3, 20, 59, tzinfo=UTC)) == date(2025, 2, 28)
    assert latest_completed_xnys_session(datetime(2025, 3, 3, 21, 0, tzinfo=UTC)) == date(2025, 3, 3)
    assert latest_completed_xnys_session(datetime(2025, 3, 3, 23, 0, tzinfo=UTC)) == date(2025, 3, 3)


def test_fixed_target_revision_keeps_root_target_and_expires_at_end_session():
    contract = _root()
    first_child_date = future_xnys_sessions(contract.anchor_date)[2]
    state = revision_state(contract, current_market_date=first_child_date, current_close=101.0)

    assert state.remaining_sessions == 17
    assert state.realized_return_from_anchor == pytest.approx(0.01)
    assert state.expired is False
    assert revision_state_at(
        contract,
        as_of_time=datetime.combine(contract.target_end_date, datetime.min.time(), tzinfo=UTC),
        latest_close=101.0,
    ).remaining_sessions == 1
    target_close = xnys_session_close_at(contract.target_end_date)
    assert target_expired(contract, as_of_time=target_close - timedelta(seconds=1)) is False
    assert target_expired(contract, as_of_time=target_close) is True

    final = revision_state(contract, current_market_date=contract.target_end_date, current_close=103.0)
    assert final.remaining_sessions == 0
    assert final.expired is True
    with pytest.raises(ForecastContractError, match="evaluation"):
        final.require_revisable()


def test_root_and_child_contract_must_be_identical_including_price_input_manifest():
    root = _root()
    child = _root()
    validate_child_target(root, child)
    assert is_child_target_compatible(root, child) is True

    changed_price = create_root_contract(
        anchor_date=root.anchor_date,
        anchor_close=root.anchor_close,
        price_source=root.price_source,
        price_version=root.price_version,
        price_hash="b" * 64,
        price_basis_metadata=_metadata(),
    )
    with pytest.raises(ForecastContractError, match="price_hash"):
        validate_child_target(root, changed_price)
    assert is_child_target_compatible(root, changed_price) is False


def test_split_or_unknown_price_basis_blocks_contract_and_cash_dividend_is_retained_as_warning():
    with pytest.raises(PriceBasisError) as split_error:
        create_root_contract(
            anchor_date=date(2025, 12, 3),
            anchor_close=100.0,
            price_source="yahoo-finance-chart",
            price_version="chart-response-v1",
            price_hash="a" * 64,
            price_basis_metadata=_metadata(actions=(CorporateAction("split", date(2025, 12, 15)),)),
        )
    assert split_error.value.code == "corporate_action_unsupported"

    with pytest.raises(PriceBasisError) as unknown_error:
        create_root_contract(
            anchor_date=date(2025, 12, 3),
            anchor_close=100.0,
            price_source="yahoo-finance-chart",
            price_version="chart-response-v1",
            price_hash="a" * 64,
            price_basis_metadata=_metadata(actions=({"kind": "unknown", "effective_date": "2025-12-15"},)),
        )
    assert unknown_error.value.code == "price_basis_unverified"

    contract = create_root_contract(
        anchor_date=date(2025, 12, 3),
        anchor_close=100.0,
        price_source="yahoo-finance-chart",
        price_version="chart-response-v1",
        price_hash="a" * 64,
        price_basis_metadata=_metadata(actions=(CorporateAction("cash_dividend", date(2025, 12, 15)),)),
    )
    assert contract.price_basis_check.warnings == ("cash_dividend:2025-12-15",)


def test_price_basis_requires_a_verified_quote_close_report():
    with pytest.raises(PriceBasisError) as error:
        create_root_contract(
            anchor_date=date(2025, 12, 3),
            anchor_close=100.0,
            price_source="yahoo-finance-chart",
            price_version="chart-response-v1",
            price_hash="a" * 64,
            price_basis_metadata=_metadata(verified=False),
        )
    assert error.value.code == "price_basis_unverified"

    with pytest.raises(PriceBasisError) as mismatched_basis:
        create_root_contract(
            anchor_date=date(2025, 12, 3),
            anchor_close=100.0,
            price_source="yahoo-finance-chart",
            price_version="chart-response-v1",
            price_hash="a" * 64,
            price_basis_metadata=_metadata(basis="adjclose-v1"),
        )
    assert mismatched_basis.value.code == "price_basis_unverified"
