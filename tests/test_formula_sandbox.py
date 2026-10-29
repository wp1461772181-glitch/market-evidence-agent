"""Unit tests for the two-layer formula sandbox (AST whitelist + isolated worker subprocess)."""
from __future__ import annotations

import math
import statistics
import time

import numpy as np
import pytest

from app.agent_workflow.formula_sandbox import (
    FormulaError,
    check_formula_ast,
    run_formula_in_sandbox,
)


def _sample_close_series(n: int = 127) -> list[float]:
    return [round(180.0 + 12.0 * math.sin(i * 0.17) + 0.35 * i, 4) for i in range(n)]


def test_case1_annualized_volatility_matches_statistics_stdev():
    close = _sample_close_series(127)
    code = (
        "def f(close):\n"
        "    r = np.diff(close) / close[:-1]\n"
        "    return float(np.std(r, ddof=1) * math.sqrt(252) * 100)\n"
    )
    results = run_formula_in_sandbox(code, [{"label": "AAPL", "vectors": {"close": close}}])
    assert len(results) == 1
    rets = [(close[i + 1] - close[i]) / close[i] for i in range(len(close) - 1)]
    expected = statistics.stdev(rets) * math.sqrt(252) * 100.0
    assert abs(results[0]["value"] - expected) < 1e-9


def test_case2_sharpe_ratio_with_custom_risk_free_rate():
    close = _sample_close_series(127)
    code = (
        "def f(close):\n"
        "    r = np.diff(close) / close[:-1]\n"
        "    rf_daily = 0.045 / 252\n"
        "    excess = r - rf_daily\n"
        "    return float(np.mean(excess) / np.std(excess, ddof=1) * math.sqrt(252))\n"
    )
    results = run_formula_in_sandbox(code, [{"label": "AAPL", "vectors": {"close": close}}])
    arr = np.asarray(close, dtype=np.float64)
    r = np.diff(arr) / arr[:-1]
    excess = r - (0.045 / 252.0)
    expected = float(np.mean(excess) / np.std(excess, ddof=1) * math.sqrt(252.0))
    assert abs(results[0]["value"] - expected) < 1e-9


def test_case3_max_drawdown_with_np_maximum_accumulate():
    close = [100.0, 110.0, 105.0, 120.0, 90.0, 95.0, 130.0, 117.0, 125.0, 128.0, 132.0]
    code = (
        "def f(close):\n"
        "    peak = np.maximum.accumulate(close)\n"
        "    dd = (close - peak) / peak\n"
        "    return float(np.min(dd) * 100)\n"
    )
    results = run_formula_in_sandbox(code, [{"label": "AAPL", "vectors": {"close": close}}])
    # Peak 120 -> trough 90 is -25.0%
    assert abs(results[0]["value"] - (-25.0)) < 1e-9


@pytest.mark.parametrize(
    "bad_code,expected_code",
    [
        ("import os\ndef f(close):\n    return 1.0\n", "formula_forbidden_node"),
        ("def f(close):\n    import os\n    return 1.0\n", "formula_forbidden_node"),
        ("def f(close):\n    open('/etc/passwd')\n    return 1.0\n", "formula_forbidden_name"),
        ("def f(close):\n    np.load('x')\n    return 1.0\n", "formula_forbidden_call"),
        ("def f(close):\n    return float(close.__class__)\n", "formula_forbidden_name"),
        ("def f(close):\n    while True:\n        pass\n    return 1.0\n", "formula_forbidden_node"),
    ],
)
def test_case5_malicious_ast_rejected_before_execution(bad_code: str, expected_code: str):
    with pytest.raises(FormulaError) as exc_info:
        check_formula_ast(bad_code, expected_params=["close"])
    assert exc_info.value.code == expected_code


def test_case5_param_name_mismatch_rejected():
    code = "def f(price):\n    return float(np.mean(price))\n"
    with pytest.raises(FormulaError) as exc_info:
        run_formula_in_sandbox(code, [{"label": "AAPL", "vectors": {"close": [100.0, 101.0]}}])
    assert exc_info.value.code == "formula_params_mismatch"


def test_case5_cpu_spin_loop_killed_under_4_seconds():
    code = (
        "def f(close):\n"
        "    acc = 0.0\n"
        "    for i in range(10**12):\n"
        "        acc += 1.0\n"
        "    return float(acc)\n"
    )
    t0 = time.monotonic()
    with pytest.raises(FormulaError) as exc_info:
        run_formula_in_sandbox(code, [{"label": "AAPL", "vectors": {"close": [100.0, 101.0]}}])
    elapsed = time.monotonic() - t0
    assert exc_info.value.code == "formula_timeout"
    assert elapsed < 4.0


@pytest.mark.parametrize(
    "non_finite_code",
    [
        "def f(close):\n    return float('nan')\n",
        "def f(close):\n    return float(math.nan)\n",
        "def f(close):\n    return float(np.std(close[:1], ddof=1))\n",
    ],
)
def test_case5_non_finite_return_rejected(non_finite_code: str):
    with pytest.raises(FormulaError) as exc_info:
        run_formula_in_sandbox(non_finite_code, [{"label": "AAPL", "vectors": {"close": [100.0, 100.0]}}])
    assert exc_info.value.code in {"formula_non_finite", "formula_runtime_error"}
