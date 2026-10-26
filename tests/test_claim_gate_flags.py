import os

from app.agent_workflow.claim_gate_flags import (
    claim_jev_daily_cap,
    claim_jev_max_attempts,
    claim_jev_max_per_turn,
    claim_jev_support_mode,
    claim_jev_support_threshold,
    claim_jev_timeout_seconds,
    claim_quote_gate_mode,
    claim_quote_min_chars,
    legacy_prose_guards_enabled,
)


def test_claim_gate_flags_default_off(monkeypatch):
    for name in (
        "CLAIM_QUOTE_GATE",
        "CLAIM_JEV_SUPPORT",
        "CLAIM_QUOTE_MIN_CHARS",
        "CLAIM_JEV_TIMEOUT_SECONDS",
        "CLAIM_JEV_MAX_ATTEMPTS",
        "CLAIM_JEV_MAX_PER_TURN",
        "CLAIM_JEV_SUPPORT_THRESHOLD",
        "CLAIM_JEV_DAILY_CAP",
        "LEGACY_PROSE_GUARDS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert claim_quote_gate_mode() == "off"
    assert claim_jev_support_mode() == "off"
    assert claim_quote_min_chars() == 24
    assert claim_jev_timeout_seconds() == 5
    assert claim_jev_max_attempts() == 1
    assert claim_jev_max_per_turn() == 4
    assert claim_jev_support_threshold() == 0.7
    assert claim_jev_daily_cap() is None
    assert legacy_prose_guards_enabled() is True


def test_unknown_mode_stays_off(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "sometimes")
    monkeypatch.setenv("CLAIM_JEV_SUPPORT", "ENFORCE")
    assert claim_quote_gate_mode() == "off"
    assert claim_jev_support_mode() == "enforce"


def test_claim_support_attempts_are_hard_capped_at_one(monkeypatch):
    monkeypatch.setenv("CLAIM_JEV_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("CLAIM_JEV_TIMEOUT_SECONDS", "30")
    assert claim_jev_max_attempts() == 1
    assert claim_jev_timeout_seconds() == 5


def test_invalid_numbers_fall_back(monkeypatch):
    monkeypatch.setenv("CLAIM_QUOTE_MIN_CHARS", "nope")
    monkeypatch.setenv("CLAIM_JEV_DAILY_CAP", "")
    monkeypatch.setenv("LEGACY_PROSE_GUARDS", "off")
    assert claim_quote_min_chars() == 24
    assert claim_jev_daily_cap() is None
    assert legacy_prose_guards_enabled() is False
    monkeypatch.setenv("CLAIM_JEV_DAILY_CAP", "12")
    assert claim_jev_daily_cap() == 12
