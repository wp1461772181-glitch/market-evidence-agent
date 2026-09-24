"""V2's immutable, absolute-price forecast target contract.

The existing Week 4 records use a rolling 20-session *excess-return*
question.  This module intentionally has no database dependency so V2 tables,
training, the worker, and the UI can all validate the same new question before
persisting it: a stock's own close-to-close return over a fixed 20 XNYS-session
window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Mapping

from .market_time import _xnys_calendar_for_year, normalize_utc, xnys_session_close_at


TARGET_SPEC_VERSION = "absolute-close-v1"
PRICE_BASIS = "provider_quote_close_v1"
XNYS_CALENDAR_NAME = "XNYS"
XNYS_CALENDAR_VERSION = "exchange-calendars-xnys-v1"
HORIZON_SESSIONS = 20
RETURN_THRESHOLD = 0.02

ForecastClass = Literal["bearish", "neutral", "bullish"]
CorporateActionKind = Literal["split", "cash_dividend", "unknown", "price_basis_change"]


class ForecastContractError(ValueError):
    """A stable, safe reason why a V2 target cannot be used."""

    def __init__(self, message: str, *, code: str = "invalid_target_contract") -> None:
        super().__init__(message)
        self.code = code


class PriceBasisError(ForecastContractError):
    """Raised when a requested interval has no verified single price basis."""


@dataclass(frozen=True)
class CorporateAction:
    """A provider-disclosed action relevant to a close-price interval.

    V2 price return deliberately excludes cash dividend reinvestment.  A cash
    dividend is therefore recorded as a warning, while a split or an unknown
    basis change blocks the interval until a later price-basis version supports
    it.
    """

    kind: CorporateActionKind
    effective_date: date
    known: bool = True

    def __post_init__(self) -> None:
        if self.kind not in {"split", "cash_dividend", "unknown", "price_basis_change"}:
            raise ForecastContractError("corporate action kind is unsupported")
        if not isinstance(self.effective_date, date):
            raise ForecastContractError("corporate action effective_date must be a date")


@dataclass(frozen=True)
class PriceBasisCheck:
    """Frozen result of checking the provider's price semantics for one target."""

    price_basis: str
    provider_behavior_verified: bool
    adjusted_close_present: bool
    warnings: tuple[str, ...] = ()
    verification_notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_basis": self.price_basis,
            "provider_behavior_verified": self.provider_behavior_verified,
            "adjusted_close_present": self.adjusted_close_present,
            "warnings": list(self.warnings),
            "verification_notes": list(self.verification_notes),
        }


@dataclass(frozen=True)
class ForecastTargetContract:
    """All fields that define one immutable V2 prediction question."""

    anchor_date: date
    anchor_close: float
    target_end_date: date
    threshold: float
    horizon_sessions: int
    calendar_name: str
    calendar_version: str
    price_source: str
    price_version: str
    price_hash: str
    price_basis: str
    price_basis_check: PriceBasisCheck
    target_spec_version: str = TARGET_SPEC_VERSION

    def __post_init__(self) -> None:
        _validate_target_fields(self)

    def absolute_return(self, target_close: float) -> float:
        return absolute_return(anchor_close=self.anchor_close, target_close=target_close)

    def classify(self, target_close: float) -> ForecastClass:
        return classify_absolute_return(self.absolute_return(target_close), threshold=self.threshold)

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchor_date": self.anchor_date.isoformat(),
            "anchor_close": self.anchor_close,
            "target_end_date": self.target_end_date.isoformat(),
            "threshold": self.threshold,
            "horizon_sessions": self.horizon_sessions,
            "calendar_name": self.calendar_name,
            "calendar_version": self.calendar_version,
            "price_source": self.price_source,
            "price_version": self.price_version,
            "price_hash": self.price_hash,
            "price_basis": self.price_basis,
            "price_basis_check": self.price_basis_check.as_dict(),
            "target_spec_version": self.target_spec_version,
        }


def target_contract_from_dict(value: Mapping[str, Any]) -> ForecastTargetContract:
    """Revalidate a persisted JSON target before publishing or replaying it."""
    try:
        check_data = value["price_basis_check"]
        if not isinstance(check_data, Mapping):
            raise TypeError("price_basis_check must be an object")
        if not isinstance(check_data["provider_behavior_verified"], bool) or not isinstance(
            check_data["adjusted_close_present"], bool
        ):
            raise TypeError("price_basis_check flags must be boolean")
        check = PriceBasisCheck(
            price_basis=check_data["price_basis"],
            provider_behavior_verified=check_data["provider_behavior_verified"],
            adjusted_close_present=check_data["adjusted_close_present"],
            warnings=tuple(check_data.get("warnings", ())),
            verification_notes=tuple(check_data.get("verification_notes", ())),
        )
        contract = ForecastTargetContract(
            anchor_date=date.fromisoformat(value["anchor_date"]),
            anchor_close=value["anchor_close"],
            target_end_date=date.fromisoformat(value["target_end_date"]),
            threshold=value["threshold"],
            horizon_sessions=value["horizon_sessions"],
            calendar_name=value["calendar_name"],
            calendar_version=value["calendar_version"],
            price_source=value["price_source"],
            price_version=value["price_version"],
            price_hash=value["price_hash"],
            price_basis=value["price_basis"],
            price_basis_check=check,
            target_spec_version=value["target_spec_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ForecastContractError):
            raise
        raise ForecastContractError("stored target contract is invalid") from exc
    if contract.as_dict() != dict(value):
        raise ForecastContractError("stored target contract contains unsupported fields")
    return contract


@dataclass(frozen=True)
class RevisionState:
    """Inputs which change on a child version while its target stays fixed."""

    current_market_date: date
    current_close: float
    remaining_sessions: int
    realized_return_from_anchor: float
    expired: bool

    def require_revisable(self) -> None:
        if self.expired:
            raise ForecastContractError(
                "the fixed target has reached its end session; create an evaluation instead of a revision",
                code="target_expired",
            )


def create_root_contract(
    *,
    anchor_date: date,
    anchor_close: float,
    price_source: str,
    price_version: str,
    price_hash: str,
    price_basis_metadata: object | Mapping[str, Any],
    threshold: float = RETURN_THRESHOLD,
    horizon_sessions: int = HORIZON_SESSIONS,
    calendar_version: str = XNYS_CALENDAR_VERSION,
) -> ForecastTargetContract:
    """Create a verified root target ending after exactly 20 future sessions."""
    if horizon_sessions != HORIZON_SESSIONS:
        raise ForecastContractError(
            f"{TARGET_SPEC_VERSION} requires horizon_sessions={HORIZON_SESSIONS}",
            code="unsupported_target_spec",
        )
    if threshold != RETURN_THRESHOLD:
        raise ForecastContractError(
            f"{TARGET_SPEC_VERSION} requires threshold={RETURN_THRESHOLD}",
            code="unsupported_target_spec",
        )
    target_end_date = future_xnys_sessions(anchor_date, horizon_sessions)[-1]
    check = validate_price_basis(
        metadata=price_basis_metadata,
        anchor_date=anchor_date,
        target_end_date=target_end_date,
    )
    return ForecastTargetContract(
        anchor_date=anchor_date,
        anchor_close=_positive_finite(anchor_close, "anchor_close"),
        target_end_date=target_end_date,
        threshold=threshold,
        horizon_sessions=horizon_sessions,
        calendar_name=XNYS_CALENDAR_NAME,
        calendar_version=calendar_version,
        price_source=_nonempty(price_source, "price_source"),
        price_version=_nonempty(price_version, "price_version"),
        price_hash=_nonempty(price_hash, "price_hash"),
        price_basis=check.price_basis,
        price_basis_check=check,
    )


def absolute_return(*, anchor_close: float, target_close: float) -> float:
    """Return the stock's own close-price return; it is never SPY-relative."""
    return _positive_finite(target_close, "target_close") / _positive_finite(anchor_close, "anchor_close") - 1.0


def classify_absolute_return(value: float, *, threshold: float = RETURN_THRESHOLD) -> ForecastClass:
    """Classify return with the required inclusive neutral boundaries."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ForecastContractError("return must be a finite number")
    if threshold != RETURN_THRESHOLD:
        raise ForecastContractError(
            f"{TARGET_SPEC_VERSION} requires threshold={RETURN_THRESHOLD}",
            code="unsupported_target_spec",
        )
    # ``target_close / anchor_close - 1`` can turn an exact decimal 2% into
    # 0.020000000000000018.  Preserve the documented inclusive boundary while
    # still treating material moves beyond it as directional classes.
    boundary_tolerance = 1e-12
    if value > threshold and not math.isclose(value, threshold, rel_tol=0.0, abs_tol=boundary_tolerance):
        return "bullish"
    if value < -threshold and not math.isclose(value, -threshold, rel_tol=0.0, abs_tol=boundary_tolerance):
        return "bearish"
    return "neutral"


def future_xnys_sessions(anchor_date: date, count: int = HORIZON_SESSIONS) -> tuple[date, ...]:
    """Return the next ``count`` sessions, excluding the anchor session itself."""
    if count <= 0:
        raise ForecastContractError("session count must be positive")
    _require_xnys_session(anchor_date, "anchor_date")
    # Twenty sessions is well inside this padded calendar's explicit range;
    # the generous window still makes the helper safe for test callers.
    calendar = _xnys_calendar_for_year(anchor_date.year)
    end = anchor_date + timedelta(days=max(90, count * 4))
    sessions = tuple(
        session.date()
        for session in calendar.sessions_in_range(anchor_date + timedelta(days=1), end)
    )
    if len(sessions) < count:
        raise ForecastContractError("XNYS calendar did not provide enough future sessions")
    return sessions[:count]


def latest_completed_xnys_session(as_of_time: datetime) -> date:
    """Find the latest daily bar whose actual XNYS close has passed."""
    instant = normalize_utc(as_of_time, name="as_of_time")
    calendar = _xnys_calendar_for_year(instant.year)
    sessions = calendar.sessions_in_range(instant.date() - timedelta(days=21), instant.date())
    for session in reversed(sessions):
        candidate = session.date()
        if xnys_session_close_at(candidate) <= instant:
            return candidate
    raise ForecastContractError("no completed XNYS session is available")


def revision_state(
    contract: ForecastTargetContract,
    *,
    current_market_date: date,
    current_close: float,
) -> RevisionState:
    """Calculate child-version inputs using the root's original target."""
    _require_xnys_session(current_market_date, "current_market_date")
    if current_market_date < contract.anchor_date:
        raise ForecastContractError("current_market_date must not predate anchor_date")
    close = _positive_finite(current_close, "current_close")
    remaining = len(
        tuple(
            session
            for session in future_xnys_sessions(current_market_date, HORIZON_SESSIONS + 1)
            if session <= contract.target_end_date
        )
    )
    # The horizon request above is a convenient calendar iterator.  If the
    # target is more than 21 sessions away it is still a valid root state; use
    # the target's own interval rather than silently truncate the count.
    if current_market_date < contract.target_end_date and remaining == HORIZON_SESSIONS + 1:
        remaining = _sessions_until(current_market_date, contract.target_end_date)
    expired = current_market_date >= contract.target_end_date or remaining <= 0
    return RevisionState(
        current_market_date=current_market_date,
        current_close=close,
        remaining_sessions=remaining,
        realized_return_from_anchor=absolute_return(anchor_close=contract.anchor_close, target_close=close),
        expired=expired,
    )


def revision_state_at(
    contract: ForecastTargetContract,
    *,
    as_of_time: datetime,
    latest_close: float,
) -> RevisionState:
    """Time-aware revision state for UI/API callers around close and holidays."""
    return revision_state(
        contract,
        current_market_date=latest_completed_xnys_session(as_of_time),
        current_close=latest_close,
    )


def target_expired(contract: ForecastTargetContract, *, as_of_time: datetime) -> bool:
    """Return true only after the target session's actual close instant."""
    return normalize_utc(as_of_time, name="as_of_time") >= xnys_session_close_at(contract.target_end_date)


def validate_child_target(root: ForecastTargetContract, child: ForecastTargetContract) -> None:
    """Reject a child that changes any part of its root's target question."""
    root_identity = _target_identity(root)
    child_identity = _target_identity(child)
    if root_identity != child_identity:
        changed = sorted(key for key in root_identity if root_identity[key] != child_identity.get(key))
        raise ForecastContractError(
            "child target contract differs from root: " + ", ".join(changed),
            code="target_contract_mismatch",
        )


def is_child_target_compatible(root: ForecastTargetContract, child: ForecastTargetContract) -> bool:
    try:
        validate_child_target(root, child)
    except ForecastContractError:
        return False
    return True


def validate_price_basis(
    *,
    metadata: object | Mapping[str, Any],
    anchor_date: date,
    target_end_date: date,
) -> PriceBasisCheck:
    """Validate a provider report without binding V2 to one provider class.

    ``metadata`` can be a dataclass-like object or a mapping.  Its stable
    surface is ``basis``, ``provider_behavior_verified``,
    ``adjusted_close_present``, ``corporate_actions`` and optional
    ``verification_notes``.  This lets ``market_data`` evolve its provider
    parser without changing V2's stored contract.
    """
    _require_xnys_session(anchor_date, "anchor_date")
    _require_xnys_session(target_end_date, "target_end_date")
    if target_end_date <= anchor_date:
        raise ForecastContractError("target_end_date must be after anchor_date")

    basis = _metadata_value(metadata, "basis", default=None)
    if basis != PRICE_BASIS:
        raise PriceBasisError(
            f"expected a verified {PRICE_BASIS} basis",
            code="price_basis_unverified",
        )
    if not bool(_metadata_value(metadata, "provider_behavior_verified", default=False)):
        raise PriceBasisError("provider quote-close behavior has not been verified", code="price_basis_unverified")

    actions: list[CorporateAction] = []
    for value in _metadata_value(metadata, "corporate_actions", default=()):
        # A provider can identify that an action exists but fail to give a
        # usable date.  We cannot prove it sits outside the target interval,
        # so fail closed with the product-visible price-basis reason.
        if _metadata_value(value, "effective_date", default=None) is None:
            raise PriceBasisError(
                "a corporate action has no verifiable effective date",
                code="price_basis_unverified",
            )
        actions.append(_normalise_corporate_action(value))
    warnings: list[str] = []
    for action in actions:
        if not anchor_date < action.effective_date <= target_end_date:
            continue
        if not action.known or action.kind in {"unknown", "price_basis_change"}:
            raise PriceBasisError(
                "a corporate action has an unknown price basis in the target interval",
                code="price_basis_unverified",
            )
        if action.kind == "split":
            raise PriceBasisError(
                "a split crosses the target interval and is unsupported by provider_quote_close_v1",
                code="corporate_action_unsupported",
            )
        if action.kind == "cash_dividend":
            warnings.append(f"cash_dividend:{action.effective_date.isoformat()}")

    notes = _metadata_value(metadata, "verification_notes", default=())
    return PriceBasisCheck(
        price_basis=PRICE_BASIS,
        provider_behavior_verified=True,
        adjusted_close_present=bool(_metadata_value(metadata, "adjusted_close_present", default=False)),
        warnings=tuple(sorted(set(warnings))),
        verification_notes=tuple(str(item) for item in notes),
    )


def _sessions_until(start_exclusive: date, end_inclusive: date) -> int:
    if end_inclusive <= start_exclusive:
        return 0
    calendar = _xnys_calendar_for_year(start_exclusive.year)
    return len(calendar.sessions_in_range(start_exclusive + timedelta(days=1), end_inclusive))


def _target_identity(contract: ForecastTargetContract) -> dict[str, Any]:
    payload = contract.as_dict()
    # The price-basis check is itself frozen target evidence, so it is included
    # rather than allowing a revision to silently alter price semantics.
    return payload


def _validate_target_fields(contract: ForecastTargetContract) -> None:
    _require_xnys_session(contract.anchor_date, "anchor_date")
    _require_xnys_session(contract.target_end_date, "target_end_date")
    _positive_finite(contract.anchor_close, "anchor_close")
    if contract.target_end_date != future_xnys_sessions(contract.anchor_date, contract.horizon_sessions)[-1]:
        raise ForecastContractError("target_end_date does not match the fixed XNYS horizon")
    if contract.horizon_sessions != HORIZON_SESSIONS or contract.threshold != RETURN_THRESHOLD:
        raise ForecastContractError("contract does not match absolute-close-v1", code="unsupported_target_spec")
    if contract.calendar_name != XNYS_CALENDAR_NAME or not contract.calendar_version:
        raise ForecastContractError("contract must identify its XNYS calendar version")
    if contract.price_basis != PRICE_BASIS or contract.price_basis_check.price_basis != PRICE_BASIS:
        raise PriceBasisError("contract uses an unverified price basis", code="price_basis_unverified")
    if not contract.price_basis_check.provider_behavior_verified:
        raise PriceBasisError("contract has no verified provider price behavior", code="price_basis_unverified")
    _nonempty(contract.price_source, "price_source")
    _nonempty(contract.price_version, "price_version")
    _nonempty(contract.price_hash, "price_hash")
    if contract.target_spec_version != TARGET_SPEC_VERSION:
        raise ForecastContractError("unsupported target_spec_version", code="unsupported_target_spec")


def _metadata_value(metadata: object | Mapping[str, Any], name: str, *, default: Any) -> Any:
    if isinstance(metadata, Mapping):
        return metadata.get(name, default)
    return getattr(metadata, name, default)


def _normalise_corporate_action(value: object) -> CorporateAction:
    if isinstance(value, CorporateAction):
        return value
    kind = _metadata_value(value, "kind", default=None)
    effective_date = _metadata_value(value, "effective_date", default=None)
    known = _metadata_value(value, "known", default=True)
    if isinstance(effective_date, str):
        try:
            effective_date = date.fromisoformat(effective_date)
        except ValueError as exc:
            raise ForecastContractError("corporate action effective_date must use ISO date") from exc
    return CorporateAction(kind=kind, effective_date=effective_date, known=bool(known))


def _require_xnys_session(value: date, name: str) -> None:
    if not isinstance(value, date):
        raise ForecastContractError(f"{name} must be a date")
    try:
        xnys_session_close_at(value)
    except ValueError as exc:
        raise ForecastContractError(f"{name} must be an XNYS trading session") from exc


def _positive_finite(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ForecastContractError(f"{name} must be a finite positive number")
    return float(value)


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForecastContractError(f"{name} must not be empty")
    return value.strip()
