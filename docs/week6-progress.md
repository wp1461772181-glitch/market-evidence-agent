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

The validator refuses an existing output directory. Before a provider call it
checks that the ten local documents match the committed manifest and that a
local provider configuration is present. Its first pass records cache hits and
provider-factory calls. Its second pass uses a factory that fails immediately
if called, so success proves that every result was read from the database cache
without a second provider request.

`artifacts/week6-validation-*/report.json` is local and ignored. It retains the
full generated events for review, including summaries and qualitative impact
directions. A reviewer must inspect those fields against the source URLs before
publishing any aggregate conclusion.

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

The run also produced one optional `capital_return` event for Microsoft Q2
FY2025 that describes capital returned during the historical quarter, rather
than a newly announced action. Its quote is real and the schema/cache checks
pass, but it does not meet this slice's stricter event-selection rule and is
not accepted as an analyst-reviewed event. The original v3 cache record remains
for audit rather than being hand-edited. A prior v1 attempt stopped after
five cached documents because one model quote exceeded the field limit; v2
then passed structurally but exposed date-selection issues in optional events.
The v3 run was the final bounded retry (27 provider calls across those trials).
Week 6 therefore has partial acceptance: the ten-document source, date, quote,
schema, and cache behavior are verified, while semantic event selection and
impact-direction assessment need a later, simpler deterministic rule before
the week can be called fully complete. An independent cache-only rerun also
returned all ten v3 documents without constructing a provider, and the
pre-existing Week 1–5 table counts and hashes remained unchanged.
