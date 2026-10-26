"""Apply the claim quote gate to one answer without touching direction Jev."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .claim_numeric import header_context, numbers_supported
from .claim_units import ClaimParseError, FieldEvidence, QuoteEvidence, extract_claims_payload, parse_claims
from .evidence import _citation_material, factual_claim, reviewed_claims

_BODY_KEYS = ("content", "text", "body", "excerpt", "summary", "answer")
_CITATION_MARKER = re.compile(r"\[\d+\]")


@dataclass
class ClaimGateApplication:
    prose: str
    kept_prose: str
    issues: list[dict]
    display_issues: list[str]
    dropped_lines: list[tuple[str, str]] = field(default_factory=list)
    audit: dict = field(default_factory=dict)


def collect_source_bodies(citations, observations=None) -> tuple[dict[int, str], list[int]]:
    """Full row text when the turn still has it; otherwise the citation quote.

    Falling back is recorded so a shadow report can tell a short excerpt from
    the document the tool returned.
    """
    rows = []
    for observation in observations or []:
        if not isinstance(observation, dict):
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        data = result.get("data")
        if isinstance(data, dict):
            rows.append(data)
        elif isinstance(data, list):
            rows.extend(row for row in data if isinstance(row, dict))
    bodies: dict[int, str] = {}
    fallbacks: list[int] = []
    for citation in citations or []:
        if not isinstance(citation, dict) or citation.get("ordinal") is None:
            continue
        try:
            ordinal = int(citation["ordinal"])
        except (TypeError, ValueError):
            continue
        quote = str(citation.get("quote") or "")
        full = _row_text(citation, rows)
        if full and len(full) > len(quote):
            bodies[ordinal] = full
        else:
            bodies[ordinal] = quote
            fallbacks.append(ordinal)
    return bodies, fallbacks


def apply_claim_gate(
    answer: str,
    citations,
    observations=None,
    *,
    mode: str,
    min_chars: int = 24,
    support_mode: str = "off",
    support_fn=None,
    support_threshold: float = 0.7,
    max_per_turn: int = 4,
) -> ClaimGateApplication:
    """Parse claims, bind them, and (in enforce) decide which prose lines remain.

    Shadow keeps the stripped prose for the legacy guard. Enforce drops lines
    that belong to a rejected claim. Neither mode turns a miss into 待核对.
    """
    started = time.monotonic()
    payload, prose = extract_claims_payload(answer or "")
    bodies, fallbacks = collect_source_bodies(citations, observations)
    parse_error = None
    claims_missing = payload is None
    units = []
    if payload is not None:
        try:
            units = parse_claims(payload)
        except ClaimParseError:
            parse_error = "claim_parse_error"
            claims_missing = True
            units = []
    if payload is not None and not units and parse_error is None:
        claims_missing = True
    review = reviewed_claims(units, citations, bodies, min_chars=min_chars) if units else None
    issues = list(review.issues) if review else []
    accepted = list(review.accepted) if review else []
    accepted, issues, support_audit = _apply_support(
        accepted,
        issues,
        citations,
        support_mode=support_mode,
        support_fn=support_fn,
        threshold=support_threshold,
        max_per_turn=max_per_turn,
    )
    review_audit = dict(review.audit) if review else {
        "claim_count": 0,
        "accepted_count": 0,
        "quote_match_kinds": [],
        "resolved_span_hashes": [],
        "drop_reasons": [],
        "source_body_fallback": [],
    }
    if mode == "enforce":
        kept, display, dropped = _enforce_lines(prose, accepted, issues, citations, bodies, claims_missing=claims_missing)
    else:
        kept, display, dropped = prose, [], []
    elapsed = max(0, int((time.monotonic() - started) * 1000))
    audit = {
        **review_audit,
        "mode": mode,
        "latency_ms": elapsed,
        "drop_reasons": [item["reason"] for item in issues],
        "claims_missing": claims_missing,
        "parse_error": parse_error,
        "source_body_fallback": fallbacks,
        "jev_support_calls": support_audit["jev_support_calls"],
        "jev_skipped_reasons": support_audit["jev_skipped_reasons"],
        "semantic_unchecked": support_audit["semantic_unchecked"],
        "jev_choices": support_audit["jev_choices"],
        "answer_quality_notes": {
            "prose_chars": len(prose),
            "claims_chars": max(0, len(answer or "") - len(prose)),
            "claims_missing": claims_missing,
            "latency_ms": elapsed,
            "claim_drop_count": len(issues),
        },
    }
    return ClaimGateApplication(prose, kept, issues, display, dropped, audit)


def _apply_support(accepted, issues, citations, *, support_mode, support_fn, threshold, max_per_turn):
    """Optional Jev pass. Only explicit ``does_not_support`` in enforce drops a claim."""
    audit = {"jev_support_calls": 0, "jev_skipped_reasons": [], "semantic_unchecked": [], "jev_choices": []}
    if support_mode == "off" or support_fn is None or not accepted:
        return accepted, issues, audit
    limit = max(0, int(max_per_turn))
    eligible = list(accepted[:limit])
    for claim in accepted[limit:]:
        audit["jev_skipped_reasons"].append({"id": claim.id, "reason": "per_turn_cap"})
    if not eligible:
        return accepted, issues, audit
    indexed = {}
    for citation in citations or []:
        if isinstance(citation, dict) and citation.get("ordinal") is not None:
            try:
                indexed[int(citation["ordinal"])] = citation
            except (TypeError, ValueError):
                continue
    payload = []
    for claim in eligible:
        if isinstance(claim.evidence, QuoteEvidence):
            quote = claim.evidence.evidence_quote
        else:
            quote = _citation_material(indexed.get(claim.citation_ordinal, {}))
        payload.append((claim.id, quote, claim.statement))
    try:
        results = list(support_fn(payload))
    except Exception:
        audit["semantic_unchecked"] = [{"id": claim.id, "status": "error"} for claim in eligible]
        return accepted, issues, audit
    audit["jev_support_calls"] = 1
    by_id = {claim.id: result for claim, result in zip(eligible, results)} if len(results) == len(eligible) else {}
    if len(results) != len(eligible):
        audit["semantic_unchecked"] = [{"id": claim.id, "status": "validation_error"} for claim in eligible]
        return accepted, issues, audit
    kept = []
    eligible_ids = {claim.id for claim in eligible}
    for claim in accepted:
        if claim.id not in eligible_ids:
            kept.append(claim)
            continue
        result = by_id.get(claim.id)
        status = getattr(result, "status", None)
        choice = getattr(result, "choice", None)
        probabilities = getattr(result, "probabilities", None) or {}
        if status != "ok":
            audit["semantic_unchecked"].append({"id": claim.id, "status": status or "error"})
            kept.append(claim)
            continue
        audit["jev_choices"].append({"id": claim.id, "choice": choice})
        supports = float(probabilities.get("supports") or 0)
        if choice == "does_not_support" and support_mode == "enforce":
            issues.append({
                "id": claim.id,
                "reason": "does_not_support",
                "citation_ordinal": claim.citation_ordinal,
                "statement": claim.statement,
            })
            continue
        if choice != "supports" or supports < threshold:
            reason = choice if choice in {"unrelated", "does_not_support"} else "below_threshold"
            audit["semantic_unchecked"].append({"id": claim.id, "status": reason})
        kept.append(claim)
    return kept, issues, audit


def _enforce_lines(prose, accepted, issues, citations, bodies, *, claims_missing: bool):
    indexed = {}
    for citation in citations or []:
        if isinstance(citation, dict) and citation.get("ordinal") is not None:
            try:
                indexed[int(citation["ordinal"])] = citation
            except (TypeError, ValueError):
                continue
    spans: list[str] = []
    headers: list[str] = []
    for claim in accepted:
        citation = indexed.get(claim.citation_ordinal, {})
        if isinstance(claim.evidence, QuoteEvidence):
            spans.append(claim.evidence.evidence_quote)
            source = bodies.get(claim.citation_ordinal) or str(citation.get("quote") or "")
            start = source.find(claim.evidence.evidence_quote)
            headers.append(header_context(source, start if start >= 0 else None))
        elif isinstance(claim.evidence, FieldEvidence):
            spans.append(_citation_material(citation))
    allowed = "\n".join(spans)
    header = "\n".join(headers)
    reasons_for = {issue.get("statement"): issue.get("reason") for issue in issues if issue.get("statement")}
    kept_lines: list[str] = []
    dropped: list[tuple[str, str]] = []
    display: list[str] = []
    for line in (prose or "").splitlines():
        stripped = line.strip()
        if not stripped:
            kept_lines.append(line)
            continue
        reason = _line_reason(stripped, allowed, header, reasons_for, claims_missing=claims_missing, accepted=accepted)
        if reason:
            dropped.append((stripped, reason))
            if reason not in display:
                display.append(reason)
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip(), display, dropped


def _line_reason(stripped, allowed, header, reasons_for, *, claims_missing: bool, accepted):
    if claims_missing and factual_claim(stripped):
        return "claims_missing"
    for statement, reason in reasons_for.items():
        if _statement_in_line(statement, stripped):
            return reason or "quote_not_found"
    body = _CITATION_MARKER.sub(" ", stripped)
    if numbers_supported(body, allowed, header_context=header):
        return "unsupported_numeric"
    if not accepted and reasons_for and factual_claim(stripped):
        return next(iter(reasons_for.values())) or "quote_not_found"
    return None


def _statement_in_line(statement: str, line: str) -> bool:
    cleaned = (statement or "").strip().rstrip(".。")
    return bool(cleaned and cleaned in line)


def _row_text(citation: dict, rows: list[dict]) -> str:
    keys = {
        str(citation.get(name))
        for name in ("source_id", "document_id", "chunk_id", "source_url")
        if citation.get(name)
    }
    if not keys:
        return ""
    best = ""
    for row in rows:
        row_ids = {
            str(row.get(name))
            for name in ("source_id", "id", "document_id", "chunk_id", "url", "source_url")
            if row.get(name)
        }
        if keys.isdisjoint(row_ids):
            continue
        for name in _BODY_KEYS:
            value = row.get(name)
            if isinstance(value, str) and len(value) > len(best):
                best = value
    return best
