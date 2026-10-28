# FinBot

[中文](README.md) | [English](README.en.md)

**Demo:** [Open the research workspace](https://wp1461772181-glitch.github.io/market-evidence-agent/) · **Repository:** [wp1461772181-glitch/market-evidence-agent](https://github.com/wp1461772181-glitch/market-evidence-agent)

FinBot is a research workspace for individual US stock researchers. It brings saved market data, SEC filings, research materials, and versioned analyses together, with bounded Agent chat over saved evidence. Researchers can find leads in materials, inspect sources, save analyses, and review forecast versions and later revisions.

The product focuses on an inspectable research process, traceable evidence, and conclusions that can be revised when new material arrives. It does not promise accurate stock forecasts and is neither a trading system nor a live-quote service.

## Agent chat

- Full-page SSE chat streams status, tool calls, results, and answers, with readable badges for tool activity.
- The production LangGraph entry point now uses four direct routes: A retrieves evidence before the tool loop, B answers in-scope concepts without tools, C returns a fixed out-of-scope response, and D enters the tool loop directly. Structured model function calls select the route and typed read tools. Reference resolution is a separate model call followed by scope validation; the first standalone question skips it. A and D share typed read, queue/navigation and confirmed owner-write tools.
- Long conversations use three context layers: recent turns word for word, half-compressed history (your words and the start of each answer), and appended segment summaries, sharing one budget (64 KB by default, set with `AGENT_CONTEXT_BUDGET_KB`). In the current direct graph, a bounded recent-dialogue slice is supplied as reference context; prior messages are not evidence or a ground for a citation. A summary is memory, not evidence. The “Context left” pill beside the model picker shows each part.
- Costly writes require an owner confirmation card with frozen arguments, an expiring conversation-bound capability, and durable replay protection. Guests receive browser-local queue and navigation actions. Confirmed web ingestion remains private and retains `web_news` provenance through indexing and retrieval.
- `web_research` can search public pages through Tavily or read a public HTTPS URL. Search defaults to a separately configured server-side `TAVILY_API_KEY`; without it, search reports unavailable and never silently falls back to Bing. Public URL reads do not need a Tavily key. Page excerpts are bounded and search snippets are not treated as page bodies.
- Answers support Markdown, tables, source citations, and quoted excerpts. Users can copy answers, navigate to questions, and inspect cited sources.
- Conversations and tool traces are stored server-side by identity, with guest and personal identities separated. Users can export a local JSON conversation archive as a backup.

## Thinking process

Every answer carries a “Thought for N s · M steps” panel. While a turn runs, the graph grows in place: input, reference resolution, routing, model decisions, tool calls, evidence verification, repair, and the final answer. Parallel tools fork from the decision that called them and join at the next step; each node shows its status and duration and can be expanded for a short output summary or its raw data. The panel folds after completion; a manual toggle lasts for the current page. After a refresh, it starts folded again but the graph and total time replay from the server, and a cancelled turn never leaves a running node behind.

This is an execution trace, not the model's chain of thought: it records what the system actually did, which tools it used, and how long each step took, without exposing hidden reasoning. Traces are capped at 60 nodes, each output is compressed to 400 characters, and the active model API key is redacted to `[redacted]`; confirmation-card control values never enter the trace. A trace is not added to the model context. See the [dialogue design notes](docs/agent-dialogue-engineering.zh-CN.md) for the measurements.

| Tool | Purpose |
| --- | --- |
| `query_stock_prices` | Read saved daily OHLCV and fetch time; these are not live quotes. |
| `query_latest_forecast` | Read the current research forecast version, status, time, and summary for a stock. |
| `query_materials` | Search saved SEC filing excerpts and user uploads; it does not search FAQs. |
| `query_sec_filings` | Read the saved SEC filing inventory and fetched excerpts; it does not contact SEC. |
| `query_research_faq` | Search the approved research FAQ. |
| `query_material_analysis` | Read the latest saved AI analysis and its source link for a material. |
| `search_material_analysis` | Search saved AI analyses, which are derived materials. |

## Try it

1. Open the [online demo](https://wp1461772181-glitch.github.io/market-evidence-agent/). Visitors can browse; write operations require a personal identity.
2. In Model settings, enter a profile display name, endpoint, model ID, and your own model key. Run the connection test and save the profile. Select it in the chat composer to use it. See the [model profile and storage guide](docs/model-profile-storage.md).
3. Ask about an original quote in saved materials, or about supporting evidence, counter evidence, and key numbers in a saved AMD analysis. Then inspect the cited source.

Visitors do not receive administrator access, and the demo server does not provide shared model keys.

## Model settings and local conversation archives

- Choose a preset model or a custom HTTPS endpoint, then enter a model ID and display name. The UI supports connection checks, saved profile cards, and profile selection in the chat composer.
- Services that support model discovery can return up to five suggestions. The provider catalog includes DeepSeek, Kimi, MiniMax, Qwen, Zhipu, Doubao, Hunyuan, Baidu, SiliconFlow, OpenRouter, and custom endpoints. Discovery depends on the service.
- Browser credentials use OPFS/IndexedDB. A separately configured local server-file mode is also available for local deployments. The browser does not treat arbitrary plaintext project files as its credential store; this does not imply encryption at rest.
- Chat history does not store provider API keys. Automatic archiving depends on browser support, and the archive file must be selected again after reload. Archives are local conversation records, not an evidence store; see the [conversation archive guide](docs/chat-local-files.md).

## Research materials and retrieval

The material library manages approved FAQs, fetched SEC filing excerpts, and user-uploaded TXT, Markdown, and PDF files (without OCR). The backend provides upload/import, retrieval testing, and index status. PostgreSQL stores originals, chunks, jobs, and analysis versions; Milvus stores the vector index. BGE-M3 produces dense vectors. The online demo currently uses hybrid retrieval, fusing dense results and BM25 with RRF. `bge-reranker-v2-m3` reranking can be enabled through configuration.

Parsing retains document structure and table headers. Retrieval applies owner, stock, time, and version filters, and supports idempotent retries, reindexing, and citations to exact sources.

### Saved analyses become knowledge

Saved AI analyses can be added to knowledge retrieval in six groups: summary, facts, supporting inferences, counter inferences, key numbers, and uncertainties. Analyses retain versions, model information, and original quotes; indexed results follow public/private visibility and owner isolation. These are AI analyses derived from source material, not independently verified facts.

Public validation so far includes two saved AMD analyses that produced 66 chunks, followed by successful indexing, querying, and citation readbacks. This verifies the workflow; it does not mean all historical materials have been backfilled or every question will retrieve accurately. Conversation review and extraction are separate optional operations; the system does not automatically learn from every chat.

## Research workspace

- **Watchlist and overview:** Users can maintain a list of US stock symbols; seven example stocks are provided by default. Stock pages organize prices, materials, forecasts, and evaluation by symbol.
- **Prices and benchmark:** View saved OHLCV and SPY comparison candlesticks with data dates and fetch times. Historical snapshots can be read with time boundaries.
- **SEC filings:** Store filing inventories, source links, and fetched excerpts; scanning can be started from the materials workflow. The Agent's read tools only read saved records; fetching filing text is a write action that runs only after a personal identity confirms it.
- **Material analysis:** Generate and cache AI analyses with versions, original quotes, and citation navigation. Reopening a saved analysis does not call a model again.
- **Research workflow:** Reusable material analyses can produce a ResearchBrief, which can then be evaluated by the experimental Jev three-class decision model as bullish, neutral, or bearish. Jev consumes a research brief; it does not generate one. Fixed targets and expiry assessments are retained with immutable versions.
- **Evaluation and run status:** Inspect expiry assessments, historical replay, background queues, and monitoring status. A four-strategy Recall@K/MRR evaluation harness exists; synthetic results do not represent production accuracy.
- **News leads:** Saved news leads and public web retrieval in chat are separate read-only paths; chat does not automatically save webpages as research materials. Recent queries require readable publication dates; explicit `days_back` windows (1–366 days) are clipped precisely, while an unbounded “recent” request uses seven days.

Jev forecasts retain the original probabilities. A local calibration pipeline uses matured observed samples and enables a candidate only after chronological holdout validation; historical calibration research is kept separate from live samples. Outputs remain experimental research results, not forward-validated investment success rates.

## Architecture

```mermaid
flowchart LR
  U[Researcher] --> FE[React frontend<br/>GitHub Pages]
  FE --> API[FastAPI<br/>Mac / Funnel or self-hosted]
  API --> AG[Agent: SSE chat]
  AG --> T[Typed catalogue: reads, browser actions,<br/>confirmed owner writes]
  T --> PG[(PostgreSQL<br/>Business records and originals)]
  T --> KB[Knowledge retrieval service]
  API --> KB
  PG --> CH[Structure-aware chunks]
  CH --> EMB[BGE-M3 embeddings]
  EMB --> MV[(Milvus index)]
  KB --> PG
  KB --> MV
  API --> LLM[Separately configured model service]
  FE --> LOCAL[Browser archives and configuration]
```

PostgreSQL stores business records, originals, chunks, jobs, and conversations; Milvus stores the retrieval index. Originals and vector indexes are updated separately, not as one atomic dual-write. Model services are configured separately from the business database. The public demo frontend is hosted on GitHub Pages; the backend runs on the maintainer's Mac and is exposed through Funnel, so availability depends on that host being online. Local self-hosting is also supported by the setup documentation.

## Current boundaries

The Agent's read tools only read saved records; explicit web research can read bounded excerpts from public pages, and error or navigation pages are detected and skipped. It does not trade or retrieve live prices. Page/API write actions have their existing identity checks. Chat writes require explicit user intent and server confirmation; public pages may be ingested as `web_news` without becoming official evidence. See the [verification report](docs/reports/agent-routing-verification.md) for the exact deployed revisions, DeepSeek-Flash results and release checks dated 2026-10-16. “Context left” is this product's context budget, not the window of the model you chose. Evidence gating uses heuristics, answers can still be wrong, and citations do not prove every answer is faithful.

Forecasts, material analyses, and retrieval results require researcher review against original sources and timestamps. Forecast evaluation is meaningful only for matured samples with available outcomes; historical replay and synthetic retrieval metrics do not establish future performance. High-concurrency guarantees and end-to-end atomic writes remain unverified. The verification report records the latest official DeepSeek-Flash evaluation and real public guest checks, including earlier failed runs. Real owner-write production coverage remains unverified.

## Documentation

- [Local setup and deployment](docs/github-pages-local-backend.md)
- [Conversation archive files](docs/chat-local-files.md)
- [Model profiles and storage](docs/model-profile-storage.md) · [Model discovery](docs/model-discovery.md)
- [Conversation context and the retention evaluation](docs/agent-context.md) · [Evaluation report](docs/context-retention-report.md)
- [Dialogue system design and thinking-process measurements (Chinese)](docs/agent-dialogue-engineering.zh-CN.md)
- [Knowledge retrieval](docs/knowledge-rag.md) · [Four-strategy evaluation report](docs/knowledge-rag-ch34-reports/four-strategy-evaluation.md)
- [Custom stock support](docs/custom-stock-support.md) · [Experimental forecast and learning limits](docs/jev-local-learning.md)

## Future direction

Possible directions are lower per-turn latency (measured first) and a budget that knows the model's window; nothing is scheduled. The system does not implement multi-Agent autonomy or model fine-tuning.


## 🧭 Routing verification

The [C1–C14 verification report](docs/reports/agent-routing-verification.md) links the 50-case JSONL evaluation, permissions, test migration, and release status. The public demo may run a different revision.
