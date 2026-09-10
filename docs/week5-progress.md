# Week 5: versioned offline inference

## Completed scope

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

## Corrections preserve the earlier prediction

A correction creates a second `forecast_snapshots` row; it never changes the
earlier row. The small `forecast_revisions` side table stores the child ID,
its single parent, the root ID, and a required short reason. The database
allows only one child per parent, which makes every history linear. Existing
snapshots with no revision link remain version-one roots.

~~~bash
.venv/bin/python -m app.forecast_archive \
  --model-dir artifacts/week4-2026-09-10 \
  --features exports/week3-features-2026-09-04.json \
  --symbol AAPL \
  --trading-date 2026-09-04 \
  --revises PUT_THE_LATEST_SNAPSHOT_ID_HERE \
  --reason 'New reviewed feature export after source correction'
~~~

`--revises` and `--reason` are an all-or-nothing pair. A child must keep its
parent's symbol, feature source, and snapshot mode; its feature date and
cutoff cannot be earlier. Any snapshot in a chain can retrieve its complete
root-to-latest history:

~~~bash
curl -s http://127.0.0.1:8000/forecast-snapshots/PUT_A_SNAPSHOT_ID_HERE/timeline
~~~

The response has `root_snapshot_id` and ordered `snapshots`. Each entry holds
the complete archived snapshot alongside `version`, `parent_snapshot_id`, and
`revision_reason`.

## Replay from the saved input

Replay needs the retained original Week 4 artifact directory and one archived
snapshot ID:

~~~bash
.venv/bin/python -m app.forecast_replay \
  --snapshot-id PUT_A_SNAPSHOT_ID_HERE \
  --model-dir artifacts/week4-2026-09-10
~~~

It reloads only the saved six feature values in their fixed order. It does not
need the original feature export or the market-price tables, and it neither
re-trains nor writes to PostgreSQL. Before the trusted local `joblib` payload
is deserialized, replay checks the persisted model and manifest SHA-256 values.
It also validates the saved model/feature provenance and finite probabilities.

The JSON result contains `id`, `model_version`, `stored_probabilities`,
`replayed_probabilities`, and `matches`. A mismatch is a result to inspect:
the command prints it and exits 1 without changing the snapshot. Missing,
altered, or incompatible artifacts fail cleanly.

## Local acceptance evidence

On 2026-09-10, the command archived the AAPL 2026-09-04 row as snapshot
`56b452a4-2db3-4c7e-9be1-e1c483de6a3c`. A local HTTP `GET` returned the exact
six stored feature values, three stored probabilities, and model/manifest/export
hashes. An unknown valid UUID returned 404 and a malformed UUID returned 422.
The later local acceptance run appended revision
`c1152656-44fd-4a0f-9b5f-9f87da843139` with the explicit reason
`Manual revision acceptance with unchanged inputs; no new market evidence`.
Both root and child replayed with `matches: true`. A local timeline request from
either ID returned the same root-to-version-two sequence with the correct
parent and reason. The root row's MD5 remained
`0fd31f2204795f7b7ab9c0b9c8db3990`; legacy `forecasts` (52 rows),
`market_prices` (4,536 rows), and `market_price_revisions` (4,536 rows) were
unchanged. The acceptance run left two snapshot rows and one revision link. A
second correction that tried to revise the already-revised root failed with
exit code 2 and created no orphan record.

The full automated suite passed: 72 tests, with 10 pre-existing dependency
deprecation warnings. `pip check` and `git diff --check` also passed.

## Boundaries still outstanding

This is an offline archival path, not online model serving. `POST /forecasts`
remains `mock-v1`. The model is an experimental Week 4 evaluation artifact;
the compatible local export currently uses `historical_research`, so this does
not demonstrate a prospective live prediction.

The application has no snapshot update or delete operation, but it does not
yet enforce database-level immutability. Online serving, a data-refresh
workflow, automatic re-evaluation on data revisions, and monitoring remain for
later work.

The model artifact and local export are ignored by Git. The archive command
only accepts a trusted local `joblib` artifact; serialized model files are not
safe to accept from untrusted callers.
