"""Isolated subprocess worker for executing AST-validated pure formulas."""
from __future__ import annotations

import ast
import json
import math
import os
import resource
import sys
from pathlib import Path
from typing import Any

import numpy as np

# Ensure project root is at the front of sys.path even when invoked under `python -I`
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT in sys.path:
    sys.path.remove(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
sys.modules.pop("app", None)

from app.agent_workflow.formula_sandbox import (  # noqa: E402
    ALLOWED_BUILTINS,
    MATH_ATTRS,
    NP_ATTRS,
    FormulaError,
    check_formula_ast,
)


class _UfuncProxy:
    """Exposes only the ufunc call and `.accumulate` for `np.maximum` / `np.minimum`."""

    __slots__ = ("_ufunc",)

    def __init__(self, ufunc: Any):
        object.__setattr__(self, "_ufunc", ufunc)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return object.__getattribute__(self, "_ufunc")(*args, **kwargs)

    @property
    def accumulate(self) -> Any:
        return object.__getattribute__(self, "_ufunc").accumulate

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"Forbidden ufunc attribute: {name}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Read-only proxy")


class _ModuleProxy:
    """Read-only proxy exposing only allowlisted attributes of `math` or `numpy`."""

    __slots__ = ("_target", "_allowed", "_overrides")

    def __init__(self, target: Any, allowed: frozenset[str], overrides: dict[str, Any] | None = None):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "_overrides", overrides or {})

    def __getattr__(self, name: str) -> Any:
        allowed = object.__getattribute__(self, "_allowed")
        if name.startswith("_") or name not in allowed:
            raise AttributeError(f"Attribute `{name}` is not allowed in formula sandbox.")
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_target"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Read-only module proxy")


SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "range": range,
    "min": min,
    "max": max,
    "abs": abs,
    "sum": sum,
    "float": float,
    "int": int,
    "round": round,
    "zip": zip,
    "enumerate": enumerate,
    "sorted": sorted,
    "True": True,
    "False": False,
    "None": None,
}

MATH_PROXY = _ModuleProxy(math, MATH_ATTRS)
NP_PROXY = _ModuleProxy(
    np,
    NP_ATTRS,
    overrides={
        "maximum": _UfuncProxy(np.maximum),
        "minimum": _UfuncProxy(np.minimum),
    },
)


def _apply_sandbox_limits() -> dict[str, bool]:
    applied: dict[str, bool] = {}
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        applied["rlimit_cpu"] = True
    except (ValueError, OSError):
        applied["rlimit_cpu"] = False

    try:
        mem_bytes = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        applied["rlimit_as"] = True
    except (ValueError, OSError):
        applied["rlimit_as"] = False

    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        applied["rlimit_fsize"] = True
    except (ValueError, OSError):
        applied["rlimit_fsize"] = False

    try:
        # Prevent opening new files
        resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
        applied["rlimit_nofile"] = True
    except (ValueError, OSError):
        applied["rlimit_nofile"] = False

    os.environ.clear()
    for mod_name in ("socket", "subprocess", "shutil", "urllib", "http", "ftplib", "telnetlib"):
        sys.modules.pop(mod_name, None)
    return applied


def main() -> int:
    raw_input = sys.stdin.buffer.read(4 * 1024 * 1024)
    limits_applied = _apply_sandbox_limits()
    try:
        payload = json.loads(raw_input.decode("utf-8"))
        code = str(payload.get("code") or "")
        bindings = payload.get("bindings") or []
        if not bindings or not isinstance(bindings, list):
            raise FormulaError("formula_shape_invalid", hint="No bindings provided.")

        first_vecs = bindings[0].get("vectors") or {}
        param_names = check_formula_ast(code, expected_params=list(first_vecs.keys()))
        tree = ast.parse(code, mode="exec")
        compiled = compile(tree, "<formula>", "exec")

        namespace: dict[str, Any] = {
            "__builtins__": SAFE_BUILTINS,
            "math": MATH_PROXY,
            "np": NP_PROXY,
        }
        exec(compiled, namespace, namespace)  # noqa: S102
        fn = namespace.get("f")
        if not callable(fn):
            raise FormulaError("formula_shape_invalid", hint="Function `f` was not defined.")

        results: list[dict[str, Any]] = []
        for item in bindings:
            label = str(item.get("label") or "")
            vectors = item.get("vectors") or {}
            if set(vectors.keys()) != set(param_names) or len(vectors) != len(param_names):
                results.append({
                    "label": label,
                    "error": "formula_params_mismatch",
                    "hint": f"Binding `{label}` keys do not match {param_names}.",
                })
                continue

            kwargs: dict[str, np.ndarray] = {}
            for p_name in param_names:
                arr = np.asarray(vectors[p_name], dtype=np.float64).copy()
                arr.flags.writeable = False
                kwargs[p_name] = arr

            try:
                with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                    out = fn(**kwargs)
                if isinstance(out, np.ndarray):
                    if out.size != 1:
                        results.append({
                            "label": label,
                            "error": "formula_non_finite",
                            "hint": "Formula must return a scalar float, not an array.",
                        })
                        continue
                    out = float(out.item())
                fval = float(out)
            except (ZeroDivisionError, OverflowError, FloatingPointError):
                results.append({
                    "label": label,
                    "error": "formula_non_finite",
                    "hint": "Division by zero or numeric overflow produced a non-finite result.",
                })
                continue
            except (TypeError, ValueError) as exc:
                msg = str(exc).lower()
                if "read-only" in msg or "writeable" in msg or "assignment destination" in msg:
                    results.append({
                        "label": label,
                        "error": "formula_runtime_error",
                        "hint": "Input vectors are read-only and cannot be mutated.",
                    })
                else:
                    results.append({
                        "label": label,
                        "error": "formula_runtime_error",
                        "hint": f"Runtime error: {type(exc).__name__}",
                    })
                continue
            except Exception as exc:
                results.append({
                    "label": label,
                    "error": "formula_runtime_error",
                    "hint": f"Runtime error: {type(exc).__name__}",
                })
                continue

            if not math.isfinite(fval):
                results.append({
                    "label": label,
                    "error": "formula_non_finite",
                    "hint": "Formula returned NaN or Inf.",
                })
                continue

            results.append({"label": label, "value": fval})

        sys.stdout.write(json.dumps({"results": results, "limits": limits_applied}, ensure_ascii=False))
        sys.stdout.flush()
        return 0
    except FormulaError as exc:
        sys.stdout.write(json.dumps({"error": exc.code, "hint": exc.hint}, ensure_ascii=False))
        sys.stdout.flush()
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"error": "formula_runtime_error", "hint": type(exc).__name__}, ensure_ascii=False))
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
