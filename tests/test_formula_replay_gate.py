"""Tests for the server-side formula replay gate, DB tamper detection, and claim gate integration."""
from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, update

from app.agent_chat_stream import _restore_citations
from app.agent_tools import build_price_set_row
from app.agent_workflow.claim_review import apply_claim_gate
from app.agent_workflow.evidence import number_rows
from app.agent_workflow.formula_replay import execute_calculate_formula, replay_formula_citations
from app.agent_workflow.formula_sandbox import formula_sha256
from app.database import Base, SessionLocal, engine
from app.models import MarketPrice


def _setup_aapl_formula_citations() -> tuple[list[dict], list[str]]:
    Base.metadata.create_all(bind=engine)
    base_date = date(2026, 10, 28)
    fetched_ts = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    rows: list[dict] = []
    row_ids: list[str] = []
    with SessionLocal() as session:
        session.execute(delete(MarketPrice).where(MarketPrice.symbol == "AAPL"))
        session.commit()
        for idx in range(25):
            d = base_date - timedelta(days=idx)
            close_val = round(200.0 + idx * 0.8 + (1.5 if idx % 2 == 0 else -1.1), 2)
            mp = MarketPrice(
                id=uuid.uuid4(),
                symbol="AAPL",
                trading_date=d,
                open=round(close_val - 0.5, 2),
                high=round(close_val + 1.0, 2),
                low=round(close_val - 1.0, 2),
                close=close_val,
                volume=8_000_000 + idx * 1000,
                source="stooq",
                fetched_at=fetched_ts,
            )
            session.add(mp)
            row_ids.append(str(mp.id))
            rows.append({
                "id": str(mp.id),
                "symbol": "AAPL",
                "trading_date": d.isoformat(),
                "open": float(mp.open),
                "high": float(mp.high),
                "low": float(mp.low),
                "close": float(mp.close),
                "volume": int(mp.volume),
                "source": mp.source,
                "fetched_at": fetched_ts.isoformat(),
            })
        session.commit()

    receipt, _ = build_price_set_row(rows, symbol="AAPL")
    citations: list[dict] = []
    number_rows("query_stock_prices", [receipt], citations)

    formula_code = (
        "def f(close):\n"
        "    r = np.diff(close) / close[:-1]\n"
        "    return float(np.std(r, ddof=1) * math.sqrt(252) * 100)\n"
    )
    execute_calculate_formula(
        {
            "formula_code": formula_code,
            "bindings": [{"label": "AAPL", "inputs": {"close": {"source_ordinal": 1, "field": "close"}}}],
            "decimals": 2,
            "unit": "percent",
        },
        citations,
    )
    return citations, row_ids


def test_case6_parent_row_modified_triggers_formula_parent_changed_and_drops_claim():
    citations, row_ids = _setup_aapl_formula_citations()
    val = citations[1]["calculation"]["value"]

    # Tamper one row in market_prices after calculate_formula ran
    target_id = uuid.UUID(row_ids[5])
    with SessionLocal() as session:
        session.execute(update(MarketPrice).where(MarketPrice.id == target_id).values(close=999.99))
        session.commit()

    citations_for_restore = copy.deepcopy(citations)
    failed, audit = replay_formula_citations(citations)
    assert len(failed) == 1
    assert failed[0] == {"ordinal": 2, "reason": "formula_parent_changed"}
    assert audit == {"checked": 1, "failed": [{"ordinal": 2, "reason": "formula_parent_changed"}]}
    assert citations[1]["source_type"] == "unavailable"
    assert citations[1]["title"] == "来源已失效"

    answer = (
        f"AAPL 年化波动率为 {val}% [2]。\n\n"
        f'{{"claims":[{{"id":"c1","statement":"AAPL 年化波动率为 {val}%","citation_ordinal":2,'
        f'"evidence":{{"type":"field","fields":{{"value":"{val}"}}}}}}]}}'
    )
    gate = apply_claim_gate(answer, citations, mode="enforce")
    assert len(gate.dropped_lines) == 1

    restored = _restore_citations(citations_for_restore)
    assert restored[0]["source_type"] == "unavailable"
    assert restored[0]["title"] == "来源已失效"
    assert restored[1]["source_type"] == "unavailable"
    assert restored[1]["title"] == "来源已失效"


def test_case6_parent_row_deleted_triggers_formula_parent_missing():
    citations, row_ids = _setup_aapl_formula_citations()

    target_id = uuid.UUID(row_ids[0])
    with SessionLocal() as session:
        session.execute(delete(MarketPrice).where(MarketPrice.id == target_id))
        session.commit()

    failed, _ = replay_formula_citations(citations)
    assert failed == [{"ordinal": 2, "reason": "formula_parent_missing"}]
    assert citations[1]["source_type"] == "unavailable"


def test_case7_tampering_formula_code_or_hash_detected_on_replay():
    citations, _ = _setup_aapl_formula_citations()

    # Case 7a: tamper formula_code without updating formula_sha256 -> formula_changed
    cits_a = copy.deepcopy(citations)
    cits_a[1]["calculation"]["formula_code"] = "def f(close):\n    return 12.34\n"
    failed_a, _ = replay_formula_citations(cits_a)
    assert failed_a == [{"ordinal": 2, "reason": "formula_changed"}]
    assert cits_a[1]["source_type"] == "unavailable"

    # Case 7b: tamper formula_code AND update formula_sha256 -> formula_replay_mismatch
    cits_b = copy.deepcopy(citations)
    tampered_code = "def f(close):\n    return float(np.mean(close) * 0.5)\n"
    cits_b[1]["calculation"]["formula_code"] = tampered_code
    cits_b[1]["calculation"]["formula_sha256"] = formula_sha256(tampered_code)
    failed_b, _ = replay_formula_citations(cits_b)
    assert failed_b == [{"ordinal": 2, "reason": "formula_replay_mismatch"}]
    assert cits_b[1]["source_type"] == "unavailable"


def test_case8_claim_value_mismatch_dropped_by_claim_gate():
    citations, _ = _setup_aapl_formula_citations()

    # Replay passes because DB and formula_calculation citation are untouched
    failed, _ = replay_formula_citations(citations)
    assert failed == []
    assert citations[1]["source_type"] == "formula_calculation"

    # Model fabricates 35.00% instead of the real calculated value
    wrong_answer = (
        "AAPL 年化波动率为 35.00% [2]。\n\n"
        '{"claims":[{"id":"c1","statement":"AAPL 年化波动率为 35.00%","citation_ordinal":2,'
        '"evidence":{"type":"field","fields":{"value":"35.00"}}}]}'
    )
    gate = apply_claim_gate(wrong_answer, citations, mode="enforce")
    assert any(issue["reason"] == "field_mismatch" for issue in gate.issues)
    assert len(gate.dropped_lines) == 1
