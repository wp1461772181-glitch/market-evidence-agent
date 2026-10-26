# Claim + Source Quote + Jev Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a flagged chat-answer faithfulness gate: structured claims with quote/field evidence, numbers checked on **resolved source spans**, then optional tight-budget Jev support checks — without changing direction Jev or breaking legacy tests when flags are off.

**Architecture:** New `claim_units` + `quote_bind` sit beside `evidence.py`. `reviewed_claims` runs when `CLAIM_QUOTE_GATE` is shadow/enforce; legacy `reviewed_answer` remains default. `jev_claim_support.py` is a separate OpenRouter Decisions client (1 attempt, ~5s, daily cap) with `jev-claim-support-v1`, never sharing retry budget or learning stats with `jev_provider.py`.

**Tech Stack:** Python 3.12, existing FastAPI agent workflow, `resolve_citation` from `material_analysis_schema.py`, OpenRouter Decisions API, pytest.

**Spec:** `docs/superpowers/specs/2026-10-26-claim-quote-jev-verification-design.md` (r2).

## Global Constraints

- Flags default **off**; existing `test_agent_evidence_*` / repair tests must stay green with no env changes.
- Numeric checks use **resolved source span text only**, never the model-copied quote alone.
- Quote not found / unverified → **drop + repair**, not 待核对.
- Min quote length applies to **all** match kinds (default 24).
- Claim-support Jev: **1 attempt**, timeout ≤5s, daily cap; errors → `semantic_unchecked` only; downgrade only on explicit `does_not_support`.
- Do not write claim-support outcomes into forecast / Jev learning tables.
- Direction `jev_provider.py` retry/timeout behavior unchanged.

---

### Task 1: Claim unit schema and parser

**Files:**
- Create: `app/agent_workflow/claim_units.py`
- Test: `tests/test_claim_units.py`

**Interfaces:**
- Produces: `QuoteEvidence`, `FieldEvidence`, `ClaimUnit`, `parse_claims(payload: dict | list) -> list[ClaimUnit]`, `ClaimParseError`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_claim_units.py
import pytest
from app.agent_workflow.claim_units import parse_claims, ClaimParseError

def test_parse_quote_evidence():
    units = parse_claims({
        "claims": [{
            "id": "c1",
            "statement": "Revenue was 16,128 million.",
            "citation_ordinal": 1,
            "evidence": {"type": "quote", "evidence_quote": "Total revenue was 16,128 for the year."},
        }]
    })
    assert len(units) == 1
    assert units[0].evidence.type == "quote"
    assert units[0].citation_ordinal == 1

def test_parse_field_evidence_price():
    units = parse_claims({
        "claims": [{
            "id": "c2",
            "statement": "Close was 192.5 on 2024-01-02.",
            "citation_ordinal": 2,
            "evidence": {"type": "field", "fields": {"trading_date": "2024-01-02", "close": 192.5}},
        }]
    })
    assert units[0].evidence.type == "field"

def test_reject_missing_evidence():
    with pytest.raises(ClaimParseError):
        parse_claims({"claims": [{"id": "c3", "statement": "x", "citation_ordinal": 1}]})
```

- [ ] **Step 2: Run tests — expect FAIL** (module missing)

```bash
cd /path/to/market-evidence-agent && .venv/bin/pytest tests/test_claim_units.py -v
```

- [ ] **Step 3: Implement minimal schema**

```python
# app/agent_workflow/claim_units.py
from __future__ import annotations
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
            q = ev.get("evidence_quote")
            if not isinstance(q, str) or not q.strip():
                raise ClaimParseError("evidence_quote required")
            evidence: QuoteEvidence | FieldEvidence = QuoteEvidence("quote", q.strip())
        else:
            fields = ev.get("fields")
            if not isinstance(fields, dict) or not fields:
                raise ClaimParseError("fields required")
            evidence = FieldEvidence("field", dict(fields))
        try:
            ordinal = int(item["citation_ordinal"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ClaimParseError("citation_ordinal required") from exc
        cid = str(item.get("id") or "").strip() or f"c{len(out)+1}"
        statement = str(item.get("statement") or "").strip()
        if not statement:
            raise ClaimParseError("statement required")
        out.append(ClaimUnit(cid, statement, ordinal, evidence))
    return out
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
.venv/bin/pytest tests/test_claim_units.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/agent_workflow/claim_units.py tests/test_claim_units.py
git commit -m "feat: add claim unit schema for quote and field evidence"
```

---

### Task 2: Quote bind wrapper (min length + resolved span)

**Files:**
- Create: `app/agent_workflow/quote_bind.py`
- Test: `tests/test_quote_bind.py`

**Interfaces:**
- Consumes: `resolve_citation` from `app.material_analysis_schema`
- Produces: `BindResult(ok, match_kind, resolved_quote, start_char, end_char, reason)`, `bind_quote(model_quote, source_text, *, min_chars=24) -> BindResult`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_quote_bind.py
from app.agent_workflow.quote_bind import bind_quote

SOURCE = "Apple Inc. Total net sales were 16,128 for fiscal 2024. Operating income follows."

def test_exact_bind_returns_source_span():
    r = bind_quote("Total net sales were 16,128 for fiscal 2024.", SOURCE)
    assert r.ok and r.match_kind == "exact"
    assert "16,128" in r.resolved_quote

def test_too_short_rejected_even_if_exact():
    r = bind_quote("16,128", SOURCE, min_chars=24)
    assert not r.ok and r.reason == "quote_too_short"

def test_not_found_is_drop_not_pending():
    r = bind_quote("This sentence does not appear anywhere in the filing text.", SOURCE)
    assert not r.ok and r.reason == "quote_not_found"

def test_fuzzy_near_miss_uses_resolved_source_text():
    # Model typo in digits; if fuzzy still binds, resolved span must be source truth
    model = "Total net sales were 16,182 for fiscal 2024."
    r = bind_quote(model, SOURCE, min_chars=24)
    if r.ok:
        assert "16,128" in r.resolved_quote
        assert r.resolved_quote != model or "16,128" in r.resolved_quote
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_quote_bind.py -v
```

- [ ] **Step 3: Implement**

```python
# app/agent_workflow/quote_bind.py
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
    if len(quote) < min_chars:
        return BindResult(False, None, "", None, None, "quote_too_short")
    resolved: dict[str, Any] = resolve_citation(quote, source_text or "")
    kind = resolved.get("match_kind") or (
        "exact" if resolved.get("start_char") is not None and resolved.get("quote") == quote
        else "unverified"
    )
    # resolve_citation returns match_kind in locate path; normalize here if absent
    if resolved.get("start_char") is None:
        return BindResult(False, "unverified", quote, None, None, "quote_not_found")
    span = str(resolved.get("quote") or "")
    # Prefer slicing source when coordinates exist
    start, end = resolved.get("start_char"), resolved.get("end_char")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(source_text):
        span = source_text[start:end]
    mk = str(resolved.get("match_kind") or kind)
    if mk == "unverified":
        return BindResult(False, mk, quote, start, end, "quote_not_found")
    return BindResult(True, mk, span, start if isinstance(start, int) else None, end if isinstance(end, int) else None, None)
```

Note: Align `match_kind` reading with actual `resolve_citation` return keys (`match_kind` is set in `locate_material_analysis_citations`; raw `resolve_citation` may omit it — set kind from whether quote equals span / normalized / fuzzy fields present). Adjust implementation to match real return dict after reading `resolve_citation` once in the implementing session.

- [ ] **Step 4: Run — expect PASS** (adjust fuzzy test if locate cannot bind digit typo — then assert `not r.ok`)

- [ ] **Step 5: Commit**

```bash
git add app/agent_workflow/quote_bind.py tests/test_quote_bind.py
git commit -m "feat: bind claim quotes with min length and resolved spans"
```

---

### Task 3: Numeric check on resolved span + unit walk-up + field bind

**Files:**
- Create: `app/agent_workflow/claim_numeric.py`
- Test: `tests/test_claim_numeric.py`

**Interfaces:**
- Consumes: `BindResult`, citation dicts, `_claim_figures` patterns from evidence.py (import or duplicate minimal figure parser — prefer import private helpers only if stable; else copy minimal digit extract)
- Produces: `numbers_supported(statement, resolved_span, *, header_context: str) -> list[float]` (unsupported values), `field_evidence_matches(evidence: FieldEvidence, citation: dict) -> bool`, `header_context(source_text, start_char, *, lines_up=8) -> str`

- [ ] **Step 1: Failing tests**

```python
from app.agent_workflow.claim_numeric import numbers_supported, header_context, field_evidence_matches
from app.agent_workflow.claim_units import FieldEvidence

def test_reject_when_statement_digits_missing_from_resolved_span():
    # Resolved span has 16,128; statement claims 16,182
    bad = numbers_supported("Revenue was 16,182 million.", "Total net sales were 16,128 for fiscal 2024.")
    assert bad  # non-empty unsupported

def test_accept_when_digits_in_resolved_span():
    assert numbers_supported("Revenue was 16,128 million.", "Total net sales were 16,128 for fiscal 2024.") == []

def test_header_context_walk_up():
    text = "In millions\nItem A\nTotal net sales were 16,128 for fiscal 2024.\n"
    start = text.index("Total net sales")
    ctx = header_context(text, start, lines_up=8)
    assert "In millions" in ctx

def test_field_evidence_matches_price_citation():
    citation = {"source_type": "saved_price", "figures": "trading_date 2024-01-02 close 192.5", "quote": "192.5"}
    ev = FieldEvidence("field", {"trading_date": "2024-01-02", "close": 192.5})
    assert field_evidence_matches(ev, citation)
```

- [ ] **Step 2–4: Implement until PASS** — unit scaling (百万→亿) can be Phase 1.1 if walk-up alone is insufficient; document follow-up test `test_millions_header_allows_yi_scaling` as optional in same file once rules are coded.

- [ ] **Step 5: Commit** `feat: verify claim numbers on resolved spans and field bindings`

---

### Task 4: `reviewed_claims` gate

**Files:**
- Modify: `app/agent_workflow/evidence.py` (add functions at end; do not change default `reviewed_answer` behavior)
- Test: `tests/test_reviewed_claims.py`

**Interfaces:**
- Consumes: `parse_claims`, `bind_quote`, `numbers_supported`, `field_evidence_matches`
- Produces: `reviewed_claims(claims, citations, source_bodies: dict[int,str], *, min_chars=24) -> tuple[list[ClaimUnit], list[dict]]`  
  Each issue: `{id, reason: quote_too_short|quote_not_found|unsupported_numeric|bad_ordinal|field_mismatch|derived_requires_calculate, ...}`

Rules to encode:
- Missing ordinal → issue `bad_ordinal`, drop
- QuoteEvidence: bind fail → drop (reasons above); on success replace evidence quote with resolved span for downstream
- Significant numbers in statement not in resolved span (+ header_context) → `unsupported_numeric`, drop
- Statement looks like derived rate (growth/margin keywords) without calculation citation → `derived_requires_calculate` (keyword list minimal; refine with fixtures)
- FieldEvidence: `field_evidence_matches` or drop

- [ ] Tests: not_found drops; short drops; fuzzy resolved numeric; field ok; flags do not affect `reviewed_answer` tests

- [ ] Commit: `feat: add reviewed_claims gate using resolved quotes`

---

### Task 5: Feature flags helper

**Files:**
- Create: `app/agent_workflow/claim_gate_flags.py`
- Test: `tests/test_claim_gate_flags.py`

```python
# Mode: off | shadow | enforce
import os

def _mode(name: str, default: str = "off") -> str:
    v = (os.environ.get(name) or default).strip().lower()
    return v if v in {"off", "shadow", "enforce"} else default

def claim_quote_gate_mode() -> str: return _mode("CLAIM_QUOTE_GATE")
def claim_jev_support_mode() -> str: return _mode("CLAIM_JEV_SUPPORT")
def claim_quote_min_chars() -> int: return int(os.environ.get("CLAIM_QUOTE_MIN_CHARS") or "24")
# plus timeout, max attempts=1, max per turn, threshold, daily cap readers
```

- [ ] Commit: `feat: add claim quote/jev gate feature flags`

---

### Task 6: Wire into `direct.py` (shadow first)

**Files:**
- Modify: `app/agent_workflow/direct.py` (answer check ~1820)
- Test: extend `tests/test_agent_answer_repair.py` or add `tests/test_claim_gate_direct_shadow.py`

**Behavior:**
- If `CLAIM_QUOTE_GATE=off`: identical to today.
- If `shadow`: after model answer, if claims JSON present, run `reviewed_claims` and write audit (`claim_gate_shadow`); **still** apply `reviewed_answer` for user-visible keep/drop.
- If `enforce`: `reviewed_claims` decides drops; run legacy guards only if `LEGACY_PROSE_GUARDS=on` as warnings in audit.
- Collect `source_bodies` from citation chunk/document text already on the turn; fall back to `citation["quote"]` only if full body unavailable (audit `source_body_fallback`).
- Shadow metrics: elapsed ms for claim parse+bind, claim counts, drop reasons — store on `self.audit`.

- [ ] Test: env off → existing repair test unchanged  
- [ ] Test: shadow with deliberate bad quote records audit drop reason but legacy path still controls kept text (construct minimal Direct harness as existing tests do)

- [ ] Commit: `feat: wire claim quote gate shadow/enforce into direct workflow`

---

### Task 7: Repair prompt for quote failures

**Files:**
- Modify: `app/agent_workflow/direct.py` repair block (~1883+)

- When enforce (or shadow preparing enforce): if `last_rejected` from claim gate includes `quote_not_found` / `quote_too_short`, repair message asks for a longer verbatim substring from `[n]` source, not only unsupported numbers.
- Derived_requires_calculate → tell model to call calculate or drop the rate claim.

- [ ] Test: repair messages contain quote guidance when claim issues present  
- [ ] Commit: `fix: repair prompts ask for source-verbatim quotes`

---

### Task 8: Prompt / schema for emitting claims

**Files:**
- Modify: answer system prompt path in `direct.py` (or `models.py` if schema lives there)

- When `CLAIM_QUOTE_GATE` in `{shadow, enforce}`, append instructions: emit trailing JSON block `{"claims":[...]}` with QuoteEvidence or FieldEvidence per design; prose may remain for UX.
- Parser: extract last JSON object from answer; if missing in enforce → treat factual sentences as needing repair once; in shadow → audit `claims_missing`.

- [ ] Commit: `feat: prompt models for structured claim evidence when gate enabled`

---

### Task 9: Phase-1 acceptance fixtures

**Files:**
- Create: `tests/fixtures/claim_gate/` (small JSON cases)
- Create: `tests/test_claim_gate_acceptance.py`

Cases must include:
- `15:00` / duration false-reject regressions from hardcoding unblock
- Digit typo fuzzy → reject wrong number via resolved span
- Too-short exact digit
- saved_price FieldEvidence success
- calculate-backed margin vs bare margin fail

- [ ] Commit: `test: claim gate acceptance fixtures for phase 1`

---

### Task 10: Jev claim-support client (tight budget)

**Files:**
- Create: `app/jev_claim_support.py`
- Test: `tests/test_jev_claim_support.py` (mock urllib/http)

**Interfaces:**
- Produces: `ClaimSupportResult(choice, probabilities, status: ok|timeout|http_error|daily_cap|skipped)`, `evaluate_claim_support(quote: str, claim: str) -> ClaimSupportResult`
- Hard: `MAX_ATTEMPTS=1`, timeout from `CLAIM_JEV_TIMEOUT_SECONDS` (default 5)
- `QUESTION_VERSION = "jev-claim-support-v1"`
- Daily cap: read counter from env path or simple PG/redis if project already has one; v1 may use in-process + file under `artifacts/claim-jev-daily.json` with date key (document limitation for multi-worker)
- **Never** import forecast learning writers

- [ ] Tests: attempts==1 on 500; timeout → status timeout; does_not_support vs supports parsing; daily cap short-circuit

- [ ] Commit: `feat: add tight-timeout Jev claim-support client`

---

### Task 11: Wire Jev shadow + enforce semantics

**Files:**
- Modify: `app/agent_workflow/evidence.py` or `direct.py` after successful quote/field bind
- Test: `tests/test_claim_jev_gate.py`

Rules:
- `CLAIM_JEV_SUPPORT=off`: no calls
- `shadow`: call (within caps), audit only
- `enforce`: only `does_not_support` drops claim; timeout/error/unrelated/below T → `semantic_unchecked` audit, claim **kept** if quote gate passed
- Cap per turn; batch multiple questions in one HTTP body when >1 claim

- [ ] Commit: `feat: optional Jev support check with failure isolation`

---

### Task 12: Docs + labelled-set gate for Jev enforce

**Files:**
- Modify: `docs/jev-integration.md` (new section Claim support)
- Create: `docs/claim-support-label-protocol.md` (how to label, accuracy bar placeholder)
- Create: `tests/fixtures/claim_jev_labels/` (empty README + 1 example)

- [ ] Document: alpha endpoint, 1×5s, server key cost, no forecast learning mix
- [ ] Enforce checklist: “do not set CLAIM_JEV_SUPPORT=enforce until labelled metrics recorded in docs/reports/”

- [ ] Commit: `docs: claim-support Jev contract and label protocol`

---

### Task 13: Phase checklist / manual verify

- [ ] Flags off: `pytest tests/test_agent_evidence_v2.py tests/test_agent_answer_repair.py tests/test_agent_hardcoding_unblocking_v1.py -q`
- [ ] `CLAIM_QUOTE_GATE=shadow` on a local turn: audit keys present; latency field present
- [ ] `CLAIM_QUOTE_GATE=enforce` on fixture pack green
- [ ] Direction: `pytest tests/test_jev_provider.py -q` unchanged
- [ ] Commit any flag default docs in `.env.example` if project maintains one

---

## Execution order

Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 (Phase 1 complete), then 10 → 11 → 12 → 13 (Phase 2/3 prep). Do not enable `CLAIM_JEV_SUPPORT=enforce` in this plan’s commits.

## Spec coverage check

| Spec item | Task |
| --- | --- |
| Resolved-span numerics | 2, 3, 4 |
| Unit walk-up + calculate for derived | 3, 4, 7 |
| FieldEvidence | 1, 3, 4, 9 |
| Min length; not-found → drop | 2, 4, 7 |
| Shadow latency/quality metrics | 6, 9 |
| Jev 1× short timeout, daily cap, isolation | 10, 11, 12 |
| Direction Jev untouched | 10, 13 |
| Flags default off | 5, 6, 13 |
