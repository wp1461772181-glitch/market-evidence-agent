# Claim-support labels

`labels.json` is the scored set (48 rows: supports / does_not_support / unrelated). Score it with `scripts/score_claim_jev_labels.py`; the latest report is `docs/reports/2026-10-27-claim-jev-support-metrics.md`. The protocol is `docs/claim-support-label-protocol.md`.

The rows were written by an agent on 2026-10-27 and have not had a human second pass. Add rows from real shadow traffic as they come in, with `origin: "shadow"`.

Do not set `CLAIM_JEV_SUPPORT=enforce` until a run of `--live --check` against Jev exits 0 and its output is recorded in the report. The local checker's numbers do not count.

`example.json` only shows the row shape.
