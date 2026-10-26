"""Numeric and field checks for claim evidence.

Figures are accepted only when they occur in the resolved source span, or when
the span's unitless cells plus a walked-up unit header (``In millions``) scale
to the stated amount. The model-copied quote is never an input here.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from app.agent_workflow.claim_units import FieldEvidence
from app.agent_workflow.evidence import _CLAIM_NUMBER, _round_to, _significant
from app.agent_workflow.semantic import _MAGNITUDE, figure_values


_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_DURATION = re.compile(
    r"\d+(?:\.\d+)?\s*(?:天|日|个月|年|周|条|篇|份|项|页|行|个|次|点|位|名|步|轮|倍|小时|分|秒|字符|字|days?|hours?|items?|times?|points?|characters?|chars?)",
    re.I,
)
_DATE = re.compile(
    r"(?<!\d)(?:\d{4}\s*[-/年]\s*)?\d{1,2}\s*[-/月]\s*\d{1,2}(?:\s*日)?(?!\d)|(?<!\d)\d{4}\s*年\s*\d{1,2}\s*月|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b",
    re.I,
)
_HEADER_SCALE = (
    (re.compile(r"in\s+millions|百万", re.I), 1e6),
    (re.compile(r"in\s+thousands|千美元|单位[:：]\s*千", re.I), 1e3),
    (re.compile(r"in\s+billions|十亿美元", re.I), 1e9),
    (re.compile(r"单位[:：]\s*亿|(?:^|\s)亿元", re.I), 1e8),
)
_MAGNITUDE_WORD = re.compile(
    r"\s*(?:万亿|千万|百万|亿|万|千|trillion|billion|million|thousand|bn|mn|b|k|m)\b",
    re.I,
)


def header_context(source_text: str, start_char: int | None, *, lines_up: int = 8) -> str:
    """Lines above a resolved span, where table units such as ``In millions`` live."""
    if not source_text or start_char is None:
        return ""
    start = max(0, min(int(start_char), len(source_text)))
    lines = source_text[:start].splitlines()
    window = max(1, int(lines_up))
    return "\n".join(lines[-window:])


def numbers_supported(statement: str, resolved_span: str, *, header_context: str = "") -> list[float]:
    """Scaled amounts in ``statement`` that the resolved span does not support."""
    span = resolved_span or ""
    span_plain = _comma_strip(span)
    allowed = set(figure_values(span))
    allowed.update(_header_scaled_cells(span, _header_scale(header_context)))
    rejected: list[float] = []
    for token, scaled, digits, unitless in _statement_figures(statement):
        if _ignored_figure(scaled, unitless):
            continue
        if token in span_plain:
            continue
        if scaled in allowed or _rounded_match(scaled, digits, allowed):
            continue
        rejected.append(scaled)
    return sorted(set(rejected))


def field_evidence_matches(evidence: FieldEvidence, citation: dict) -> bool:
    """Every bound field value must occur on the structured citation, not in prose alone."""
    if not isinstance(citation, dict) or not evidence.fields:
        return False
    for key, value in evidence.fields.items():
        if not _field_supported(key, value, citation):
            return False
    return True


def _statement_figures(text: str) -> list[tuple[str, float, int, bool]]:
    prepared = _prepare(text)
    found: list[tuple[str, float, int, bool]] = []
    for dollar, number, unit, percent, currency in _CLAIM_NUMBER.findall(prepared):
        if not number:
            continue
        scaled = round(float(number) * _MAGNITUDE.get(unit.lower(), 1.0), 4)
        unitless = not (dollar or unit or percent or currency)
        found.append((number, scaled, _significant(number), unitless))
    return found


def _prepare(text: str) -> str:
    prepared = unicodedata.normalize("NFKC", text or "").replace("萬", "万").replace("億", "亿")
    prepared = _TIME.sub(" ", prepared)
    prepared = _DATE.sub(" ", prepared)
    prepared = _DURATION.sub(" ", prepared)
    return _comma_strip(prepared)


def _comma_strip(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text or "")


def _ignored_figure(value: float, unitless: bool) -> bool:
    if not unitless:
        return False
    if 1.0 <= value <= 10.0 and value == int(value):
        return True
    return 1990 <= value <= 2035 and value == int(value)


def _header_scale(header: str) -> float:
    for pattern, scale in _HEADER_SCALE:
        if pattern.search(header or ""):
            return scale
    return 1.0


def _header_scaled_cells(span: str, scale: float) -> set[float]:
    if scale == 1.0:
        return set()
    plain = _comma_strip(span)
    scaled: set[float] = set()
    for match in re.finditer(r"\d+(?:\.\d+)?", plain):
        raw = float(match.group())
        if 1900 <= raw <= 2100 and raw == int(raw):
            continue
        if _MAGNITUDE_WORD.match(plain[match.end(): match.end() + 16]):
            continue
        scaled.add(round(raw * scale, 4))
    return scaled


def _rounded_match(value: float, digits: int, allowed: set[float]) -> bool:
    if digits < 2:
        return False
    return any(_round_to(candidate, digits) == _round_to(value, digits) for candidate in allowed if candidate)


def _field_supported(key: str, value: Any, citation: dict) -> bool:
    if key in citation and _same_value(citation.get(key), value):
        return True
    calculation = citation.get("calculation")
    if isinstance(calculation, dict) and key in calculation and _same_value(calculation.get(key), value):
        return True
    return _value_in_text(value, _citation_blob(citation))


def _citation_blob(citation: dict) -> str:
    parts: list[str] = []
    for key in ("quote", "figures", "title", "trading_date", "concept", "period", "metric"):
        if citation.get(key) is not None:
            parts.append(str(citation[key]))
    calculation = citation.get("calculation")
    if isinstance(calculation, dict):
        parts.append(json.dumps(calculation, ensure_ascii=False, default=str))
    return " ".join(parts)


def _same_value(left: Any, right: Any) -> bool:
    if left == right:
        return True
    left_number, right_number = _as_number(left), _as_number(right)
    if left_number is None or right_number is None:
        return str(left).strip().casefold() == str(right).strip().casefold()
    return abs(left_number - right_number) <= 1e-6 * max(1.0, abs(right_number))


def _value_in_text(value: Any, blob: str) -> bool:
    number = _as_number(value)
    if number is not None and not isinstance(value, bool):
        return _number_in_text(number, blob)
    return str(value).strip().casefold() in blob.casefold()


def _number_in_text(number: float, text: str) -> bool:
    plain = _comma_strip(text)
    tolerance = 1e-6 * max(1.0, abs(number))
    return any(abs(float(match.group()) - number) <= tolerance for match in re.finditer(r"\d+(?:\.\d+)?", plain))


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        token = value.strip().replace(",", "")
        if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
            return float(token)
    return None
