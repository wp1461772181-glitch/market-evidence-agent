"""Validated contract for one frozen material analysis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    quote: str = Field(min_length=1, max_length=500)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)


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
    prompt_version: str = Field(pattern=r"^material-analysis-prompt-v1$")
    summary: str = Field(min_length=1, max_length=1200)
    facts: list[Fact] = Field(max_length=12)
    supporting: list[Inference] = Field(max_length=6)
    counter: list[Inference] = Field(max_length=6)
    uncertainties: list[Uncertainty] = Field(max_length=8)
    key_numbers: list[KeyNumber] = Field(max_length=12)


def validate_material_analysis(payload: dict[str, Any], text: str) -> MaterialAnalysisPayload:
    """Validate JSON shape, IDs, fact links, and exact frozen-text quotations."""
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
            if citation.end_char <= citation.start_char or citation.end_char > len(text):
                raise ValueError("citation coordinates are outside the frozen text")
            if text[citation.start_char:citation.end_char] != citation.quote:
                raise ValueError("citation quote does not match the frozen text")
    return parsed


def locate_material_analysis_citations(payload: dict[str, Any], text: str) -> dict[str, Any]:
    """Fill omitted coordinates from exact quotes; supplied coordinates stay strict.

    If the same quote occurs more than once, the earliest exact occurrence is
    used. This is deliberately exact matching, never fuzzy correction.
    """
    import copy

    if not isinstance(payload, dict):
        raise ValueError("analysis response must be a JSON object")
    normalized = copy.deepcopy(payload)
    groups = ("facts", "supporting", "counter", "uncertainties", "key_numbers")
    for group_name in groups:
        for item in normalized.get(group_name, []) if isinstance(normalized.get(group_name, []), list) else []:
            citations = item.get("citations", []) if isinstance(item, dict) else []
            for citation in citations if isinstance(citations, list) else []:
                if not isinstance(citation, dict):
                    continue
                has_start = "start_char" in citation
                has_end = "end_char" in citation
                if has_start != has_end:
                    raise ValueError("citation must supply both coordinates or neither")
                if not has_start:
                    quote = citation.get("quote")
                    if not isinstance(quote, str) or not quote:
                        raise ValueError("citation quote is missing")
                    start = text.find(quote)
                    if start < 0:
                        raise ValueError("citation quote does not occur in the frozen text")
                    citation["start_char"] = start
                    citation["end_char"] = start + len(quote)
    return normalized
