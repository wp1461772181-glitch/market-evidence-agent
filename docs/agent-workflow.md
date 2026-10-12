# Agent workflow status (development worktree, not deployed)

The SSE chat endpoint runs a LangGraph StateGraph with reference resolution, strict classification, deterministic routing, scoped source reads, evidence checks, authorized system actions, clarification, answer generation and audit recording. Custom graph events feed SSE and SQL conversation history. PostgreSQL checkpoints hold pending scope and task state; model keys and identity credentials remain in request-only runtime context.

Saved SEC and saved AI analysis have separate exact reads. Latest financial reports are selected by filing date among 10-Q/10-K sources, and exact AI summaries use the selected source. Thematic questions bind explicit accession, form or source ID before retrieval; a verified direct-source fallback is available while indexing is pending. Multi-stock compound requests retain per-source task outcomes and report missing evidence. Rejected retrieval content and its citation ordinals are excluded before final generation. Clarifications retain a pending request ID and selected scope. Owner actions use an additive idempotency ledger and existing SEC, analysis, knowledge and forecast services; a submitted asynchronous job remains running until its underlying job reports completion. Limits are five tools, six decision steps, 120 seconds, graph recursion limit 60, and a 32,000-token turn budget using provider totals where available and labeled estimates otherwise. The evidence gate checks basic source, entity, metric, and period coverage; it does not establish claim faithfulness.

`web_research` reads public HTTPS pages or searches Tavily with `TAVILY_API_KEY`, bounded results and extracted bodies. Search terms use company aliases and topic words; an unsuccessful relevant-article check permits one alternative search. A single explicit public HTTPS URL is sent to direct-read mode; multiple or invalid explicit URLs prompt clarification. A missing key returns unavailable for search; Bing RSS requires explicit `WEB_RESEARCH_BACKEND=bing_rss`. Search snippets remain leads, not body evidence. RFC 2822 and ISO publisher timestamps are normalized separately from fetch time; a fresh-news gate requires publisher dates for each retained source. Query freshness is bounded by Tavily's news `time_range` option and does not guarantee the newest available article. Web citations refer to retained conversation snapshots; evidence beyond the ten-citation cap is excluded from the model context.

## Local operation

Apply the additive checkpoint and action-ledger migrations to the intended database before enabling chat actions:

```sh
PYTHONPATH=. .venv/bin/python scripts/migrate_agent_workflow.py
PYTHONPATH=. .venv/bin/python scripts/migrate_agent_actions.py
PYTHONPATH=. .venv/bin/pytest -q
cd frontend && npm run test:i18n && npm run build
curl -fsS http://127.0.0.1:8000/health/agent-workflow
```

The workflow uses `AGENT_CHECKPOINT_DATABASE_URL` when set, otherwise the application's PostgreSQL URL. `/health/agent-workflow` reports `checkpoint_ready`, `tavily_configured`, and the selected web backend. A false checkpoint readiness means chat cannot run until the migration and database are available. The migration does not publish or alter existing business records.

## Verification and limits

Fixture-driven tests exercise the graph, SSE/SQL traces, owner isolation, interrupted action recovery, partial results, deadlines, cancellation and PostgreSQL checkpoint reopen. This worktree has not run authenticated DeepSeek, Tavily, browser or production acceptance. Provider outputs and retrieved excerpts still require source review. The current gate has limited paraphrase and complex-period coverage. Checkpoint and business SQL writes remain separate transactions. Interrupted analysis and forecast requests reconcile by their underlying idempotency key; an action without a provable replay path reports `interrupted_reconcile_required` instead of claiming progress.
