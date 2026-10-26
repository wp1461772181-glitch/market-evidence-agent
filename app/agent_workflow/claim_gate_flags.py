"""Feature flags for the claim quote gate and the claim-support Jev check.

Every mode defaults to off. Claim-support attempts and timeout are capped so
this path cannot inherit the direction provider's 3×30s budget.
"""
from __future__ import annotations

import os

_MODES = {"off", "shadow", "enforce"}


def _mode(name: str, default: str = "off") -> str:
    value = (os.environ.get(name) or default).strip().lower()
    return value if value in _MODES else default


def _int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def claim_quote_gate_mode() -> str:
    return _mode("CLAIM_QUOTE_GATE")


def claim_jev_support_mode() -> str:
    return _mode("CLAIM_JEV_SUPPORT")


def claim_quote_min_chars() -> int:
    return _int("CLAIM_QUOTE_MIN_CHARS", 24, minimum=1)


def claim_jev_timeout_seconds() -> int:
    """Single-attempt timeout. Values above 5 seconds are clamped."""
    return _int("CLAIM_JEV_TIMEOUT_SECONDS", 5, minimum=1, maximum=5)


def claim_jev_max_attempts() -> int:
    """Hard ceiling of one HTTP attempt, whatever the environment says."""
    return 1


def claim_jev_max_per_turn() -> int:
    return _int("CLAIM_JEV_MAX_PER_TURN", 4, minimum=0)


def claim_jev_support_threshold() -> float:
    raw = os.environ.get("CLAIM_JEV_SUPPORT_THRESHOLD")
    if raw is None or not str(raw).strip():
        return 0.7
    try:
        value = float(str(raw).strip())
    except ValueError:
        return 0.7
    if not 0.0 <= value <= 1.0:
        return 0.7
    return value


def claim_jev_daily_cap() -> int | None:
    """Server-wide daily max. Unset means the cap is not applied."""
    raw = os.environ.get("CLAIM_JEV_DAILY_CAP")
    if raw is None or not str(raw).strip():
        return None
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return None


def claim_jev_daily_path() -> str:
    return os.environ.get("CLAIM_JEV_DAILY_PATH") or "artifacts/claim-jev-daily.json"


def legacy_prose_guards_enabled() -> bool:
    value = (os.environ.get("LEGACY_PROSE_GUARDS") or "on").strip().lower()
    return value not in {"off", "0", "false", "no"}
