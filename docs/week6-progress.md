# Week 6: source-grounded event extraction

## Implemented slice

Week 6 uses a deliberately bounded path: ten saved, first-party Apple and
Microsoft earnings announcements become small structured event lists. The
local source-text cache is ignored by Git; [the source manifest](week6-sources.json)
contains the public URLs, dates, document hashes, and short review anchors.

`app.event_extraction` sends one saved document to the configured DeepSeek
model, accepts only a strict JSON object, and rejects an event when its
evidence quote is not an exact substring of that same document. Company and
source URL are attached by the application from the saved document rather than
accepted from the model. Validated results are cached in the additive
`event_extractions` table using a hash of the document, provider, model, and
prompt settings.

This is evidence extraction only. It does not fetch arbitrary URLs at request
time, invoke an agent loop, retrieve unrelated material, or predict a price.

## Run the bounded validation

Set `DEEPSEEK_API_KEY` only in the untracked local `.env` file. Do not commit
that file or paste its value into commands, documents, or issues. The selected
model is `deepseek-flash`.

~~~bash
.venv/bin/python scripts/fetch_week6_samples.py
.venv/bin/python scripts/validate_week6.py \
  --output-dir artifacts/week6-validation-YYYY-MM-DD \
  --model deepseek-flash
~~~

The validator refuses an existing output directory. It checks that the ten
local documents match the committed manifest. Its first pass records cache hits
and provider-factory calls; an all-cached run does not construct a provider or
need an API key. Its second pass uses a factory that fails immediately if
called, so success proves that every result was read from the database cache
without a second provider request.

`artifacts/week6-validation-*/report.json` is local and ignored. Its `events`
field is the review-facing list; every event keeps the model's
`impact_direction` but is marked `impact_direction_status: review_required` and
is not used for forecasts. `excluded_events` retains a transparent copy and an
exclusion reason. A reviewer must inspect summaries and directions against the
source URLs before publishing any aggregate conclusion.

## Validation status

On 2026-09-12, a live `deepseek-flash` run using prompt version
`week6-event-extraction-v3` completed all ten documents. The first pass made
ten provider calls with zero cache hits; the second made zero provider calls
and returned ten cache hits with identical event lists. The full
local test suite passed (`82 passed`). The ignored full report is
`artifacts/week6-validation-2026-09-12-v3/report.json`.

Manual review accepted the required `earnings_release` event's source company,
URL, announcement date, and source-grounded quote for all ten documents. The
result summaries match the reported figures on that first review. The
qualitative `impact_direction` labels remain heuristic rather than accepted
analysis: for example, Apple Q4 FY2024 is labelled positive while its summary
omits the one-time tax charge that affected reported EPS. This is source
extraction, not a claim about future prices or trading performance.

The cached v3 records are never rewritten. A review projection now excludes
only a `capital_return` event whose English evidence quote contains both
`returned` and `quarter`, without `declared` or `authorized`. This narrowly
catches Microsoft Q2 FY2025's statement that it returned capital in the
reported quarter; it retains Apple's declared dividends and authorized
repurchase programs. It is a transparent rule for this saved English source
set, not a general natural-language history classifier. The excluded original
event and its reason remain in `excluded_events` for audit.

On 2026-09-12, an independent cache-only replay of the ten v3 documents made
zero provider-factory calls, returned ten cache hits, displayed 19 events, and
excluded only Microsoft Q2 FY2025's historical capital-return event. All
displayed and excluded directions are marked `review_required`; no direction
feeds a forecast. With this boundary, Week 6's ten-document source, date,
quote, schema, cache, presentation filter, and direction-review state are
accepted. A prior v1 attempt stopped after five cached documents because one
model quote exceeded the field limit; v2 then passed structurally but exposed
date-selection issues in optional events. The v3 run was the final bounded
retry (27 provider calls across those trials). The pre-existing Week 1–5 table
counts and hashes remained unchanged.
