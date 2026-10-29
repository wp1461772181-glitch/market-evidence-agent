"""Server-side execution and deterministic replay gate for `calculate_formula` citations."""
from __future__ import annotations

from typing import Any

import numpy as np

from ..agent_tools import PRICE_SET_FIELDS, load_vectors
from .formula_sandbox import (
    FormulaError,
    check_formula_ast,
    format_formula_value,
    formula_sha256,
    run_formula_in_sandbox,
    runtime_engine_info,
)


def execute_calculate_formula(args: dict[str, Any], citations: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate, load parent vectors from `market_prices`, run in sandbox, and append `formula_calculation` citations."""
    formula_code = str(args.get("formula_code") or "")
    bindings = args.get("bindings") or []
    decimals = int(args.get("decimals", 4))
    unit = str(args.get("unit") or "ratio")

    if not isinstance(bindings, list) or not (1 <= len(bindings) <= 20):
        raise FormulaError("formula_shape_invalid", hint="bindings must contain 1..20 items.")
    if type(decimals) is not int or not (0 <= decimals <= 8):
        raise FormulaError("formula_shape_invalid", hint="decimals must be an integer in 0..8.")

    param_names = check_formula_ast(formula_code)
    indexed = {c.get("ordinal"): c for c in (citations or []) if isinstance(c, dict) and type(c.get("ordinal")) is int}

    worker_bindings: list[dict[str, Any]] = []
    binding_meta: list[dict[str, Any]] = []

    for binding in bindings:
        label = str(binding.get("label") or "")
        inputs = binding.get("inputs") or {}
        if not isinstance(inputs, dict) or not (1 <= len(inputs) <= 6):
            raise FormulaError("formula_params_mismatch", hint=f"Binding `{label}` must provide 1..6 input vectors.")
        if set(inputs.keys()) != set(param_names) or len(inputs) != len(param_names):
            raise FormulaError(
                "formula_params_mismatch",
                hint=f"Function parameters {param_names} do not match binding `{label}` inputs {sorted(inputs.keys())}.",
            )

        vectors: dict[str, list[float]] = {}
        bound_inputs: dict[str, dict[str, Any]] = {}
        parent_row_ids: dict[str, list[str]] = {}
        parent_digests: dict[str, str] = {}
        parents_set: set[int] = set()
        reference_dates: list[str] | None = None
        primary_set_id: str = ""
        vector_len: int = 0

        for p_name in param_names:
            vec_spec = inputs[p_name]
            ord_num = vec_spec.get("source_ordinal")
            field = str(vec_spec.get("field") or "")
            source = indexed.get(ord_num)
            if not source or source.get("source_type") != "saved_price_set":
                raise FormulaError(
                    "formula_input_unbound",
                    hint=f"Citation [{ord_num}] is not a `saved_price_set`.",
                )
            avail = source.get("fields_available") or list(PRICE_SET_FIELDS)
            if field not in avail or field not in PRICE_SET_FIELDS:
                raise FormulaError(
                    "formula_input_unbound",
                    hint=f"Field `{field}` is not available on citation [{ord_num}].",
                )
            row_ids = source.get("row_ids") or []
            expected_digest = source.get("parent_digest")
            values, trading_dates, actual_digest = load_vectors(
                row_ids,
                field,
                expected_digest=expected_digest,
            )
            if reference_dates is None:
                reference_dates = trading_dates
                primary_set_id = str(source.get("set_id") or "")
                vector_len = len(values)
            elif trading_dates != reference_dates:
                raise FormulaError(
                    "formula_vectors_misaligned",
                    hint=f"Input vectors for `{label}` do not share identical trading_date series.",
                )

            vectors[p_name] = values
            bound_inputs[p_name] = {
                "source_ordinal": int(ord_num),
                "field": field,
                "set_id": str(source.get("set_id") or ""),
            }
            parent_row_ids[p_name] = list(row_ids)
            parent_digests[p_name] = actual_digest
            parents_set.add(int(ord_num))

        worker_bindings.append({"label": label, "vectors": vectors})
        binding_meta.append({
            "label": label,
            "n": vector_len,
            "set_id": primary_set_id,
            "inputs": bound_inputs,
            "parents": sorted(parents_set),
            "parent_row_ids": parent_row_ids,
            "parent_digest": parent_digests,
        })

    sandbox_results = run_formula_in_sandbox(formula_code, worker_bindings)
    code_hash = formula_sha256(formula_code)
    engine_meta = runtime_engine_info()

    receipt_data: list[dict[str, Any]] = []
    for meta, res in zip(binding_meta, sandbox_results):
        raw_val = float(res["value"])
        formatted_val = format_formula_value(raw_val, decimals)
        ordinal = len(citations) + 1
        calc_payload = {
            "value": formatted_val,
            "raw": raw_val,
            "decimals": decimals,
            "unit": unit,
            "label": meta["label"],
            "formula_code": formula_code,
            "formula_sha256": code_hash,
            "inputs": meta["inputs"],
            "parents": meta["parents"],
            "parent_row_ids": meta["parent_row_ids"],
            "parent_digest": meta["parent_digest"],
            "engine": engine_meta,
        }
        citations.append({
            "ordinal": ordinal,
            "source_type": "formula_calculation",
            "derived": True,
            "title": "可复核公式计算",
            "quote": formatted_val,
            "parents": meta["parents"],
            "calculation": calc_payload,
            "fields": {
                "label": meta["label"],
                "value": formatted_val,
                "unit": unit,
                "n": meta["n"],
                "set_id": meta["set_id"],
            },
        })
        receipt_data.append({
            "label": meta["label"],
            "value": formatted_val,
            "citation": f"[{ordinal}]",
            "n": meta["n"],
            "set_id": meta["set_id"],
        })

    return {
        "ok": True,
        "data": receipt_data,
        "formula_sha256": code_hash,
        "engine": f"numpy {np.__version__}",
    }


def verify_single_formula_citation(
    item: dict[str, Any],
    citations: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    """Re-verify one `formula_calculation` citation against `market_prices` and the sandbox (§6)."""
    calc = item.get("calculation")
    if not isinstance(calc, dict):
        return False, "formula_changed"

    code = calc.get("formula_code")
    recorded_sha = calc.get("formula_sha256")
    if not isinstance(code, str) or not code.strip() or formula_sha256(code) != recorded_sha:
        return False, "formula_changed"

    inputs = calc.get("inputs")
    parent_row_ids = calc.get("parent_row_ids")
    parent_digests = calc.get("parent_digest")
    parents = calc.get("parents") or item.get("parents") or []

    if (
        not isinstance(inputs, dict)
        or not inputs
        or not isinstance(parent_row_ids, dict)
        or not isinstance(parent_digests, dict)
        or not isinstance(parents, list)
        or not parents
    ):
        return False, "formula_parent_missing"

    indexed = {
        c.get("ordinal"): c
        for c in (citations or [])
        if isinstance(c, dict) and type(c.get("ordinal")) is int
    }

    for p_ord in parents:
        parent_cit = indexed.get(p_ord)
        if parent_cit is not None and parent_cit.get("source_type") != "saved_price_set":
            return False, "formula_parent_missing"

    try:
        param_names = check_formula_ast(code, expected_params=list(inputs.keys()))
    except FormulaError as exc:
        return False, exc.code

    vectors: dict[str, list[float]] = {}
    reference_dates: list[str] | None = None

    for p_name in param_names:
        spec = inputs.get(p_name)
        if not isinstance(spec, dict):
            return False, "formula_parent_missing"
        ord_num = spec.get("source_ordinal")
        field = str(spec.get("field") or "")
        parent_cit = indexed.get(ord_num)
        if parent_cit is not None and parent_cit.get("source_type") != "saved_price_set":
            return False, "formula_parent_missing"

        row_ids = parent_row_ids.get(p_name)
        expected_digest = parent_digests.get(p_name)
        if not isinstance(row_ids, list) or not row_ids or not isinstance(expected_digest, str):
            return False, "formula_parent_missing"
        if parent_cit is not None:
            if parent_cit.get("row_ids") and list(parent_cit.get("row_ids")) != list(row_ids):
                return False, "formula_parent_changed"
            if parent_cit.get("parent_digest") and parent_cit.get("parent_digest") != expected_digest:
                return False, "formula_parent_changed"

        try:
            values, trading_dates, _ = load_vectors(
                row_ids,
                field,
                expected_digest=expected_digest,
            )
        except FormulaError as exc:
            return False, exc.code

        if reference_dates is None:
            reference_dates = trading_dates
        elif trading_dates != reference_dates:
            return False, "formula_vectors_misaligned"

        vectors[p_name] = values

    label = str(calc.get("label") or "replay")
    try:
        results = run_formula_in_sandbox(code, [{"label": label, "vectors": vectors}])
    except FormulaError as exc:
        return False, exc.code

    replay_raw = float(results[0]["value"])
    recorded_raw = calc.get("raw")
    recorded_value = str(calc.get("value") or "")
    decimals = calc.get("decimals", 4)
    if type(decimals) is not int or not (0 <= decimals <= 8):
        return False, "formula_replay_mismatch"

    try:
        raw_float = float(recorded_raw)
    except (TypeError, ValueError):
        return False, "formula_replay_mismatch"

    tol = max(1e-9, 10 ** (-decimals - 2))
    if abs(replay_raw - raw_float) > tol:
        return False, "formula_replay_mismatch"

    replayed_str = format_formula_value(replay_raw, decimals)
    if replayed_str != recorded_value and f"{round(replay_raw, decimals):.{decimals}f}" != recorded_value:
        return False, "formula_replay_mismatch"

    if item.get("quote") is not None and str(item.get("quote") or "") != recorded_value:
        return False, "formula_replay_mismatch"

    return True, None


def replay_formula_citations(citations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replay all `formula_calculation` citations in-place, replacing failures with `source_type='unavailable'`."""
    checked = 0
    failed: list[dict[str, Any]] = []
    for idx, item in enumerate(list(citations or [])):
        if not isinstance(item, dict) or item.get("source_type") != "formula_calculation":
            continue
        checked += 1
        ordinal = item.get("ordinal", idx + 1)
        ok, reason = verify_single_formula_citation(item, citations)
        if not ok:
            err_reason = reason or "formula_replay_mismatch"
            failed.append({"ordinal": ordinal, "reason": err_reason})
            citations[idx] = {
                "ordinal": ordinal,
                "chunk_id": None,
                "document_id": None,
                "title": "来源已失效",
                "section": "",
                "source_url": None,
                "source_type": "unavailable",
                "unavailable": True,
                "truncated": False,
                "replay_error": err_reason,
            }
    return failed, {"checked": checked, "failed": failed}
