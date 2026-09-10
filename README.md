# Market Evidence Agent — Weeks 1–5

A FastAPI and PostgreSQL foundation for a market-evidence system. The project stores a deterministic Week 1 mock-v1 forecast, then adds reproducible daily market-data snapshots, leakage-safe Week 3 features, a fixed Week 4 offline baseline evaluation, and Week 5's versioned offline-prediction archive. The API does **not** yet serve the trained baseline.

## Implemented scope

- POST /forecasts creates a deterministic three-class mock-v1 forecast and stores it in PostgreSQL.
- Daily OHLCV retrieval uses the Yahoo Finance Chart endpoint. The initial universe is AAPL, MSFT, GOOGL, AMZN, NVDA, and benchmark SPY.
- Ingestion records its source, normalized UTC cutoff, lifecycle, and the time at which the system observed each provider response.
- market_price_revisions is append-only. A changed provider bar becomes a new numbered, content-hashed revision instead of replacing an earlier value.
- Historical snapshots select the newest revision visible at a chosen cutoff.
- market-features-v1 exports momentum, volatility, volume, drawdown, and relative-market features from a reproducible snapshot.
- Week 4 builds a five-stock, 20-XNYS-session excess-return dataset and evaluates a fixed logistic-regression baseline with time-ordered, label-maturity-purged folds.
- Week 5 archives a prediction from a trusted local Week 4 artifact and compatible Week 3 feature export, preserves later corrections as a linked revision chain, and replays any archived prediction from its saved inputs.

## Time semantics and data-version limits

Daily bars use the actual XNYS session close, including holidays, daylight saving time, and early closes. Cutoffs must be timezone-aware and are normalized to UTC.

Every revision stores two different times:

- available_at: the earliest possible XNYS session close for the bar's trading date;
- observed_at: when this system received that version from its provider.

get_market_data(..., mode="historical_research") uses a controlled research assumption: only an initial imported/backfilled version may be treated as available at its session close. Later provider corrections remain hidden until their observed_at time, so a future correction cannot rewrite an older feature snapshot.

mode="observed" is stricter. It exposes a version only when both its session close and its actual observed_at are no later than the cutoff. Existing Week 2 bars are backfills: their original fetched_at does not prove the exact time that the HTTP response arrived, so migration records their observed_at at migration time. Consequently, an observed-mode export for a historical backfill may contain no rows. That is expected; it is not proof that the system possessed those bars at the historical date.

This project therefore provides reproducible, leakage-controlled research snapshots, but it does not claim to have reconstructed a provider's true historical point-in-time feed.

## Week 3 features

Each feature row uses its trading date and earlier visible bars only. The first 20 bars for a symbol are reported as insufficient_history, rather than being filled or imputed. Stock and SPY sessions must align exactly; missing sessions are reported as skips.

| Feature family | market-features-v1 definition |
| --- | --- |
| Momentum | close_t / close_t-5 - 1 and close_t / close_t-20 - 1 |
| Volatility | Sample standard deviation (ddof=1) of 20 close-to-close returns, annualized by sqrt(252) |
| Volume | volume_t / mean(volume_t-20 ... volume_t-1) |
| Drawdown | close_t / max(close_t-19 ... close_t) - 1 |
| Relative market performance | Stock 20-day return minus SPY 20-day return on exact matching dates |

For a final export cutoff, feature construction reloads the stock and SPY snapshot at **each feature date's own XNYS close**. This prevents later visible bars or revisions from entering an earlier feature row.

## Week 4 offline baseline

The Week 4 experiment is deliberately small: it reuses scikit-learn's
`Pipeline(StandardScaler, LogisticRegression)` and compares it with class-prior,
majority-class, and relative-momentum baselines. Sigmoid calibration is fit on a
separate time block with `FrozenEstimator`, never on the test block.

Labels use the stock's 20-session return minus SPY's return over the same dates.
For a row at `t`, `threshold = max(1e-8, 0.5 * volatility_20d(t) * sqrt(20 / 252))`:
below `-threshold` is bearish (0), within the boundary is neutral (1), and above
`threshold` is bullish (2). The final 20 sessions without a mature label are
excluded.

Three expanding time folds each hold out 84 XNYS sessions for testing. The 63
sessions immediately before each test block are reserved for calibration; rows
whose label was not available before the next block began are purged. See
[the Week 4 evaluation report](docs/week4-evaluation.md) for the fixed design,
actual OOS metrics, and limits.

## Setup

Requires Python 3.12, Docker or OrbStack, and a local PostgreSQL container. The database credentials below are local-development demo values only.

~~~bash
cd market-evidence-agent
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export DATABASE_URL='postgresql+psycopg://market_evidence:market_evidence_dev@localhost:55432/market_evidence'
~~~

Create the local development database once. If the container already exists, run docker start market-evidence-postgres instead.

~~~bash
docker run --name market-evidence-postgres \
  -e POSTGRES_USER=market_evidence \
  -e POSTGRES_PASSWORD=market_evidence_dev \
  -e POSTGRES_DB=market_evidence \
  -p 55432:5432 -d postgres:16
until docker exec market-evidence-postgres pg_isready -U market_evidence -d market_evidence; do sleep 1; done
~~~

Run the single upgrade-and-migration entrypoint once. It creates the current tables, upgrades only the recognized empty pre-release revision-table shape, then appends baseline revisions for existing Week 2 market_prices. It never edits or deletes those legacy price rows; an unexpected populated old schema fails closed.

~~~bash
python -c "from app.market_data_ingestion import migrate_legacy_market_prices; print(migrate_legacy_market_prices())"
~~~

## Run tests and the API

Tests ignore DATABASE_URL. They create a unique disposable PostgreSQL database based on TEST_DATABASE_URL, whose database name must begin with test_, and drop only that created database when the run ends.

~~~bash
source .venv/bin/activate
export TEST_DATABASE_URL='postgresql+psycopg://market_evidence:market_evidence_dev@localhost:55432/test_market_evidence'
pytest -q
uvicorn app.main:app --reload
~~~

Create the current mock forecast:

~~~bash
curl -s -X POST http://127.0.0.1:8000/forecasts \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL"}'
~~~

The forecast API accepts normalized 1–5 letter ASCII symbols and returns HTTP 422 for an invalid symbol.

The checked-in VS Code Run and Debug configuration uses the workspace .venv/bin/python, starts the local PostgreSQL container, then runs uvicorn app.main:app --reload at 127.0.0.1:8000.

## Ingest and export features

Fetch the initial stock universe and SPY into the development database:

~~~bash
python -c "from datetime import UTC, date, datetime; from app.market_data_ingestion import ingest_market_data; print(ingest_market_data(['AAPL','MSFT','GOOGL','AMZN','NVDA','SPY'], date(2023, 8, 31), date(2026, 9, 4), datetime.now(UTC)))"
~~~

Export an explicit historical-research snapshot as JSON. The exports/ directory is intentionally ignored by Git because it can contain local data outputs; retain a reviewed aggregate evaluation summary elsewhere when one is ready to publish.

~~~bash
mkdir -p exports
python -m app.feature_export \
  --as-of-time 2026-09-04T21:00:00Z \
  --symbols AAPL MSFT GOOGL AMZN NVDA \
  --mode historical_research \
  --format json \
  --output exports/features-2026-09-04.json
~~~

The export contains metadata (feature_version, UTC cutoff, source, snapshot mode, formulas, row count, and skipped-row reasons), feature rows, and explicit skips. Use --mode observed when the experiment must use only bars that this system had actually observed by each cutoff.

CSV is also supported with `--format csv`; its first line is a `# metadata=`
JSON comment, so a dataframe reader should use `comment="#"`.

## Run the Week 4 evaluation

The command saves the dataset, out-of-sample predictions, JSON report, model,
and manifest under an ignored artifact directory. Use a fresh or empty output
directory; the command refuses to overwrite an existing run.

~~~bash
.venv/bin/python -m app.training \
  --features exports/week3-features-2026-09-04.json \
  --output-dir artifacts/week4-2026-09-10
~~~

The saved model is the calibrated model from the **last evaluation fold**. It is
for reproduction and inspection only, not an API or full-history production
model. `manifest.json` records the ordered features, class order, library
versions, source-export hash, data metadata, and reload check.

## Week 5: archive, revise, and replay offline predictions

Week 5 deliberately keeps model loading out of the request path. It
loads a **trusted local** Week 4 artifact, selects one existing feature row,
uses the saved model to calculate the three probabilities, and appends the
exact inputs and provenance to `forecast_snapshots`. The record can then be
read through `GET /forecast-snapshots/{id}`.

For the checked local Week 4 artifact and Week 3 export, archive the AAPL row
at the export cutoff as follows:

~~~bash
.venv/bin/python -m app.forecast_archive \
  --model-dir artifacts/week4-2026-09-10 \
  --features exports/week3-features-2026-09-04.json \
  --symbol AAPL \
  --trading-date 2026-09-04
~~~

The command creates additive tables when needed and prints the new ID. Its
record includes the named bearish, neutral, and bullish probabilities; the six
feature values; feature date and cutoff; feature version, source, and snapshot
mode; and SHA-256 values for the model, manifest, and input export. It accepts
only a feature export compatible with the saved model's feature version,
source, and snapshot mode. The selected feature date must be on or after
2026-04-09, the conservative availability boundary recorded by this last-fold
artifact.

### Preserve a correction as a new version

Never edit an archived prediction. To append a correction, run the same archive
command with the latest snapshot ID and a short reason. Both arguments are
required together. The new snapshot must use the same symbol, source, and
snapshot mode, and its feature date and cutoff cannot precede its parent.

~~~bash
.venv/bin/python -m app.forecast_archive \
  --model-dir artifacts/week4-2026-09-10 \
  --features exports/week3-features-2026-09-04.json \
  --symbol AAPL \
  --trading-date 2026-09-04 \
  --revises PUT_THE_LATEST_SNAPSHOT_ID_HERE \
  --reason 'New reviewed feature export after source correction'
~~~

`forecast_revisions` contains only the child-to-parent link, root ID, and
reason. Existing unlinked snapshots are version-one roots. A parent can have
only one child, so a history remains a simple linear sequence. Get the full
root-to-latest history from any snapshot in the sequence:

~~~bash
curl -s http://127.0.0.1:8000/forecast-snapshots/PUT_A_SNAPSHOT_ID_HERE/timeline
~~~

The response contains `root_snapshot_id` and a `snapshots` list. Each list
entry contains the complete stored prediction plus `version`,
`parent_snapshot_id`, and `revision_reason`.

### Replay an archived prediction

Keep the original Week 4 artifact directory available for every archived
prediction. Replaying uses only the snapshot's persisted six feature values;
it does not reread the original feature export or market-price tables, retrain,
or write to the database.

~~~bash
.venv/bin/python -m app.forecast_replay \
  --snapshot-id PUT_A_SNAPSHOT_ID_HERE \
  --model-dir artifacts/week4-2026-09-10
~~~

The command verifies the saved model and manifest SHA-256 values before
deserializing the trusted local `joblib` artifact. It prints the stored and
recomputed named probabilities plus `matches`. A probability mismatch prints
that evidence and exits with status 1; missing, altered, or incompatible
inputs fail without changing the archived record.

The Week 4 artifact and the feature export are local ignored files; they are
not committed to GitHub. `joblib` files must be treated as trusted local input,
not as files supplied by an API caller. See [Week 5 progress](docs/week5-progress.md)
for the exact boundary of this slice.

## Validation evidence

The automated suite covers the forecast API, data validation, idempotent ingestion, immutable revisions, timezone and session-close boundaries, cross-timezone snapshot selection, feature formulas, warm-up/skip behavior, and tests that future bars or later revisions cannot change an earlier feature row. Run pytest -q after changing the schema, ingestion, snapshot, or feature logic.

Verified locally on 2026-09-10: 72 tests passed (10 dependency deprecation
warnings), and `pip check` reported no broken requirements.

- The upgrade appended 4,536 baseline revisions and a repeat migration added 0;
  the original 4,536 legacy price rows were preserved.
- At a 2025-03-03 cutoff, both equivalent timezone representations returned
  375 rows ending 2025-02-28 at 20:59 UTC, then 376 rows ending 2025-03-03 at
  the 21:00 UTC XNYS close. Observed mode returned 0 legacy rows, as expected.
- Historical export `exports/week3-features-2026-09-04.json` produced 4,416
  finite feature rows: 736 per AAPL, MSFT, GOOGL, AMZN, NVDA, and SPY, plus
  exactly 120 warm-up skips and no other skip reason. The SPY relative-return
  feature was zero. This local run took about 37 seconds; timing is only a
  machine-specific reference.
- The Week 4 run retained 3,580 labelled stock rows and excluded 100 rows whose
  20-session labels had not matured. Its three OOS blocks contain 1,260 rows in
  total. Raw logistic regression had 0.4516 accuracy, 0.3530 balanced accuracy,
  0.6252 multiclass Brier score, and 1.0465 log loss. Sigmoid calibration made
  probability scores worse in this run (0.6920 Brier, 1.2237 log loss), so it
  is recorded rather than presented as an improvement.
- The Week 5 acceptance run used the archived AAPL 2026-09-04 root snapshot
  `56b452a4-2db3-4c7e-9be1-e1c483de6a3c`, then appended revision
  `c1152656-44fd-4a0f-9b5f-9f87da843139` with an explicit manual-review reason
  and unchanged input. HTTP timeline requests from either ID returned the same
  root-to-version-two sequence; both records replayed exactly. A stale-parent
  revision attempt failed with exit code 2 and did not create an orphan. The
  root row hash and the legacy 52 `forecasts`, 4,536 `market_prices`, and 4,536
  `market_price_revisions` rows remained unchanged.

## Current limitations

- `POST /forecasts` still creates only the deterministic `mock-v1` placeholder;
  it does not serve the Week 4 model.
- Week 5 archives, revisions, and replays offline inferences only. It does not
  provide online model serving, automatic re-evaluation after a data revision,
  or monitoring. The application exposes no update or delete route for
  snapshots, but this is not a database-level tamper-proof guarantee.
- The Week 4 artifact is experimental. This archive feature makes no accuracy
  improvement or trading-profit claim. It was built on 2026-09-10 from a
  historical-research export, so it is not evidence of a prospective live run.
- Historical-research backfills are not a genuine provider point-in-time feed.
  Overlapping 20-session labels also mean the OOS rows are correlated and do not
  establish trading profitability.
- Yahoo Finance Chart is an external undocumented endpoint and can change or rate-limit requests.
- SQLAlchemy create_all is currently used for schema creation; Alembic migrations, SEC/FRED evidence, LLM workflows, frontend, deployment, and monitoring remain later milestones.
