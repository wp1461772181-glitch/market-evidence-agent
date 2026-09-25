# Public news-discovery probe

This is a bounded, metadata-only proof of concept for media leads. It is not
an evidence store, article scraper, source verifier, LLM input, prediction
input, or automatic-revision trigger.

## What the command does

`scripts/discover_news_candidates.py` asks both the public GDELT DOC `artlist`
endpoint and TechCrunch's public company-tag RSS feeds for a small candidate
list for AAPL, MSFT, GOOGL, AMZN and NVDA. It saves nothing. Its JSON output
identifies:

- the candidate headline, outlet and original article URL;
- `source_seen_at`: GDELT's own discovery/seen timestamp, when provided;
- `published_at`: an RSS publisher timestamp when the source provides one;
  GDELT candidates keep this as `null`, since its response does not verify the
  original publisher time;
- `discovered_at`: the time this application made its metadata request;
- one explicit status per source/symbol: `success`, `empty`, `error` or
  `unsupported`.

It keeps each source's original headline and original article URL for display,
while using a separate normalized URL only as an internal same-symbol
de-duplication key. When two sources resolve to the same URL, the direct
publisher RSS candidate with `published_at` is retained over an aggregator-only
seen-time candidate. It applies small title-level company/product checks to both
sources. This reduces obvious noise only: a source tag alone does not make an
item a stock candidate. A returned item remains `review_status: "candidate"`;
it does not prove that the article is accurate, about the company in a material
way, or permitted for full-text reuse.

Run a bounded probe:

~~~bash
./.venv/bin/python scripts/discover_news_candidates.py --symbols AAPL,MSFT
~~~

The public sources are [GDELT DOC](https://api.gdeltproject.org/api/v2/doc/doc)
with [GDELT's usage statement](https://gdeltproject.org/about.html), and
[TechCrunch RSS](https://techcrunch.com/rss-terms-of-use/). The implementation
requests metadata and original links only; it deliberately does not retrieve
publisher pages or article bodies. TechCrunch RSS candidates are display-only:
their attribution and original link remain in the output, following its RSS
terms. A link returned by either source does not grant the project a licence to
store or submit the article body to a model.

## Multiple-source behaviour

Every source/symbol attempt is isolated. A rate limit, timeout, malformed
response or empty result is reported for that attempt while other configured
attempts continue. The current default is GDELT DOC plus the documented
TechCrunch company-tag RSS feeds. It runs TechCrunch first, then makes bounded
eight-second GDELT requests. It does not treat unofficial Google News RSS or
an undocumented publisher feed as a reliable production source.

Each TechCrunch feed exposes only its latest 20 entries. This experiment does
not establish coverage of any prediction-to-prediction time window. It is for
an operator to inspect candidates manually; it cannot yet support an hourly
complete-monitoring claim or the V2 P2 media-body acceptance requirement. The
default five-symbol CLI makes five separate GDELT requests. Since GDELT is
currently rate-limited in this environment, every symbol keeps its own error
or success state and the command deliberately makes no retry attempt.

Adding a later source requires a publisher/API with current documentation and
terms that permit the exact metadata or text use proposed here. Its results
must preserve the same URL, outlet, `published_at` versus `source_seen_at`,
candidate status and de-duplication boundary.

## Bounded live smoke result

At `2026-09-25T02:05:32Z`, one direct metadata-only request to GDELT DOC for
`"Apple"` over one day returned HTTP `429 Too Many Requests`. At
`2026-09-25T02:07:51Z`, the first CLI AAPL request returned the same explicit
`gdelt_doc: error` result. At `2026-09-25T03:14:05Z`, a direct five-symbol
TechCrunch RSS smoke test returned `success` and 20 *feed entries* for each of
AAPL, MSFT, GOOGL, AMZN and NVDA. The revised title filter may return fewer
stock candidates than the 20 entries in a feed. No article page was requested,
no retry loop was run, and no data was stored. The CLI and tests therefore
make rate limits visible as `error`; a production scheduler would need a
deliberately low request rate, backoff and a source-coverage indicator before
it could be relied on.

After the title filter at `2026-09-25T03:17:25Z`, the live TechCrunch counts
were AAPL 18, MSFT 13, GOOGL 20, AMZN 15 and NVDA 14. These are review leads,
not a coverage or event-materiality measurement.
