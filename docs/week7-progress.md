# Week 7: bounded research workflow

## Implemented slice

`app.research_workflow` implements one fixed sequence for a research request:

1. Source check: accept only up to ten IDs from `week6-sources.json`, load the
   matching saved documents, require the requested ticker and exact validated
   Week 6 v3 cache key, and record each document's URL and SHA-256.
2. Supporting case: one bounded DeepSeek JSON request can propose up to five
   source-bound claims.
3. Counter case: a second bounded request can propose up to five claims that
   qualify the supporting case. An empty counter case remains an explicit
   information gap; the workflow does not invent opposing evidence.
4. Review: local code verifies every `source_id` and exact quote against the
   selected source text, then builds the report from only those verified
   claims. This step does not call a model.

Each request has a timezone-aware `as_of_time`. Because the saved Week 6
documents have dates but not publication timestamps, a document is available
only from 00:00 UTC on the following day. The report labels this as
`historical_research`: publication date is a conservative proxy, not proof of
when this system observed the announcement or a live historical feed.

The workflow stores its request, source snapshot, node trace, final report or
safe error, and completion time in the additive `research_runs` table. A
model-response or source-validation failure is saved with `status: failed` and
has no report; retrying is a new request. Reports contain no probabilities,
price targets, or forecast updates. Claims are model-generated inferences with
source quotes and require human review.

## Run a selected research request

The API accepts IDs rather than server file paths:

~~~bash
curl -s -X POST http://127.0.0.1:8000/research-runs \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","as_of_time":"2024-11-01T00:00:00Z","document_ids":["aapl-2024-q4"]}'
~~~

The equivalent CLI also accepts only manifest IDs:

~~~bash
.venv/bin/python -m app.research_workflow \
  --symbol AAPL \
  --as-of-time 2024-11-01T00:00:00Z \
  --document-id aapl-2024-q4
~~~

Use `GET /research-runs/{id}` to retrieve an accepted report or a persisted
failure state. The selected source must already have a valid cached Week 6 v3
extraction with the same configured DeepSeek model; the research endpoint does
not quietly re-extract it. `POST /research-runs` returns HTTP 201 when it has
created a durable run record; callers must inspect `status` and `report` rather
than treating HTTP 201 as a successful analysis.

## Validation status

The workflow has local success, invalid-quote, unavailable-as-of-time,
model-failure, provider-exception, empty-counter, empty-both-sides, manifest
identity, filtered-event-context, and CLI failure-exit tests. The full local
suite passed with `93 passed` (plus ten pre-existing dependency deprecation
warnings).

On 2026-09-15, one real bounded request ran for `AAPL` with
`aapl-2024-q4` and `as_of_time=2024-11-01T00:00:00Z`. It used the existing v3
cache key `717dd66a984552e26d81c673434406e63bee4de24c2d2f27d285ee6e3f6098a5`,
made two DeepSeek requests, and completed research run
`7e59a146-1c75-4faf-b3fb-9b39903e6286`. The ignored local result is
`artifacts/week7-2026-09-15/report.json`. It contains five supporting and five
counter claims, each with an exact source quote and `human_review_required`.
Manual review accepted that the source supports the quoted $94.9 billion and
6% revenue result, the excluded-one-time-charge $1.64 EPS statement, and
nearly $27 billion operating-cash-flow statement. It did not accept three
model inferences as stated: the supporting claim that Services is necessarily
higher-margin recurring revenue; the counter claim that `iPhone drives` proves
growth was not broad-based; and the counter claim that the dividend must be
funded regardless of business conditions. An exact quote validates source
provenance, not every inference made from it.

The saved real report retains its original immutable text. New runs use the
corrected deterministic conclusion: the model proposed claims with
source-verified quotes, and quote verification does not establish those claims,
their direction, or a forecast. Week 7 is therefore accepted as an engineering
workflow with explicit human-review boundaries; it is not an accepted research
judgment, investment recommendation, or live backtest.

A local simulated provider exception created failed run
`ce0e2297-14d2-4645-bbdb-0ec1320d82d4`, with no report. Counts for the legacy
forecast, market-price, and Week 6 cache tables were unchanged before and
after that failure.

An HTTP source-time check used the same AAPL document with
`as_of_time=2024-10-30T00:00:00Z`, before its conservative availability time.
It created run `1faa6161-62c3-41cb-9147-dfbb0fa636af` with
`status: failed`, `current_stage: source_check`, and `report: null`, without a
provider request. GET retrieval of the completed real run also returned its
saved report successfully.
