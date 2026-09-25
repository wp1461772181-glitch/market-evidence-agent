import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from app.training_data_v2 import (
    HISTORY_SESSIONS,
    TRAINING_END,
    TRAINING_START,
    _xnys_sessions,
    load_v2_market_training_data,
)


def _base_manifest():
    return {
        "schema_version": "v2-corpus-manifest-v1",
        "mode": "historical_research",
        "symbols": ["AAPL"],
        "training_range": {"start": TRAINING_START.isoformat(), "end": TRAINING_END.isoformat()},
        "target_spec_version": "absolute-close-v1",
        "price_basis": "provider_quote_close_v1",
        "partitions": {
            "train": ["2021-01-01", "2023-12-31"],
            "calibration": ["2024-01-01", "2024-12-31"],
            "test": ["2025-01-01", "2026-08-31"],
        },
        "price_snapshot_contract": {
            "schema_version": "v2-price-snapshot-v1",
            "mode": "historical_research",
            "symbols": ["AAPL", "SPY"],
            "source": "yahoo-finance-chart",
            "price_basis": "provider_quote_close_v1",
            "close_field": "indicators.quote.close",
            "cash_dividend_reinvestment_included": False,
            "start_date": TRAINING_START.isoformat(),
            "end_date": TRAINING_END.isoformat(),
        },
        "price_snapshot_files": [],
    }


def _snapshot(symbol, close_by_date, actions=()):
    rows = []
    for session in _xnys_sessions(TRAINING_START, TRAINING_END):
        close = close_by_date.get(session, 100.0)
        rows.append(
            {
                "trading_date": session.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
            }
        )
    return {
        "schema_version": "v2-price-snapshot-v1",
        "mode": "historical_research",
        "symbol": symbol,
        "source": "yahoo-finance-chart",
        "requested_range": {"start_date": TRAINING_START.isoformat(), "end_date": TRAINING_END.isoformat()},
        "observed_at": datetime(2026, 9, 25, 1, tzinfo=UTC).isoformat(),
        "price_basis": {
            "basis": "provider_quote_close_v1",
            "provider_behavior_verified": True,
            "adjusted_close_present": True,
            "corporate_actions_available": True,
            "corporate_actions_response_shape": "events_object",
            "corporate_actions": list(actions),
            "verification_notes": ["fixture quote-close"],
            "price_return_only": True,
            "cash_dividend_reinvestment_included": False,
        },
        "coverage_complete": True,
        "missing_sessions": [],
        "row_count": len(rows),
        "prices": rows,
    }


def _write_fixture_dataset(tmp_path, *, closes=None, actions=()):
    closes = closes or {}
    manifest = _base_manifest()
    for symbol in ("AAPL", "SPY"):
        snapshot = _snapshot(symbol, closes if symbol == "AAPL" else {}, actions if symbol == "AAPL" else ())
        filename = f"prices-{symbol}.json"
        raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        (tmp_path / filename).write_bytes(raw)
        manifest["price_snapshot_files"].append(
            {
                "symbol": symbol,
                "filename": filename,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "source": "yahoo-finance-chart",
                "mode": "historical_research",
                "start_date": TRAINING_START.isoformat(),
                "end_date": TRAINING_END.isoformat(),
                "observed_at": snapshot["observed_at"],
                "row_count": snapshot["row_count"],
                "coverage_complete": True,
                "provider_behavior_verified": True,
                "price_basis": "provider_quote_close_v1",
            }
        )
    manifest_path = tmp_path / "dataset-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_builds_fixed_twenty_session_roots_and_keeps_plus_minus_two_percent_neutral(tmp_path):
    sessions = _xnys_sessions(TRAINING_START, TRAINING_END)
    anchors = (sessions[100], sessions[150], sessions[200])
    closes = {
        sessions[120]: 102.0,  # exact +2% remains neutral by contract
        sessions[170]: 102.01,
        sessions[220]: 97.99,
    }
    manifest_path = _write_fixture_dataset(tmp_path, closes=closes)
    dataset = load_v2_market_training_data(manifest_path, symbols=("AAPL",))
    rows = {row.anchor_date: row for row in dataset.rows}

    assert rows[anchors[0]].target_end_date == sessions[120]
    assert rows[anchors[0]].label == "neutral"
    assert rows[anchors[1]].label == "bullish"
    assert rows[anchors[2]].label == "bearish"
    assert rows[anchors[0]].remaining_sessions == 20
    assert rows[anchors[0]].realized_return_from_anchor == 0.0
    assert dataset.audit["history_sessions_required"] == HISTORY_SESSIONS
    assert dataset.audit["target_sessions_required"] == 20
    assert dataset.audit["rows_by_partition"]["train"] > 0
    assert dataset.audit["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def test_purges_cross_partition_labels_and_split_crossing_roots(tmp_path):
    sessions = _xnys_sessions(TRAINING_START, TRAINING_END)
    split_anchor = sessions[300]
    split_date = sessions[310]
    crossing_anchor = next(item for item in sessions if item == date(2023, 12, 15))
    manifest_path = _write_fixture_dataset(
        tmp_path,
        actions=(
            {
                "kind": "split",
                "effective_date": split_date.isoformat(),
                "known": True,
                "numerator": 4,
                "denominator": 1,
            },
        ),
    )

    dataset = load_v2_market_training_data(manifest_path, symbols=("AAPL",))
    anchors = {row.anchor_date for row in dataset.rows}

    assert split_anchor not in anchors
    assert crossing_anchor not in anchors
    assert dataset.audit["excluded_by_reason"]["corporate_action_unsupported"] > 0
    assert dataset.audit["excluded_by_reason"]["cross_partition_purge"] > 0


def test_unknown_corporate_action_blocks_the_affected_price_interval(tmp_path):
    sessions = _xnys_sessions(TRAINING_START, TRAINING_END)
    anchor = sessions[400]
    unknown_action_date = sessions[410]
    manifest_path = _write_fixture_dataset(
        tmp_path,
        actions=(
            {
                "kind": "unknown",
                "effective_date": unknown_action_date.isoformat(),
                "known": False,
            },
        ),
    )

    dataset = load_v2_market_training_data(manifest_path, symbols=("AAPL",))

    assert anchor not in {row.anchor_date for row in dataset.rows}
    assert dataset.audit["excluded_by_reason"]["price_basis_unverified"] > 0


def test_rejects_a_snapshot_when_its_manifest_hash_no_longer_matches(tmp_path):
    manifest_path = _write_fixture_dataset(tmp_path)
    (tmp_path / "prices-AAPL.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        load_v2_market_training_data(manifest_path, symbols=("AAPL",))
