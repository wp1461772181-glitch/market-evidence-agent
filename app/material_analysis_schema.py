"""Validated contract for one frozen material analysis."""

from __future__ import annotations

import copy
import difflib
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MATCH_KINDS = ("exact", "normalized", "fuzzy", "unverified")
MatchKind = Literal["exact", "normalized", "fuzzy", "unverified"]
FUZZY_MIN_SIMILARITY = 0.95
FUZZY_MAX_LENGTH_DELTA = 12

_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_CHARACTER_FOLD = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u00a0": " ",
    "\u202f": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    quote: str = Field(min_length=1, max_length=500)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, gt=0)
    # How the quote was bound to the frozen text. New responses are always
    # resolved by the server; older saved payloads default to exact.
    match_kind: MatchKind = "exact"
    similarity: float | None = Field(default=None, ge=0, le=1)
    original_quote: str | None = Field(default=None, max_length=500)


class Fact(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=1200)
    citations: list[Citation] = Field(min_length=1, max_length=8)


class Inference(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=1200)
    rationale: str = Field(min_length=1, max_length=1200)
    fact_ids: list[str] = Field(max_length=12)
    citations: list[Citation] = Field(min_length=1, max_length=8)


class Uncertainty(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=1200)
    reason: str = Field(min_length=1, max_length=1200)
    citations: list[Citation] = Field(default_factory=list, max_length=8)


class KeyNumber(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    value_text: str = Field(min_length=1, max_length=160)
    period: str | None = Field(default=None, max_length=160)
    citations: list[Citation] = Field(min_length=1, max_length=8)


class MaterialAnalysisPayload(StrictModel):
    schema_version: str = Field(pattern=r"^material-analysis-v1$")
    prompt_version: str = Field(pattern=r"^material-analysis-prompt-v[123]$")
    summary: str = Field(min_length=1, max_length=1200)
    facts: list[Fact] = Field(max_length=12)
    supporting: list[Inference] = Field(max_length=6)
    counter: list[Inference] = Field(max_length=6)
    uncertainties: list[Uncertainty] = Field(max_length=8)
    key_numbers: list[KeyNumber] = Field(max_length=12)


def validate_material_analysis(payload: dict[str, Any], text: str) -> MaterialAnalysisPayload:
    """Validate JSON shape, IDs, fact links, and frozen-text citation spans.

    Unverified citations keep the model's quotation but have no coordinates;
    they are surfaced as a citation that needs review rather than invalidating
    the whole analysis. Every other citation must still point at exactly the
    stored source substring.
    """
    if not isinstance(text, str):
        raise ValueError("analysis text must be text")
    parsed = MaterialAnalysisPayload.model_validate(payload)
    all_groups = (parsed.facts, parsed.supporting, parsed.counter, parsed.uncertainties)
    for group in all_groups:
        ids = [item.id for item in group]
        if len(ids) != len(set(ids)):
            raise ValueError("item IDs must be unique within each list")
    fact_ids = {item.id for item in parsed.facts}
    for item in [*parsed.supporting, *parsed.counter]:
        if any(fact_id not in fact_ids for fact_id in item.fact_ids):
            raise ValueError("inference references an unknown fact_id")
    citation_groups = [item.citations for group in all_groups for item in group]
    citation_groups.extend(item.citations for item in parsed.key_numbers)
    for citations in citation_groups:
        for citation in citations:
            if citation.match_kind == "unverified":
                if citation.start_char is not None or citation.end_char is not None:
                    raise ValueError("unverified citation must not carry coordinates")
                continue
            if citation.start_char is None or citation.end_char is None:
                raise ValueError("citation coordinates are missing")
            if citation.end_char <= citation.start_char or citation.end_char > len(text):
                raise ValueError("citation coordinates are outside the frozen text")
            if text[citation.start_char:citation.end_char] != citation.quote:
                raise ValueError("citation quote does not match the frozen text")
    return parsed


def locate_material_analysis_citations(payload: dict[str, Any], text: str) -> dict[str, Any]:
    """Bind every citation to the frozen text with graded matching.

    Resolution order is exact, then normalization (NFKC, quotes/dashes,
    whitespace), then a bounded fuzzy alignment. A quote that cannot be bound
    is kept as unverified instead of rejecting the whole analysis; the caller
    decides whether the verification rate is acceptable. The resolved quote is
    always the literal source substring so saved payloads remain auditable.
    """
    if not isinstance(payload, dict):
        raise ValueError("analysis response must be a JSON object")
    normalized = copy.deepcopy(payload)
    groups = ("facts", "supporting", "counter", "uncertainties", "key_numbers")
    for group_name in groups:
        group = normalized.get(group_name, [])
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            citations = item.get("citations", [])
            if not isinstance(citations, list):
                continue
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                quote = citation.get("quote")
                if not isinstance(quote, str) or not quote:
                    raise ValueError("citation quote is missing")
                has_start = "start_char" in citation
                has_end = "end_char" in citation
                if has_start != has_end:
                    raise ValueError("citation must supply both coordinates or neither")
                resolved = resolve_citation(quote, text)
                citation["quote"] = resolved["quote"]
                if resolved["match_kind"] == "unverified":
                    citation["start_char"] = None
                    citation["end_char"] = None
                else:
                    citation["start_char"] = resolved["start_char"]
                    citation["end_char"] = resolved["end_char"]
                citation["match_kind"] = resolved["match_kind"]
                citation["similarity"] = resolved["similarity"]
                if resolved["quote"] != quote:
                    citation["original_quote"] = quote
    return normalized


def resolve_citation(quote: str, text: str) -> dict[str, Any]:
    """Resolve one quotation to a literal source span and a match kind."""
    start = text.find(quote)
    if start >= 0:
        return {
            "quote": quote, "start_char": start, "end_char": start + len(quote),
            "match_kind": "exact", "similarity": 1.0,
        }
    normalized_text, mapping = _normalize_with_map(text)
    normalized_quote, _ = _normalize_with_map(quote)
    normalized_quote = normalized_quote.strip()
    if normalized_quote:
        position = normalized_text.find(normalized_quote)
        if position >= 0:
            source_start, source_end = _source_span(position, len(normalized_quote), mapping, len(text))
            return {
                "quote": text[source_start:source_end], "start_char": source_start,
                "end_char": source_end, "match_kind": "normalized", "similarity": 1.0,
            }
    fuzzy = _fuzzy_span(normalized_text, mapping, normalized_quote, text)
    if fuzzy is not None:
        return fuzzy
    best = fuzzy_similarity(normalized_quote, normalized_text) if normalized_quote else 0.0
    return {
        "quote": quote, "start_char": None, "end_char": None,
        "match_kind": "unverified", "similarity": round(best, 4),
    }


def citation_verification(payload: dict[str, Any]) -> dict[str, Any]:
    """Count citation match kinds for a saved payload (safe on legacy rows)."""
    counts = {kind: 0 for kind in MATCH_KINDS}
    total = 0
    groups = ("facts", "supporting", "counter", "uncertainties", "key_numbers")
    for group_name in groups:
        group = payload.get(group_name, []) if isinstance(payload, dict) else []
        if not isinstance(group, list):
            continue
        for item in group:
            citations = item.get("citations", []) if isinstance(item, dict) else []
            if not isinstance(citations, list):
                continue
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                kind = citation.get("match_kind", "exact")
                if kind not in counts:
                    kind = "exact"
                counts[kind] += 1
                total += 1
    verified = total - counts["unverified"]
    return {
        "total": total, "verified": verified, "unverified": counts["unverified"],
        "exact": counts["exact"], "normalized": counts["normalized"], "fuzzy": counts["fuzzy"],
        "verified_ratio": round(verified / total, 4) if total else 1.0,
    }


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    mapping: list[int] = []
    for index, original in enumerate(text):
        folded = _CHARACTER_FOLD.get(original)
        if folded is None:
            folded = unicodedata.normalize("NFKC", original)
        for character in folded:
            if character in _ZERO_WIDTH:
                continue
            if character.isspace():
                if characters and characters[-1] == " ":
                    continue
                characters.append(" ")
                mapping.append(index)
            else:
                characters.append(character)
                mapping.append(index)
    return "".join(characters), mapping


def _source_span(position: int, length: int, mapping: list[int], text_length: int) -> tuple[int, int]:
    start = mapping[position]
    end = min(text_length, mapping[position + length - 1] + 1)
    return start, end


def _fuzzy_span(normalized_text: str, mapping: list[int], normalized_quote: str, text: str) -> dict[str, Any] | None:
    quote = normalized_quote.strip()
    if len(quote) < 12:
        return None
    tokens = sorted({token for token in _tokens(quote) if len(token) >= 5}, key=len, reverse=True)
    if not tokens:
        return None
    candidates: dict[tuple[int, int], float] = {}
    for token in tokens[:2]:
        offset = quote.find(token)
        if offset < 0:
            continue
        occurrences = 0
        position = normalized_text.find(token)
        while position >= 0 and occurrences < 30:
            occurrences += 1
            anchor_start = position - offset
            if anchor_start < 0:
                position = normalized_text.find(token, position + 1)
                continue
            window_start = max(0, anchor_start - 12)
            window_end = min(len(normalized_text), anchor_start + len(quote) + 48)
            window = normalized_text[window_start:window_end]
            if len(window) < len(quote) * 0.8:
                position = normalized_text.find(token, position + 1)
                continue
            matcher = difflib.SequenceMatcher(None, quote, window, autojunk=False)
            blocks = [block for block in matcher.get_matching_blocks() if block.size]
            if not blocks:
                position = normalized_text.find(token, position + 1)
                continue
            matched = sum(block.size for block in blocks)
            if matched < len(quote) * 0.9:
                position = normalized_text.find(token, position + 1)
                continue
            first, last = blocks[0], blocks[-1]
            span_start = window_start + first.b
            span_end = window_start + last.b + last.size
            # Trim only small unmatched edges (the model's own wording) instead
            # of letting a generous window depress the similarity score.
            if first.a <= 8:
                span_start = max(0, span_start - first.a)
            if len(quote) - (last.a + last.size) <= 8:
                span_end = min(len(normalized_text), span_end + (len(quote) - last.a - last.size))
            ratio = 2 * matched / (len(quote) + (span_end - span_start))
            key = (span_start, span_end)
            candidates[key] = max(candidates.get(key, 0.0), ratio)
            position = normalized_text.find(token, position + 1)
    if not candidates:
        return None
    ranked = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
    (start, end), ratio = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if ratio < FUZZY_MIN_SIMILARITY or second > ratio - 0.02:
        return None
    if abs((end - start) - len(quote)) > max(FUZZY_MAX_LENGTH_DELTA, int(len(quote) * 0.08)):
        return None
    source_start, source_end = _source_span(start, end - start, mapping, len(text))
    return {
        "quote": text[source_start:source_end], "start_char": source_start, "end_char": source_end,
        "match_kind": "fuzzy", "similarity": round(ratio, 4),
    }


def fuzzy_similarity(normalized_quote: str, normalized_text: str) -> float:
    """Best-effort similarity for reporting an unverified quote; never binding."""
    if not normalized_quote:
        return 0.0
    tokens = sorted({token for token in _tokens(normalized_quote) if len(token) >= 5}, key=len, reverse=True)
    best = 0.0
    for token in tokens[:2]:
        position = normalized_text.find(token)
        occurrences = 0
        offset = normalized_quote.find(token)
        while position >= 0 and occurrences < 20:
            occurrences += 1
            start = max(0, position - offset) if offset >= 0 else max(0, position)
            window = normalized_text[start:start + len(normalized_quote) + 12]
            best = max(best, difflib.SequenceMatcher(None, normalized_quote, window, autojunk=False).ratio())
            position = normalized_text.find(token, position + 1)
    return best


def _tokens(value: str) -> list[str]:
    token: list[str] = []
    tokens: list[str] = []
    for character in value:
        if character.isalnum() or character == "_":
            token.append(character)
        elif token:
            tokens.append("".join(token))
            token = []
    if token:
        tokens.append("".join(token))
    return tokens