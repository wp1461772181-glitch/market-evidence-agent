"""Bind a model-supplied quote to a literal source span.

Numeric checks must use ``resolved_quote``, which is sliced from the source.
A near-miss fuzzy match therefore cannot smuggle the model's digits through.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.material_analysis_schema import resolve_citation


@dataclass(frozen=True)
class BindResult:
    ok: bool
    match_kind: str | None
    resolved_quote: str
    start_char: int | None
    end_char: int | None
    reason: str | None = None


def bind_quote(model_quote: str, source_text: str, *, min_chars: int = 24) -> BindResult:
    quote = (model_quote or "").strip()
    if len(quote) < int(min_chars):
        return BindResult(False, None, "", None, None, "quote_too_short")
    source = source_text or ""
    resolved: dict[str, Any] = resolve_citation(quote, source)
    kind = str(resolved.get("match_kind") or "unverified")
    start, end = resolved.get("start_char"), resolved.get("end_char")
    if kind == "unverified" or not isinstance(start, int) or not isinstance(end, int):
        return BindResult(False, "unverified", "", None, None, "quote_not_found")
    if not (0 <= start < end <= len(source)):
        return BindResult(False, "unverified", "", None, None, "quote_not_found")
    if kind not in {"exact", "normalized", "fuzzy"}:
        return BindResult(False, kind, "", start, end, "quote_not_found")
    return BindResult(True, kind, source[start:end], start, end, None)
