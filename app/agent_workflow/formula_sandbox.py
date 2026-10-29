"""Restricted AST validator and isolated subprocess runner for pure formula calculations."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import platform
import subprocess
import sys
from typing import Any

import numpy as np


class FormulaError(ValueError):
    """Structured error raised when formula validation or execution fails."""

    def __init__(self, code: str, *, hint: str = ""):
        super().__init__(code)
        self.code = code
        self.hint = hint


ALLOWED_BUILTINS = frozenset({
    "len", "range", "min", "max", "abs", "sum",
    "float", "int", "round", "zip", "enumerate", "sorted",
})

FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "input", "print", "breakpoint", "help",
    "type", "object", "super", "memoryview", "os", "sys", "subprocess", "socket",
    "builtins", "importlib", "pathlib", "shutil", "ctypes", "pickle", "marshal", "io",
})

MATH_FUNCS = frozenset({
    "sqrt", "log", "log10", "log1p", "exp", "expm1", "fabs", "pow",
    "isfinite", "floor", "ceil",
})
MATH_CONSTS = frozenset({"pi", "e", "nan", "inf"})
MATH_ATTRS = MATH_FUNCS | MATH_CONSTS

NP_FUNCS = frozenset({
    "array", "asarray", "diff", "log", "log1p", "exp", "sqrt", "abs",
    "mean", "std", "var", "sum", "prod", "cumsum", "cumprod",
    "min", "max", "minimum", "maximum", "argmax", "argmin",
    "median", "percentile", "quantile", "where", "clip", "isfinite",
    "nanmean", "nanstd", "nansum", "nanmax", "nanmin", "corrcoef", "cov", "dot", "sign",
})
NP_CONSTS = frozenset({"pi", "e", "nan", "inf", "float64"})
NP_ATTRS = NP_FUNCS | NP_CONSTS

NDARRAY_METHODS = frozenset({
    "mean", "std", "var", "sum", "min", "max",
    "cumsum", "cumprod", "prod", "argmax", "argmin",
})
NDARRAY_PROPS = frozenset({"size", "shape", "ndim"})
NDARRAY_ATTRS = NDARRAY_METHODS | NDARRAY_PROPS

ALLOWED_OPERATORS = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

ALLOWED_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg,
    ast.Return, ast.Assign, ast.AugAssign, ast.Expr, ast.If, ast.For, ast.Pass,
    ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.keyword, ast.Attribute, ast.Subscript, ast.Slice,
    ast.Tuple, ast.List, ast.ListComp, ast.GeneratorExp, ast.comprehension,
    *ALLOWED_OPERATORS,
)


def formula_sha256(code: str) -> str:
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def format_formula_value(value: float, decimals: int = 4) -> str:
    if not math.isfinite(value):
        raise FormulaError("formula_non_finite", hint="Formula returned non-finite float (NaN or Inf).")
    rounded = round(float(value), decimals)
    if rounded == 0.0:
        rounded = 0.0
    if decimals <= 0:
        return str(int(round(rounded)))
    # Preserve standard Python round string when non-trailing-zero or exact decimal representation
    # Wait: both 28.4137 (decimals=4) and 28.41 (decimals=2) have exact digits with str(round(...)) unless trailing zero;
    # using str(round(rounded, decimals)) matches `round(replay, decimals)` string in §6.4!
    return str(rounded)


def _is_np_accumulate(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "accumulate"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr in {"maximum", "minimum"}
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "np"
    )


def check_formula_ast(
    formula_code: str,
    expected_params: list[str] | set[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Validate `formula_code` against the §4 AST whitelist and return positional parameter names."""
    if not isinstance(formula_code, str) or not (1 <= len(formula_code.strip()) <= 2000):
        raise FormulaError("formula_shape_invalid", hint="formula_code must be a non-empty string up to 2000 chars.")
    try:
        tree = ast.parse(formula_code, mode="exec")
    except SyntaxError as exc:
        raise FormulaError("formula_shape_invalid", hint=f"Syntax error: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise FormulaError(
                "formula_forbidden_node",
                hint="Import statements are not allowed; math and np are pre-injected.",
            )

    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise FormulaError(
            "formula_shape_invalid",
            hint="Formula must consist of a single top-level `def f(...):` function.",
        )

    fn = tree.body[0]
    if fn.name != "f" or fn.decorator_list:
        raise FormulaError(
            "formula_shape_invalid",
            hint="Top-level function must be named `f` without decorators.",
        )

    args = fn.args
    if (
        args.posonlyargs
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
        or args.defaults
        or any(d is not None for d in args.kw_defaults)
    ):
        raise FormulaError(
            "formula_shape_invalid",
            hint="Function `f` may only use plain positional parameters without default values or *args/**kwargs.",
        )

    if not (1 <= len(args.args) <= 6):
        raise FormulaError(
            "formula_shape_invalid",
            hint="Function `f` must accept between 1 and 6 positional parameters.",
        )

    param_names: list[str] = []
    for arg in args.args:
        name = arg.arg
        if name.startswith("_") or name in FORBIDDEN_NAMES or name in ALLOWED_BUILTINS or name in {"math", "np", "f"}:
            raise FormulaError("formula_forbidden_name", hint=f"Parameter name `{name}` is not allowed.")
        if arg.annotation is not None:
            raise FormulaError("formula_shape_invalid", hint="Parameter type annotations are not allowed.")
        if name in param_names:
            raise FormulaError("formula_shape_invalid", hint=f"Duplicate parameter `{name}`.")
        param_names.append(name)

    if fn.returns is not None:
        if not (isinstance(fn.returns, ast.Name) and fn.returns.id == "float"):
            raise FormulaError("formula_shape_invalid", hint="Return annotation may only be `float` or omitted.")

    if expected_params is not None:
        expected_set = set(expected_params)
        if set(param_names) != expected_set or len(param_names) != len(expected_set):
            raise FormulaError(
                "formula_params_mismatch",
                hint=f"Function parameters {param_names} must match binding inputs {sorted(expected_set)}.",
            )

    allowed_vars: set[str] = set(param_names)

    def _register_target(target: ast.AST, *, allow_param_overwrite: bool = False) -> None:
        if isinstance(target, ast.Name):
            name = target.id
            if name.startswith("_") or name in FORBIDDEN_NAMES or name in ALLOWED_BUILTINS or name in {"math", "np", "f"}:
                raise FormulaError("formula_forbidden_name", hint=f"Cannot assign to reserved name `{name}`.")
            if not allow_param_overwrite and name in param_names:
                raise FormulaError("formula_forbidden_name", hint=f"Input parameter `{name}` is read-only.")
            allowed_vars.add(name)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                _register_target(elt, allow_param_overwrite=allow_param_overwrite)
        else:
            raise FormulaError("formula_forbidden_node", hint="Only simple variable or tuple unpacking assignment is allowed.")

    def _visit(node: ast.AST, *, in_float_call: bool = False) -> None:
        if not isinstance(node, ALLOWED_NODES):
            raise FormulaError("formula_forbidden_node", hint=f"Forbidden AST node: `{type(node).__name__}`.")

        if isinstance(node, ast.FunctionDef) and node is not fn:
            raise FormulaError("formula_forbidden_node", hint="Nested function definitions are not allowed.")

        if isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id in FORBIDDEN_NAMES:
                raise FormulaError("formula_forbidden_name", hint=f"Forbidden identifier: `{node.id}`.")
            if isinstance(node.ctx, ast.Load):
                if (
                    node.id not in allowed_vars
                    and node.id not in ALLOWED_BUILTINS
                    and node.id not in {"math", "np"}
                ):
                    raise FormulaError("formula_forbidden_name", hint=f"Unknown or forbidden name: `{node.id}`.")
            return

        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise FormulaError("formula_forbidden_name", hint=f"Forbidden underscore attribute: `{node.attr}`.")
            if _is_np_accumulate(node):
                return
            _visit(node.value)
            if isinstance(node.value, ast.Name) and node.value.id == "math":
                if node.attr not in MATH_ATTRS:
                    raise FormulaError("formula_forbidden_call", hint=f"Forbidden math attribute: `math.{node.attr}`.")
                return
            if isinstance(node.value, ast.Name) and node.value.id == "np":
                if node.attr not in NP_ATTRS:
                    raise FormulaError("formula_forbidden_call", hint=f"Forbidden numpy attribute: `np.{node.attr}`.")
                return
            if node.attr not in NDARRAY_ATTRS:
                raise FormulaError("formula_forbidden_call", hint=f"Forbidden attribute: `.{node.attr}`.")
            return

        if isinstance(node, ast.Call):
            func = node.func
            # Visit func first so forbidden names/calls are reported before argument constants
            if isinstance(func, ast.Name):
                if func.id.startswith("_") or func.id in FORBIDDEN_NAMES:
                    raise FormulaError("formula_forbidden_name", hint=f"Forbidden function: `{func.id}`.")
                if func.id not in ALLOWED_BUILTINS:
                    raise FormulaError("formula_forbidden_call", hint=f"Calling `{func.id}` is not allowed.")
            elif isinstance(func, ast.Attribute):
                if func.attr.startswith("_"):
                    raise FormulaError("formula_forbidden_name", hint=f"Forbidden method: `{func.attr}`.")
                if _is_np_accumulate(func):
                    pass
                elif isinstance(func.value, ast.Name) and func.value.id == "math":
                    if func.attr not in MATH_FUNCS:
                        raise FormulaError("formula_forbidden_call", hint=f"Calling `math.{func.attr}` is not allowed.")
                elif isinstance(func.value, ast.Name) and func.value.id == "np":
                    if func.attr not in NP_FUNCS:
                        raise FormulaError("formula_forbidden_call", hint=f"Calling `np.{func.attr}` is not allowed.")
                else:
                    _visit(func.value)
                    if func.attr not in NDARRAY_METHODS:
                        raise FormulaError("formula_forbidden_call", hint=f"Calling `.{func.attr}` is not allowed.")
            else:
                raise FormulaError("formula_forbidden_call", hint="Only direct function or method calls are allowed.")

            is_float_builtin = isinstance(func, ast.Name) and func.id == "float" and len(node.args) == 1 and not node.keywords
            for arg_node in node.args:
                if isinstance(arg_node, ast.Starred):
                    raise FormulaError("formula_forbidden_node", hint="Starred call arguments (*args) are not allowed.")
                _visit(arg_node, in_float_call=is_float_builtin)
            for kw in node.keywords:
                if kw.arg is None or kw.arg.startswith("_"):
                    raise FormulaError("formula_forbidden_node", hint="**kwargs or underscore keyword arguments are not allowed.")
                _visit(kw.value)
            return

        if isinstance(node, ast.Constant):
            val = node.value
            if isinstance(val, str):
                if in_float_call and val.strip().lower() in {"nan", "inf", "-inf", "+inf"}:
                    return
                raise FormulaError("formula_forbidden_node", hint="String constants are not allowed in formulas.")
            if type(val) not in {int, float, bool, type(None)}:
                raise FormulaError("formula_forbidden_node", hint=f"Constant of type `{type(val).__name__}` is not allowed.")
            return

        if isinstance(node, ast.Assign):
            _visit(node.value)
            for target in node.targets:
                _register_target(target)
                _visit(target)
            return

        if isinstance(node, ast.AugAssign):
            _visit(node.op)
            _visit(node.value)
            _register_target(node.target)
            _visit(node.target)
            return

        if isinstance(node, ast.For):
            if node.orelse:
                raise FormulaError("formula_forbidden_node", hint="`for ... else` is not allowed.")
            it = node.iter
            valid_iter = False
            if isinstance(it, ast.Name) and (it.id in param_names or it.id in allowed_vars):
                valid_iter = True
            elif isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id in {"range", "zip", "enumerate"}:
                valid_iter = True
            if not valid_iter:
                raise FormulaError(
                    "formula_forbidden_node",
                    hint="`for` loop may only iterate over `range(...)`, `zip(...)`, `enumerate(...)`, or an input vector.",
                )
            _visit(it)
            _register_target(node.target)
            _visit(node.target)
            for stmt in node.body:
                _visit(stmt)
            return

        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            saved_vars = set(allowed_vars)
            for gen in node.generators:
                if getattr(gen, "is_async", 0):
                    raise FormulaError("formula_forbidden_node", hint="Async comprehensions are not allowed.")
                _visit(gen.iter)
                _register_target(gen.target)
                _visit(gen.target)
                for if_clause in gen.ifs:
                    _visit(if_clause)
            _visit(node.elt)
            allowed_vars.clear()
            allowed_vars.update(saved_vars)
            return

        for child in ast.iter_child_nodes(node):
            _visit(child)

    for stmt in fn.body:
        _visit(stmt)

    return param_names


def run_formula_in_sandbox(
    formula_code: str,
    bindings: list[dict[str, Any]],
    *,
    timeout_seconds: float = 3.0,
) -> list[dict[str, Any]]:
    """Validate AST and execute `formula_code` over `bindings` in an isolated `-I` subprocess."""
    if not bindings:
        raise FormulaError("formula_shape_invalid", hint="At least one binding is required.")
    first_vectors = bindings[0].get("vectors") or {}
    param_names = check_formula_ast(formula_code, expected_params=list(first_vectors.keys()))
    for item in bindings:
        vecs = item.get("vectors") or {}
        if set(vecs.keys()) != set(param_names) or len(vecs) != len(param_names):
            raise FormulaError(
                "formula_params_mismatch",
                hint=f"Function parameters {param_names} do not match binding `{item.get('label')}` inputs {sorted(vecs.keys())}.",
            )
        for p_name, values in vecs.items():
            if not isinstance(values, list) or not (1 <= len(values) <= 2000):
                raise FormulaError(
                    "formula_input_unbound",
                    hint=f"Vector `{p_name}` for `{item.get('label')}` must have between 1 and 2000 elements.",
                )

    from pathlib import Path
    worker_script = str(Path(__file__).with_name("formula_worker.py"))
    payload = json.dumps({"code": formula_code, "bindings": bindings}, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-I", worker_script],
            input=payload,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FormulaError("formula_timeout", hint="Formula execution timed out (limit 3s).") from exc

    if len(proc.stdout or b"") > 65536:
        raise FormulaError("formula_runtime_error", hint="Worker output exceeded 64KB limit.")

    if proc.returncode != 0 and not proc.stdout:
        # SIGXCPU (-24 / 152) or SIGKILL (-9 / 137) from rlimit CPU/time
        if proc.returncode in {-24, -9, 137, 152}:
            raise FormulaError("formula_timeout", hint="Formula exceeded CPU time limit.")
        raise FormulaError("formula_runtime_error", hint="Formula worker process terminated unexpectedly.")

    try:
        response = json.loads((proc.stdout or b"{}").decode("utf-8"))
    except Exception as exc:
        raise FormulaError("formula_runtime_error", hint="Invalid response from formula worker.") from exc

    if not isinstance(response, dict):
        raise FormulaError("formula_runtime_error", hint="Invalid response payload from formula worker.")

    if response.get("error"):
        err_code = str(response["error"])
        raise FormulaError(err_code, hint=str(response.get("hint") or ""))

    results = response.get("results")
    if not isinstance(results, list) or len(results) != len(bindings):
        raise FormulaError("formula_runtime_error", hint="Worker returned incomplete binding results.")

    verified_results: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            raise FormulaError("formula_runtime_error", hint="Invalid binding result entry.")
        if item.get("error"):
            raise FormulaError(str(item["error"]), hint=str(item.get("hint") or ""))
        val = item.get("value")
        try:
            fval = float(val)
        except (TypeError, ValueError) as exc:
            raise FormulaError("formula_non_finite", hint="Formula return value could not be converted to float.") from exc
        if not math.isfinite(fval):
            raise FormulaError("formula_non_finite", hint="Formula returned non-finite value (NaN or Inf).")
        verified_results.append({"label": item.get("label"), "value": fval})

    return verified_results


def runtime_engine_info() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
    }
