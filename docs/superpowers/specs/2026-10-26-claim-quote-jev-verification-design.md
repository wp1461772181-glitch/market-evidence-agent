# Claim + Source Quote + Jev Verification Design

**Status:** draft for review (revised after reviewer notes)  
**Date:** 2026-10-26  
**Scope:** Agent chat answer faithfulness gate (`app/agent_workflow/evidence.py` + `direct.py`), not the ResearchBrief → direction Jev path.

## Goal

Replace brittle prose scraping (regex number/event guards that cause false rejects) with:

1. Structured claim units that carry a **supporting quote** (or a structured field binding for non-prose sources).
2. Server-side proof that the quote binds into the cited **source text**, then verify numbers against the **resolved source span** (not the model-supplied string).
3. Optional semantic check via the existing third-party **Jev** Decisions API (separate question version from `jev-direction-v1`), with strict latency/cost and failure isolation.

## Non-goals

- Do not change bullish/neutral/bearish forecast Jev (`QUESTION_VERSION = jev-direction-v1`).
- Do not mix claim-support Jev outcomes into forecast learning / calibration stats.
- Do not rewrite retrieval, Milvus, or checkpointing.
- Do not require Jev for every turn on day one (feature flag + shadow mode).
- Do not delete the current regex guards overnight.

## Current path (baseline)

1. Tools populate `citations` with ordinals and `quote` excerpts (`number_rows`).
2. Model writes free-form prose with `[n]` markers.
3. `reviewed_answer` / `reviewed_passages` scan lines for:
   - `invalid_citation` / `missing_citation`
   - `unsupported_numeric_claim` via `unsupported_numbers` (mine digits from prose → match against citation quote/figures)
   - `unsupported_claim` via event lexicon + local negation polarity
4. Failures → drop/trim lines → model `repair` with rejected numbers named → re-check.
5. Direction Jev is unrelated and only runs later on ResearchBrief.

Pain: guards must infer claims from prose; event lexicon and time-like numbers (e.g. `15:00`) cause false rejects; no true entailment check.

## Prior art already in this repo (reuse)

| Piece | Location | Reuse |
| --- | --- | --- |
| Fact + citation quote schema | `app/material_analysis_schema.py` (`Fact`, `Citation`) | Shape for claim units |
| Exact → NFKC/normalize → fuzzy locate | `resolve_citation` / `locate_material_analysis_citations` | Quote-in-source proof; returns **source span text** |
| Exact substring claim quotes | `app/research_workflow.py` | Stricter mode option |
| Jev OpenRouter adapter | `app/jev_provider.py` | New thin support-check caller; **do not copy** its 3×30s retry budget |
| Answer review + repair loop | `app/agent_workflow/direct.py` (~1820+) | Wire new review path |
| Guard regression tests | `tests/test_agent_evidence_v2.py`, `test_agent_hardcoding_unblocking_v1.py`, `test_agent_answer_repair.py` | Keep green; add parallel suites |

## Approaches considered

### A — Prompt-only “copy the quote into prose”

Ask the model to embed quotes in markdown; keep regex.  
**Reject:** still scraping; weak contract; same false-reject class.

### B — Structured claims + quote locate only (no Jev)

Model emits claim list; server verifies quote binds into source; numbers checked on **resolved source text** (with unit/header walk-up and calculate for derived rates). Structured sources use field+value bindings.  
**Pros:** cheap, deterministic, reuses material-analysis locator.  
**Cons:** no semantic support (entity mix-ups survive if digits match).

### C — Structured claims + quote locate + Jev support check (recommended)

B, then a **tightly budgeted** Jev `choice` on (resolved quote, claim) with its own `question_version`.  
**Pros:** matches handbook Q54; reuses existing Jev integration surface.  
**Cons:** OpenRouter alpha latency/cost; server-paid key; needs labelled accuracy before enforce.

**Recommendation: C**, shipped as **Phase 1 = B behind a flag**, **Phase 2 = Jev shadow on a labelled set**, **Phase 3 = Jev enforce** only after accuracy bar is met.

## Target data flow

```
tools → citations (unchanged)
     → model answer
         preferred: AnswerPayload {
           prose?,
           claims: [{
             id, statement, citation_ordinal,
             evidence: QuoteEvidence | FieldEvidence
           }]
         }
         fallback: legacy prose-only (old guards, flagged in audit)

for each claim:
  1. ordinal must exist in citations
  2. bind evidence:
       QuoteEvidence → resolve_citation(model_quote, source_body)
         - min quote length (see Quote bind rules)
         - unverified / not found → DROP claim + repair (not “pending”)
         - fuzzy/normalized hit → REPLACE model_quote with resolved source span text
       FieldEvidence → bind field_name + value against structured citation payload
  3. numeric check on **resolved source material only**:
       - figures must appear in resolved span text (never in the raw model-copied quote alone)
       - walk upward from span for table/header unit context (e.g. “In millions”)
       - derived rates (growth, gross margin, …) must come from calculate tool results, not free invention
  4. if CLAIM_JEV_SUPPORT enabled and bind+numeric ok:
       Jev once (tight timeout); see Jev support contract
  5. render user-visible prose from accepted claims (+ optional model polish)

audit fields: claim_count, quote_match_kinds, resolved_span_hashes,
  jev_support_calls, jev_skipped_reasons, drop_reasons, latency_ms, answer_quality_notes
```

## Quote bind rules (reviewer notes 1, 2, 4)

### Numbers from the source span, not the model copy

`resolve_citation` at fuzzy similarity ≥ 0.95 can accept a near-miss copy (e.g. model writes `16,182` for source `16,128`). **All numeric verification MUST use the returned source span text** (`resolved["quote"]` / `text[start:end]`), never the model’s `evidence_quote` string alone. After a non-exact bind, persist and display the **resolved** quote.

### Units and derived figures

- Example: claim says `161.28 亿美元` while the cell is `16,128` and the table header says `In millions`. After locating the span, **walk upward in the source** (same table / nearby header lines) to recover unit context before accepting scaled forms.
- Spec the walk-up window in implementation (e.g. previous N lines or DOM/table header if structured HTML/PDF text is available).
- **Derived** quantities (YoY growth, gross margin, differences, …) are valid only when backed by an accepted `calculate` (or equivalent) citation / observation — not by quoting a raw cell alone.

### Minimum quote length and failure classes

`resolve_citation` today only enforces length ≥ 12 on the **fuzzy** path; exact match has no floor, so a lone `16,128` can “hit.” For the chat gate:

| Rule | Value |
| --- | --- |
| Minimum `evidence_quote` length (all match kinds) | **≥ 24 characters** after strip (config `CLAIM_QUOTE_MIN_CHARS`; tune with fixtures) |
| Exact / normalized / fuzzy | All must meet the min length before locate |
| Fuzzy similarity floor | Keep material-analysis **0.95** |

| Bind outcome | Gate action |
| --- | --- |
| Not found / unverified | **Drop** the claim (fabricated basis); trigger repair round — **not** “待核对” |
| Exact / normalized / fuzzy OK | Keep; **replace** stored quote with resolved source span |
| Too short | Reject as invalid evidence shape; repair |

## Structured sources without prose quotes (reviewer note 3)

These citation kinds often have no natural-language passage. Requiring `evidence_quote` would fail them all. Use **field binding** instead:

| Source kind (examples) | Evidence shape |
| --- | --- |
| Saved price / OHLCV | `{ "type": "field", "fields": { "trading_date": "...", "close": ... } }` matching citation figures |
| XBRL / structured filing numbers | `{ "type": "field", "concept": "...", "period": "...", "value": ... }` (or project’s existing fact keys) |
| Saved forecast rows | `{ "type": "field", "forecast_id"/"version": "...", "metric": "...", "value": ... }` |
| `calculate` results | `{ "type": "field", "calculation_id" or citation ordinal, "value": ... }` must match the tool output |

`QuoteEvidence` remains for web text, markdown/PDF passages, FAQ chunks, etc. The schema is a tagged union; the gate dispatches on `type`.

## Shadow / “record only” is not zero-impact (reviewer note 5)

Even when `CLAIM_QUOTE_GATE=shadow` (legacy still decides user-visible accept/drop):

- The prompt already asks for claims + quotes → **longer outputs**, different answer distribution.
- Measure in shadow reports: **latency**, **token/size**, **answer quality** (spot rubric or side-by-side), **not only** bind hit-rate vs legacy disagreement.
- Optional: shadow can parse claims from a **parallel** model call only if we later need zero prompt change; v1 accepts prompt change and measures it honestly.

## Jev support contract (reviewer note 6; do not overload direction)

### Reality of the existing adapter

- Endpoint is OpenRouter **alpha**: `https://openrouter.ai/api/alpha/decisions` (`jev_provider.py`).
- Direction provider: up to **3 attempts**, **30s** timeout each → worst case ~**90s** extra latency.
- Uses the **server’s** `OPENROUTER_API_KEY` — guest turns are billed to the operator.

### Claim-support caller (separate module)

- **`question_version`:** `jev-claim-support-v1` (never `jev-direction-v1`).
- **Question type:** `choice`: `supports` | `does_not_support` | `unrelated`.
- **`state`:** `{ "quote": <resolved span>, "claim": <statement> }` only.
- **HTTP budget (hard):** **1 attempt**, timeout **~3–5s** (config); **no** 3×30s retry copy from direction provider.
- **Per-day cap:** operator-level daily max calls (config); over cap → skip Jev, audit `jev_skipped: daily_cap`.
- **Per-turn cap:** still `CLAIM_JEV_MAX_PER_TURN` (default lower, e.g. 4).
- **Batching:** prefer one Decisions request with multiple named questions when several claims need support checks (within body size limits).

### Gate semantics (failure isolation)

| Jev outcome | Effect on answer |
| --- | --- |
| `supports` and P ≥ T | Accept (quote path already passed) |
| Explicit `does_not_support` | **Downgrade / drop** that claim only |
| `unrelated`, low confidence, below T | Audit only in shadow; in enforce → treat as **semantic unchecked** (do **not** paint whole answer 待核对) |
| HTTP / timeout / validation / daily cap | Audit `semantic_unchecked`; **leave** quote-gate decision intact — OpenRouter blips must not degrade every reply |

### Preconditions to enable enforce

- Freeze a **labelled** claim-support set; measure precision/recall (or agreement) for `does_not_support` vs humans.
- Enable `CLAIM_JEV_SUPPORT=enforce` only after meeting the agreed accuracy bar.
- Results **must not** enter forecast / Jev direction learning or calibration pipelines.

### Logging

Request id, latency, choice, probs, input sha256, skip reasons; never API key or raw upstream error bodies.

## Feature flags

| Env | Default | Meaning |
| --- | --- | --- |
| `CLAIM_QUOTE_GATE` | `off` | `off` \| `shadow` \| `enforce` |
| `CLAIM_QUOTE_MIN_CHARS` | `24` | min evidence quote length |
| `CLAIM_JEV_SUPPORT` | `off` | `off` \| `shadow` \| `enforce` |
| `CLAIM_JEV_SUPPORT_THRESHOLD` | `0.7` | min P(supports) when using supports path |
| `CLAIM_JEV_TIMEOUT_SECONDS` | `5` | single-attempt timeout |
| `CLAIM_JEV_MAX_ATTEMPTS` | `1` | hard 1 for claim support |
| `CLAIM_JEV_MAX_PER_TURN` | `4` | cap |
| `CLAIM_JEV_DAILY_CAP` | (set in ops) | server-wide daily max |
| `LEGACY_PROSE_GUARDS` | `on` | keep regex until shadow metrics OK |

**Shadow (quote):** new path runs + audits; legacy still decides. Track latency and quality, not only hit rate.  
**Enforce (quote):** bind/numeric/field rules decide drop vs keep.  
**Shadow (Jev):** log only.  
**Enforce (Jev):** only explicit `does_not_support` downgrades; errors → semantic unchecked.

## File plan

| File | Change |
| --- | --- |
| `app/agent_workflow/claim_units.py` (new) | Parse/validate claim list; `QuoteEvidence` \| `FieldEvidence` |
| `app/agent_workflow/quote_bind.py` (new) | Wrap `resolve_citation`; min length; return resolved span; unit walk-up helper |
| `app/jev_claim_support.py` (new) | Tight-timeout Jev support adapter; own question_version; daily cap hook |
| `app/jev_provider.py` | **Unchanged** direction path (3×30s remains forecast-only) |
| `app/agent_workflow/evidence.py` | `reviewed_claims(...)`; keep `reviewed_answer` for legacy |
| `app/agent_workflow/direct.py` | Flag wiring; repair for missing/bad quotes; calculate required for derived metrics |
| `app/agent_workflow/models.py` (or prompts) | Answer schema including field bindings |
| `docs/jev-integration.md` | Second question version + latency/cost warnings |
| `tests/test_claim_quote_gate.py` (new) | Resolved-span numbers; fuzzy digit-typo; min length; field bindings |
| `tests/test_jev_claim_support.py` (new) | 1-attempt timeout; does_not_support vs error isolation |
| `tests/test_agent_evidence_v2.py` | Unchanged until enforce; then retire event-lexicon cases |

## Phased delivery

### Phase 0 — Spec freeze (this doc)

Agree min quote length, pending vs drop UX, field-binding shapes per source kind, Jev accuracy bar.

### Phase 1 — Quote + field gate (`CLAIM_QUOTE_GATE=shadow` → `enforce`)

- Claims with `QuoteEvidence` or `FieldEvidence`.
- Bind via `resolve_citation`; numbers on **resolved** span; unit walk-up; calculate for derived rates.
- Not-found → drop + repair.
- Shadow metrics: bind rate, **latency**, **answer quality**, legacy disagreement (incl. `15:00` / translation fixtures).

### Phase 2 — Jev shadow on labelled set

- Wire `jev-claim-support-v1` with 1×~5s budget and daily cap.
- Score against labels; **no** user-visible downgrade except logging.
- Confirm isolation from forecast learning stats.

### Phase 3 — Jev enforce (conditional)

- Only if Phase 2 accuracy bar met.
- Downgrade **only** on explicit `does_not_support`.
- Errors / unrelated / below T → `semantic_unchecked`, not whole-answer 待核对.
- Demote event lexicon; soften obsolete numeric false-positive rules.

### Phase 4 — Docs

- Update agent workflow docs; keep forecast Jev “unverified” language untouched.

## Repair behavior change

| Today | Target |
| --- | --- |
| Name unsupported numbers; rewrite prose | Name claims with bind failure / too-short quote / field mismatch; ask for a quote that appears in source or a valid field binding |
| Soft “pending” for fake quotes | **Drop** unverifiable quote claims and repair |
| — | Derived metric without calculate → reject that claim |

## Testing strategy

1. Fuzzy near-miss digits: model quote `16,182`, source `16,128` → bind may succeed → numeric check on **resolved** `16,128` must reject claim that asserts `16,182`.
2. Unit walk-up: cell `16,128` + header `In millions` accepts `161.28亿` form only with header context rules as specified in tests.
3. Min length: exact `16,128` alone fails gate.
4. Not found → drop + repair signal (not pending).
5. FieldEvidence for price / calculate citations passes without prose quote.
6. Jev mock: `does_not_support` drops claim; timeout/5xx → semantic_unchecked, answer otherwise intact; attempts==1.
7. Flags off: existing evidence/repair tests bit-identical.
8. Shadow pack reports latency and quality notes, not only hit rate.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Fuzzy accepts typo’d numbers | Verify figures on resolved span only |
| Unit mismatch (millions vs 亿) | Header walk-up + explicit tests |
| Structured sources all fail | FieldEvidence union |
| Short token quotes | Min length on all match kinds |
| Shadow skews answers | Measure latency + quality |
| OpenRouter alpha slow/down | 1 attempt, short timeout, daily cap; errors ≠ whole-answer degrade |
| Guest cost on server key | Daily cap; enforce only after labelled accuracy |
| Contaminating forecast learning | Separate module + explicit non-goal |

## Success criteria

- Hardcoding false-reject fixtures pass under quote enforce without lexicon growth.
- Flags off: existing tests unchanged.
- Shadow report includes bind rate, **latency**, **quality**, and legacy disagreement.
- Direction Jev path and `probe_jev.py` unchanged; claim-support metrics never enter forecast calibration.
- Jev enforce ships only after labelled accuracy bar; runtime failures do not mass-degrade answers.

## Open decisions (narrowed)

1. Exact `CLAIM_QUOTE_MIN_CHARS` (default proposal **24**) after fixture pass.
2. Phase 1 answer shape: JSON-only vs prose + claims block.
3. Labelled-set size and accuracy bar for Jev enforce (propose before Phase 2).
4. Whether user-visible copy ever says “语义未核对” or stays silent in audit only.

## Revision log

- **2026-10-26 (r2):** Incorporated six reviewer constraints: resolved-span numerics; unit walk-up + calculate for derived; FieldEvidence for structured sources; min quote length + not-found→drop; shadow measures latency/quality; Jev 1× short timeout, daily cap, error≠pending whole answer, labelled bar before enforce, isolate from forecast learning.
