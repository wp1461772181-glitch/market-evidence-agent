# Claim-support Jev (`jev-claim-support-v2`) — labelled-set metrics, 2026-10-27

## Update: live v1 failed; v2 awaits a Mac re-run

The first live run (Mac, `jev-claim-support-v1`, 48 rows) exited 1: does_not_support precision 48.6% (bar ≥ 90%), recall 85.0%, supports rows dropped 41.2% (bar ≤ 5%), status ok 97.9%. All 11 unrelated rows came back does_not_support.

Cause: v1 sent up to 4 claims in one Decisions request as `state.claims`, with the same question text for each. Nothing tied a question to its list entry, and the probabilities inside a request were nearly the same for unrelated rows. In the first request, `fx-revenue-exact` (gold supports) and `fx-digit-typo` (gold does_not_support) both got about 0.42 / 0.57. The single-claim path used flat state only when the id was `claim`, and the scorer and production pass real claim ids, so they always took the batched path.

v2 (`jev-claim-support-v2`) changes:

- One HTTP request per claim, `state` = `{"quote", "claim"}`, a single question id `claim`. Requests within a turn run concurrently, so a turn still waits at most the 5 s timeout.
- Each request uses one daily-cap slot. Timeout, HTTP error, validation error and the cap fail open for that claim only.
- Criteria reworded. supports: same fact (entity, figure, period, direction), unit headers such as `In millions` / 亿 and ordinary rounding allowed. does_not_support: a conflicting value, period, direction or entity for the same fact. unrelated: a different metric or topic, not a contradiction.

**Status: awaiting the Mac re-live run of v2. `CLAIM_JEV_SUPPORT` stays shadow until both commands below exit 0.** The live command now makes 48 requests instead of 12.

## Earlier status (before the live run)

Status: **the labelled set and the scorer are in place; Jev itself has not been scored.** This environment has no `OPENROUTER_API_KEY` and no recorded shadow results (`artifacts/` is absent), so every number below comes from the local reference checker, not from Jev. The protocol's requirement is precision and recall *of the Jev check*. That run is one command (see "Live run" below), and the enforce decision should rest on its output.

## Set

`tests/fixtures/claim_jev_labels/labels.json`, 48 rows: supports 17, does_not_support 20, unrelated 11.

- 8 rows come from the `tests/fixtures/claim_gate/` cases (digit typo, millions header with and without the unit line, bare margin, calculate field, saved price, clock/duration).
- 40 rows are written in the style of shadow traffic: price, XBRL and forecast field citations; EN and ZH filing spans; wrong period, quarter or date; scale errors (billion vs million, 亿 vs 万); direction and negation flips; entity and segment swaps; guidance stated as an outcome; over-claims; boilerplate; one prompt-injection quote.
- Labeller: an agent (Claude), 2026-10-27. **No human second pass yet**, so human agreement is not measured. The borderline rows are named under "Answer-quality notes".

Each row records `origin` and `notes`. `example.json` is still only the shape example.

## How the set meets the gate

`scripts/score_claim_jev_labels.py --subset reached` runs each row through `apply_claim_gate(mode="enforce")` and keeps only the rows the quote/field gate hands to Jev. Of 48 rows, **30 reach Jev** (supports 15, does_not_support 13, unrelated 2). The quote gate stops the other 18 first (unsupported_numeric, quote_too_short, derived_requires_calculate), including 7 of the 20 does_not_support rows and 9 of the 11 unrelated rows. Production precision is therefore mostly a question about the 30-row slice. Unrelated spans seldom reach Jev, so the 2-row unrelated count there is too small to say anything.

`tests/test_claim_jev_labels.py::test_enforce_on_labelled_set_drops_only_does_not_support_rows` feeds the gold label back as the Jev answer and checks the enforce gate on every reached row. does_not_support rows are dropped; supports and unrelated rows stay in the kept prose. That test checks the gate's semantics, not Jev's accuracy.

## Proposed bar (the protocol leaves the number open)

| Metric | Bar | Why |
| --- | --- | --- |
| does_not_support precision | ≥ 90% | Enforce drops only on this choice, so precision equals the rate of correct drops |
| does_not_support recall | ≥ 60% | Below this, enforce adds little beyond the quote gate |
| supports rows predicted does_not_support | ≤ 10% | True sentences removed from answers |
| status ok rate | ≥ 90% | Timeouts and errors fail open (kept, `semantic_unchecked`), but a low rate means the check is not running |

These constants sit at the top of `scripts/score_claim_jev_labels.py`. `--check` exits 1 when any one is missed.

## Results: local reference checker (baseline, not Jev)

These rows show the scorer works and give a baseline to compare Jev with. The checker was adjusted on this set to handle two previously mis-classified rows (`sh-segment-swap` and `sh-headcount-ok`). Treat its numbers as optimistic.

### All 48 rows

| Metric | Value | Proposed bar |
| --- | --- | --- |
| does_not_support precision | 100.0% | ≥ 90% |
| does_not_support recall | 100.0% | ≥ 60% |
| supports rows dropped (predicted does_not_support) | 0.0% | ≤ 10% |
| status ok rate | 100.0% | ≥ 90% |
| passes bar | yes | |

| gold \ predicted | supports | does_not_support | unrelated | unchecked |
| --- | --- | --- | --- | --- |
| supports | 17 | 0 | 0 | 0 |
| does_not_support | 0 | 20 | 0 | 0 |
| unrelated | 0 | 0 | 11 | 0 |

### 30 rows that reach Jev after the quote gate

| Metric | Value | Proposed bar |
| --- | --- | --- |
| does_not_support precision | 100.0% | ≥ 90% |
| does_not_support recall | 100.0% | ≥ 60% |
| supports rows dropped (predicted does_not_support) | 0.0% | ≤ 10% |
| status ok rate | 100.0% | ≥ 90% |
| passes bar | yes | |

| gold \ predicted | supports | does_not_support | unrelated | unchecked |
| --- | --- | --- | --- | --- |
| supports | 15 | 0 | 0 | 0 |
| does_not_support | 0 | 13 | 0 | 0 |
| unrelated | 0 | 0 | 2 | 0 |

Disagreements: none.

On this set the local reference checker meets the proposed bar. It is still not a candidate for the production check; this report does not propose it as one.

## Latency

- Local checker: under 1 ms per row; no HTTP.
- Jev: not measured here. Code limits: 1 attempt, timeout ≤ 5 s (`CLAIM_JEV_TIMEOUT_SECONDS` is clamped), at most `CLAIM_JEV_MAX_PER_TURN` (4) claims per answer turn, one concurrent request per claim (v2; v1 batched them into one request). In `direct.py` the call runs inline whether the mode is shadow or enforce, so switching to enforce adds no request. Worst-case added latency per turn stays at 5 s, the same as shadow today. The live run records p50 / p95 / max per request.

## Answer-quality notes

1. **Fixed in this change: unit header now reaches Jev.** Before, `_apply_support` sent only the resolved span. For a table under `In millions`, a correct `营收为 161.28 亿美元` reached Jev as "16,128" with no scale, which is a does_not_support by the protocol's own rule (row `fx-millions-no-header`). In enforce that would have dropped a true sentence. The gate now puts the unit lines from the header above the span (`header_unit_lines`), and only those lines, so other table rows stay out. Tests: `test_quote_sent_to_jev_carries_the_table_unit_header` and `test_quote_without_unit_header_is_sent_as_the_resolved_span`.
2. **Implicit entity (fixed in local checker, watch in Jev).** `sh-headcount-ok` says "the Company"; the claim says "Apple". The local checker previously called this does_not_support; it now ignores "Company" and months as entities. Jev may still be stricter, so watch this row in the live run.
3. **Segment swap (fixed in local checker, watch in Jev).** `sh-segment-swap` puts a real figure on the wrong segment. The local checker now uses clause-level segment-number matching and catches it. Jev should be stronger on this kind of semantic case; the live run will show whether it is.
4. **Borderline labels.** `un-zh-segment` and `un-opex-vs-revenue` are labelled unrelated; a reader could argue for does_not_support. Neither reaches Jev today (the quote gate stops both), so they do not move the reached-slice numbers.
5. **No confidence floor on drops.** Enforce drops on any argmax `does_not_support`, even at P≈0.4. The live run's replay file keeps the probabilities. If false drops cluster at low P, add a drop threshold before widening enforce.
6. **Fail-open paths unchanged.** Timeout, HTTP error, validation error, daily cap and missing key all leave the claim in place as `semantic_unchecked`. The daily cap counter is per process (`artifacts/claim-jev-daily.json`).
7. Claim-support results still stay out of forecast learning and calibration (`app/jev_learning.py` is not imported by the scorer or the gate).

## Live run (required before enforce)

On the deploy host, in the checkout with `.env` loaded:

```sh
set -a; . ./.env; set +a
.venv-rag/bin/python scripts/score_claim_jev_labels.py --live --save-replay artifacts/claim-jev-replay-2026-10-27-v2.json --check
.venv-rag/bin/python scripts/score_claim_jev_labels.py --replay artifacts/claim-jev-replay-2026-10-27-v2.json --subset reached --check
```

The first command makes 48 requests (one per row, 4 at a time), about a minute at most. `daily_cap=None` is passed, so the production daily counter is not used. If both commands exit 0, the bar is met and this report should get a "Live results" section with the two outputs pasted in. If either exits 1, keep `CLAIM_JEV_SUPPORT=shadow`.

## Operator decision (2026-10-27)

Supports-drop bar raised from ≤5% to ≤10% so a single known FYE-paraphrase miss (`sh-headcount-ok`) does not block CLAIM_JEV_SUPPORT=enforce. Precision/recall/ok bars unchanged.
