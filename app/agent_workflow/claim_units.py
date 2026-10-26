"""Structured claim units for the chat-answer faithfulness gate.

A claim cites one ordinal and carries either a prose quote or a structured
field binding. Parsing only checks shape; binding and numeric checks live
elsewhere.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal


class ClaimParseError(ValueError):
    pass


@dataclass(frozen=True)
class QuoteEvidence:
    type: Literal["quote"]
    evidence_quote: str


@dataclass(frozen=True)
class FieldEvidence:
    type: Literal["field"]
    fields: dict[str, Any]


@dataclass(frozen=True)
class ClaimUnit:
    id: str
    statement: str
    citation_ordinal: int
    evidence: QuoteEvidence | FieldEvidence


def parse_claims(payload: dict | list) -> list[ClaimUnit]:
    raw = payload.get("claims") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise ClaimParseError("claims must be a list")
    out: list[ClaimUnit] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ClaimParseError("claim must be an object")
        ev = item.get("evidence")
        if not isinstance(ev, dict) or ev.get("type") not in {"quote", "field"}:
            raise ClaimParseError("evidence.type must be quote or field")
        if ev["type"] == "quote":
            quote = ev.get("evidence_quote")
            if not isinstance(quote, str) or not quote.strip():
                raise ClaimParseError("evidence_quote required")
            evidence: QuoteEvidence | FieldEvidence = QuoteEvidence("quote", quote.strip())
        else:
            fields = ev.get("fields")
            if fields is None:
                fields = {key: value for key, value in ev.items() if key != "type"}
            if not isinstance(fields, dict) or not fields:
                raise ClaimParseError("fields required")
            evidence = FieldEvidence("field", dict(fields))
        try:
            ordinal = int(item["citation_ordinal"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ClaimParseError("citation_ordinal required") from exc
        if ordinal < 1:
            raise ClaimParseError("citation_ordinal required")
        claim_id = str(item.get("id") or "").strip() or f"c{len(out) + 1}"
        statement = str(item.get("statement") or "").strip()
        if not statement:
            raise ClaimParseError("statement required")
        out.append(ClaimUnit(claim_id, statement, ordinal, evidence))
    return out


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```\s*$", re.DOTALL | re.IGNORECASE)


def extract_claims_payload(answer: str) -> tuple[dict | list | None, str]:
    """Split a trailing claims JSON object from user-visible prose.

    Returns ``(payload, prose)``. ``payload`` is None when no claims object
    is present. The JSON block is never part of the prose returned to the user.
    """
    text = answer or ""
    fenced = _FENCED_JSON.search(text)
    if fenced:
        payload = _loads_object(fenced.group(1))
        if payload is not None and _looks_like_claims(payload):
            return payload, text[: fenced.start()].rstrip()
    stripped = text.rstrip()
    if not stripped.endswith("}"):
        return None, text
    start = _last_json_object_start(stripped)
    if start is None:
        return None, text
    payload = _loads_object(stripped[start:])
    if payload is None or not _looks_like_claims(payload):
        return None, text
    return payload, stripped[:start].rstrip()


def _looks_like_claims(payload: dict | list) -> bool:
    if isinstance(payload, list):
        return True
    return isinstance(payload, dict) and "claims" in payload


def _loads_object(raw: str) -> dict | list | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(value, (dict, list)):
        return value
    return None


def _last_json_object_start(text: str) -> int | None:
    """Index of the '{' that begins the last balanced JSON object, if any."""
    depth = 0
    in_string = False
    escape = False
    start = None
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth == 0:
                return None
            depth -= 1
            if depth == 0 and index == len(text) - 1:
                return start
    return None
