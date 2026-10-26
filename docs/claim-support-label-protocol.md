# Claim-support label protocol

Claim-support Jev (`jev-claim-support-v1`) may run in shadow as soon as `CLAIM_QUOTE_GATE` and `CLAIM_JEV_SUPPORT` are set to `shadow`. It must not run in enforce until a labelled set has been scored.

## What to label

Each row is one claim that already passed quote or field binding. Label the **resolved source span** (or the structured field binding), not the model's original quote string.

| Field | Meaning |
| --- | --- |
| `id` | Stable id |
| `quote` | Resolved span, or the structured citation text for field evidence |
| `claim` | The statement shown to the user |
| `label` | `supports`, `does_not_support`, or `unrelated` |
| `notes` | Optional. Why a human chose the label |

`supports`: the span states the claim, including the entities and the figures.
`does_not_support`: the span contradicts the claim or does not state it (a digit typo that survived fuzzy bind belongs here).
`unrelated`: the span is about a different fact.

Seed rows live in `tests/fixtures/claim_jev_labels/`. That directory is an example, not a scored set.

## Accuracy bar

Record precision and recall (or human agreement) for `does_not_support` in `docs/reports/` before any enforce rollout. The numeric bar is not set in this change. Until that report exists:

- Keep `CLAIM_JEV_SUPPORT` at `off` or `shadow`.
- Do not set `CLAIM_JEV_SUPPORT=enforce` in committed defaults, deployment config, or `.env.example`.

Shadow reports must include latency and answer-quality notes, not only bind hit rate. Claim-support results stay out of forecast / Jev direction learning and calibration.

## How to turn shadow on

```sh
CLAIM_QUOTE_GATE=shadow
CLAIM_JEV_SUPPORT=shadow
```

Leave `CLAIM_JEV_DAILY_CAP` set to an operator limit before shadow traffic is real. The file counter under `artifacts/claim-jev-daily.json` is per process.
