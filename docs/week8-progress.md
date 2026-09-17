# Week 8: bounded event-triggered rolling refresh

## Implemented slice

Week 8 joins the existing offline prediction archive, saved-source extraction,
and bounded research workflow in one small API path. It accepts a stock symbol,
two exact XNYS session-close cutoffs, and one ID from the committed
[Week 8 source manifest](week8-sources.json). It does not accept a model
directory, document path, URL, or arbitrary provider settings from a caller.

`POST /forecast-refresh-runs` first checks the fixed trusted Week 4 artifact
and that both requested market feature rows can be built from the local
historical-research snapshot. It then extracts one event from the saved
announcement and performs the fixed supporting/counter research workflow. A
failed research run is retained as an auditable failure, but creates no
forecast chain.

After those checks, a transaction creates an original snapshot and one linked
child snapshot together with the `forecast_revisions` link and a
`forecast_revision_evidence` sidecar. The sidecar records the document hash,
validated event quote, cache key, research-run ID, and both target windows. A
transaction-scoped PostgreSQL advisory lock uses the symbol and original
cutoff, and the parent can have only one child. Repeating the same parent and
event is rejected before extraction or research. If the sidecar write fails,
the original, child, revision link, and sidecar all roll back together.

`GET /forecast-refresh-runs/{child_snapshot_id}` reconstructs the saved report
from the database. It returns both named three-class probabilities, their
per-class delta, the source-triggered event, research record, and target
windows without rereading market data or calling a provider.

## Rolling-target semantics

Only `rolling_refresh` is implemented. A snapshot's target is the next 20
actual XNYS sessions after its own feature date, so the revised target moves
forward with the refresh. There is deliberately no `fixed_target` mode: its
different question would require a separately specified label/revision design.
The request schema rejects it.

The LLM never supplies, edits, or explains the numeric probabilities. Both
probability vectors come from the trusted local Week 4 artifact applied to two
six-feature market snapshots. The displayed delta therefore means only
"model output under the later market snapshot"; it is neither an LLM
adjustment nor a causal estimate of the announcement's effect.

## Run the saved demonstration

Fetch the public first-party source cache once, then start the local API. The
source text and response reports are ignored by Git; the manifest retains the
URL, date, and SHA-256 identity.

~~~bash
.venv/bin/python scripts/fetch_week8_sample.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
curl -s -X POST http://127.0.0.1:8000/forecast-refresh-runs \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","before_as_of_time":"2026-07-29T20:00:00Z","after_as_of_time":"2026-07-31T20:00:00Z","document_id":"aapl-2026-q3","revision_mode":"rolling_refresh"}'
~~~

The saved Apple announcement is dated 2026-07-30 and is conservatively
eligible from 2026-07-31T00:00:00Z. The two feature cutoffs are
2026-07-29T20:00:00Z and 2026-07-31T20:00:00Z. The resulting rolling target
windows are 2026-07-30 through 2026-08-26 and 2026-08-03 through 2026-08-28.

Use the returned `revised_snapshot.id` to retrieve the durable report:

~~~bash
curl -s http://127.0.0.1:8000/forecast-refresh-runs/PUT_CHILD_SNAPSHOT_ID_HERE
curl -s http://127.0.0.1:8000/forecast-snapshots/PUT_CHILD_SNAPSHOT_ID_HERE/timeline
~~~

Set `DEEPSEEK_API_KEY` only in the ignored local `.env`. An uncached run can
make at most three bounded provider requests: one event extraction and two
research prompts. A duplicate request is rejected before those calls.

## Validation status

The focused tests cover a successful two-version chain, rolling windows,
research failure without a forecast chain, duplicate rejection without new
provider calls, sidecar-write rollback, fixed-target request rejection, and a
calendar boundary that fails closed. The full local suite passed with **99
tests** on 2026-09-15 (ten dependency deprecation warnings).

One live local HTTP request completed on 2026-09-15 using `deepseek-flash`,
the saved Apple source, and the fixed Week 4 artifact. It created root snapshot
`d06e417e-5548-42d0-9f1a-55141b896b93`, child snapshot
`ba2ac136-236f-4e4b-b61d-de0cd77de428`, and successful research run
`be9460ac-526f-4b3a-bdf6-dd168786ba37`. The ignored response is
`artifacts/week8-validation-2026-09-15/refresh-response.json`.

The original model probabilities were bearish 0.6764945738, neutral
0.2941896673, and bullish 0.02931575899. The child values were bearish
0.1567365293, neutral 0.6849474223, and bullish 0.1583160484. They match a
separate direct calculation from the two saved feature exports. The child
report, normal forecast timeline, and replay of both archived snapshots were
retrieved successfully from saved local records.

Manual review accepted the event type, date, source URL, and exact earnings
announcement quote. It did not accept every model-written research inference
as a conclusion: for example, a statement about WWDC26 occurring outside the
reported quarter is not established by this announcement, and a margin
qualification appeared in the supporting list. Those original report claims
remain immutable and explicitly require human review. They do not affect the
stored probability values or the revision decision.

This is a historical-research demonstration. The underlying bars are initial
backfills with conservative availability assumptions, and the Week 4 model was
an offline evaluation artifact saved after the historical period. It is not an
observed live feed, prospective forecast, automated scheduler, or trading
recommendation.
