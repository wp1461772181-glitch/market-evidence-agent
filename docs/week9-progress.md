# Week 9: local evidence dashboard

## Intended slice

Week 9 adds a small local React + TypeScript dashboard for reading already
persisted evidence. A user enters a stock symbol and the page requests one
read-only `GET /dashboard/{symbol}` view. The response is assembled from saved
forecast chains, saved Week 8 refresh reports, and a fixed local whitelist from
the trusted Week 4 evaluation artifacts.

The dashboard's archive view remains read-only. Its separate explicit
`POST /forecast-runs` action can create a new immutable numerical snapshot;
it is not the legacy `POST /forecasts` mock endpoint. The action refreshes the
selected stock and SPY with the existing Yahoo ingestion path, then uses only
bars observed by this application at a post-refresh cutoff and no unfinished
XNYS session. It loads the fixed local Week 4 artifact, never a caller-supplied
model, and does not call an LLM.

The response declares `experimental_offline_model`, its 20-session forward
target window, and its limitations. SEC filing inventory entries are still
pending human review and are not automatically used as numeric forecast input.
Provider failure with stale or missing observed bars returns HTTP 422 without
writing a replacement record; a repeated request with identical observed
features returns the existing immutable snapshot.

## Page content

For a symbol with saved data, the single page should show:

- the latest archived three-class probabilities (bearish, neutral, bullish)
  and their feature cutoff;
- the primary evidence event, with a clickable HTTPS source link and the saved
  exact quote;
- the saved research report, clearly labelled `human review required` because
  exact-quote validation does not establish model inferences;
- a simple chronological version timeline for every saved root-to-leaf chain;
- a rolling-refresh reason, source date, and probability change where revision
  evidence exists; and
- the trusted Week 4 offline metrics and their historical-evaluation label.

The page also contains a lightweight SVG candlestick comparison. It displays up
to 250 saved daily OHLCV bars for the selected stock, with same-date saved SPY
closes for comparison. Archived original and revised feature cutoffs are marked
when they fall inside the saved series. A rolling target window is marked only
for the report to which it belongs. Where the required stored closes exist, the
view shows realized stock and SPY price returns for that window; incomplete
windows have no realized-return claim.

The dashboard does not imply a causal event effect. For rolling refreshes, a
probability change means that the fixed trusted model was applied to a later
market-feature snapshot with a different rolling 20-session target window.

## Visual direction

Keep the interface quiet and editorial: off-white background, dark ink text,
and teal reserved for active data and links. Use ordinary HTML controls and a
small set of cards, progress bars, and timeline rows. The page must remain
usable on a narrow viewport; a mobile layout can stack the same content rather
than introduce a second interaction model.

## Data boundary

`GET /dashboard/{symbol}` normalizes a one-to-five-letter symbol to uppercase
and returns HTTP 422 for malformed input. A successful response has
`symbol`, `snapshots`, `refresh_reports`, `evaluation`, and `price_history`
fields. It is read-only and draws only from persisted local records:

1. every `forecast_snapshots` chain for the normalized symbol, including
   versions that have no Week 8 evidence;
2. a saved `forecast_revision_evidence` record and its corresponding refresh
   report when a chain contains one; and
3. the trusted local Week 4 `report.json` and `manifest.json`, read through a
   fixed server-side whitelist. It exposes pooled five-stock, three-fold OOS
   metrics for the archived logistic and baseline comparisons; they are not
   symbol-specific. Missing or malformed artifacts produce `evaluation: null`,
   never a browser-side recomputation, model load, file path, or traceback; and
4. the last 250 saved daily stock OHLCV rows plus an aligned same-date SPY
   close. The endpoint reads the existing local historical-research records;
   it does not call Yahoo Finance or any other external source.

At the start of Week 9, the local demonstration database contains four AAPL
snapshots across two chains and one refresh-evidence record. Those seed records
are archived historical-research examples. The later explicit forecast action
can create a prospective, local experimental snapshot only after the fixed
Week 4 artifact publication bound, using observed bars and a completed-session
cutoff. It remains a research result, not a price target or trading signal.

## Candlestick data freshness

The local AAPL and SPY archive was refreshed once through the existing
validated ingestion pipeline for 2026-09-08 through 2026-09-21. It added ten
saved daily rows for each symbol, with 2026-09-21 as the latest saved date.
Future updates use the established ingestion command manually. There is no
scheduled fetch or automatic live feed.

The chart shares the project-wide historical-research limitation: initial
backfilled bars use conservative availability assumptions and are not a true
provider point-in-time feed. It is a visual comparison of saved records, not a
live-price chart, prospective forecast, or trading signal.

## Local run

With the API running at `127.0.0.1:8000`, install the frontend once and start
Vite locally:

~~~bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
~~~

The Vite development proxy sends `/api/dashboard/{symbol}` to the local API.
`npm run build` is the production build check.

## Acceptance evidence

The original local-reader acceptance suite passed with 104 tests and ten pre-existing dependency
deprecation warnings. The frontend production build also passed. Browser
acceptance used the persisted local AAPL archive: four snapshots across two
chains, one refresh report, and ten saved research claims. The later revised
snapshot rendered bearish 15.7%, neutral 68.5%, and bullish 15.8%; its original
July 29 snapshot rendered 67.6%, 29.4%, and 2.9% and correctly showed no future
event evidence.

- [x] a valid saved AAPL query renders named probabilities, cutoff, source,
  research-review label, timeline, revision reason, and offline metrics;
- [x] source URLs are rendered as HTTPS links to the saved Apple source, with
  a new-tab target and `noreferrer` verified in the browser;
- [x] all persisted AAPL chains are present, rather than only the latest chain;
- [x] an unknown symbol shows the empty state, while malformed input shows the
  API error state;
- [x] loading is visible while the request is pending, and a server/network
  failure offers a clear retry path;
- [x] the layout remains readable at 390px width without horizontal overflow;
- [x] browser acceptance finds no normal core-page console errors; injected
  network failures are expected and were tested separately; and
- [x] the documented local build and backend test commands have been run.

This original acceptance covers the local historical reader. The later
on-demand action has separate backend acceptance for market refresh, observed
cutoff, immutable persistence, model-publication bounds, and target-window
exposure; it is not a live quote stream or deployed online service.

Separate browser acceptance verified the AAPL candlestick chart at desktop and
390px mobile widths, including original/revised target windows and the
event-evidence boundary. MSFT correctly rendered its saved price history without
an archived forecast, and ZZZZZ showed the empty state. No page errors or
horizontal overflow were observed.

## Still outside scope

This is not a live market terminal, portfolio tool, trading recommendation, or
deployed online service. It does not add a scheduler, cloud hosting,
monitoring, automatic background refreshes, retraining, or a fixed-target
revision mode. SEC filing inventory is still a human-review queue and is not
automatically used as model evidence. Week 10 has not started.
