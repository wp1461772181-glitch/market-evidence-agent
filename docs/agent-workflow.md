# Agent workflow status (development worktree, not deployed)

The SSE chat endpoint now runs a LangGraph StateGraph with `resolve_reference`, `classify_intent`, `route_intent`, `retrieve_knowledge`, `confidence_gate`, `main_agent`, `execute_tools`, `clarify`, `fallback`, `final_answer`, and `record_log`. Custom graph events feed the existing SSE frames and SQL conversation history. The request handler retains verified ownership, conversation locking, request slots, and terminal cleanup. Checkpoints use LangGraph's PostgreSQL saver; model keys and identity credentials remain in request-only runtime context.

The deterministic route fetches required saved or public sources before Agent synthesis. Knowledge and web observations pass a pre-answer gate, including observations made later in the bounded ReAct loop. Weak, empty, or unavailable evidence produces an explanatory answer and low-confidence record without a final model call. Saved-data reads skip the knowledge gate. Multi-stock saved comparisons fetch each classified symbol; an explicit multi-stock request without a singular selection leaves the current focus unset unless the prior confirmed focus is one of those stocks. Chinese filing terms route to saved SEC filings, while saved AI-summary requests route to clearly labeled derived analysis. Limits are five tools, six decision steps, 120 seconds, graph recursion limit 60, and a 32,000-token turn budget using provider totals where available and labeled estimates otherwise. This heuristic checks basic source, entity, metric, and period coverage; it does not establish claim faithfulness.

`web_research` reads public HTTPS pages or searches Tavily with `TAVILY_API_KEY`, bounded results and extracted bodies. A single explicit public HTTPS URL is sent to direct-read mode; multiple or invalid explicit URLs prompt clarification. A missing key returns unavailable for search; Bing RSS requires explicit `WEB_RESEARCH_BACKEND=bing_rss`. Search snippets remain leads, not body evidence. RFC 2822 and ISO publisher timestamps are normalized separately from fetch time; a fresh-news gate requires publisher dates for each retained source. Query freshness is bounded by Tavily's news `time_range` option and does not guarantee the newest available article. Web citations refer to retained conversation snapshots; evidence beyond the ten-citation cap is excluded from the model context.

## Local operation

Run the explicit additive checkpoint migration before starting chat:

```sh
.venv/bin/python scripts/migrate_agent_workflow.py
.venv/bin/python -m pytest -q
cd frontend && npm run test:i18n && npm run build
curl -fsS http://127.0.0.1:8000/health/agent-workflow
```

The workflow uses `AGENT_CHECKPOINT_DATABASE_URL` when set, otherwise the application's PostgreSQL URL. `/health/agent-workflow` reports `checkpoint_ready`, `tavily_configured`, and the selected web backend. A false checkpoint readiness means chat cannot run until the migration and database are available. The migration does not publish or alter existing business records.

## Verification and limits

Fixture-driven tests exercise the actual graph path, SSE/SQL traces, low-confidence stop, multi-stock reads, repeat stop, deadline, cancellation, and PostgreSQL saver reopen and owner isolation. A live anonymous read of `https://example.com/` succeeded. No real model or authenticated Tavily search was made by this worker; these and public deployment remain for independent acceptance. Provider outputs and retrieved excerpts still require source review. The current gate has limited paraphrase and complex-period coverage. Checkpoint and SQL writes are separate and are not one atomic transaction.
