# Week 5 progress: archived offline inference

## Completed in this slice

`app.forecast_archive` loads one trusted local Week 4 calibrated model and one
compatible Week 3 JSON feature export. It selects an existing stock feature
row, calculates bearish, neutral, and bullish probabilities, and appends a
`forecast_snapshots` record. `GET /forecast-snapshots/{id}` returns that exact
saved record.

Each record stores the six numerical feature inputs, their trading date and
export cutoff, feature version/source/snapshot mode, model version, model and
manifest SHA-256 values, input-export SHA-256, probabilities, and creation
time. The command rejects a malformed artifact, a feature-contract mismatch,
a non-unique or non-finite feature row, and an inference date before the
artifact's conservative availability bound. For the current last-fold model,
that bound is 2026-04-09.

The archive export does not have to be byte-identical to the older export used
to train the artifact. It must meet the same feature version, source, and
snapshot-mode contract; its own hash is stored with the new record.

~~~bash
.venv/bin/python -m app.forecast_archive \
  --model-dir artifacts/week4-2026-09-10 \
  --features exports/week3-features-2026-09-04.json \
  --symbol AAPL \
  --trading-date 2026-09-04
~~~

The command prints the snapshot ID. Retrieve the archived record from a local
running API with:

~~~bash
curl -s http://127.0.0.1:8000/forecast-snapshots/PUT_THE_PRINTED_ID_HERE
~~~

## Local acceptance evidence

On 2026-09-10, the command archived the AAPL 2026-09-04 row as snapshot
`56b452a4-2db3-4c7e-9be1-e1c483de6a3c`. A local HTTP `GET` returned the exact
six stored feature values, three stored probabilities, and model/manifest/export
hashes. An unknown valid UUID returned 404 and a malformed UUID returned 422.
The run appended one snapshot and left the pre-existing `forecasts`,
`market_prices`, and `market_price_revisions` records unchanged.

## Boundaries still outstanding

This is an offline archival path, not online model serving. `POST /forecasts`
remains `mock-v1`. The model is an experimental Week 4 evaluation artifact;
the compatible local export currently uses `historical_research`, so this does
not demonstrate a prospective live prediction.

The application has no snapshot update or delete operation, but it does not
yet enforce database-level immutability. A version/revision timeline, replay
from stored inputs, online serving, data-refresh workflow, and re-evaluation
on data revisions remain for the rest of Week 5 and later work.

The model artifact and local export are ignored by Git. The archive command
only accepts a trusted local `joblib` artifact; serialized model files are not
safe to accept from untrusted callers.
