# Workflow + ReAct Agent for Market Evidence Agent

## Intent and authorization
User supplied the pipeline and directly authorized implementation: reference resolution → intent recognition → deterministic routing → knowledge retrieval → pre-answer confidence gate → main Agent → logging. Latest steering adds an online research tool this round, overriding the earlier no-new-business-tool constraint for that one tool. Keep the existing stock-research product, guest/owner identity and SSE chat UI. No additional yes/no handoff: user already requested direct execution and root uses planner-worker-review.

## Fixed stack and public baseline
Baseline d0148a9; isolated baseline 700 passed / 7 skipped. Python 3.12, FastAPI, PostgreSQL, existing BGE-M3/Milvus and seven LangChain tools. Add LangGraph 1.2.14 and langgraph-checkpoint-postgres 3.1.2 (current PyPI metadata); actual Context7 official receipts in /tmp/market-workflow-20261012. Use StateGraph and built-in AsyncPostgresSaver; in-memory saver is test-only. No new relational or vector database.

## Deterministic routing
Classifier is a small prompt with strict JSON validation; unknown/malformed labels safely clarify, never unrestricted routing. The resolver returns the raw question unchanged (no formal coreference this chapter). State may retain explicit stock/material focus and bounded complete history; switching stock clears incompatible material focus. Route dictionary is code, not an LLM policy.

| Intent | Initial workflow | Main capabilities |
|---|---|---|
| saved_data | No RAG gate | Existing saved prices, forecasts, SEC inventory, exact saved analysis |
| source_qa | query_materials → confidence | Original-source evidence + existing read-only tools |
| analysis_qa | search_material_analysis → confidence | Explicitly derived saved AI analysis + read-only tools |
| faq_qa | query_research_faq → confidence | FAQ evidence |
| compound_research | Required original/analysis retrieval; web retrieval when explicitly requested → confidence | Bounded multi-step tool observations and further checks |
| web_research | web_research → confidence | Public web research and existing read-only tools |
| general | No data retrieval | General conversation, no fabricated saved records |
| unsupported_action | Explain supported read-only scope | No writes/trading |

Missing a required stock/material and no valid current focus leads to a specific user question. Simple intents converge after one successful tool operation (initial knowledge/web retrieval counts); compound/multi-stock requests can iterate. Dynamic custom US stocks must work, not a seven-stock hardcoded whitelist.

## Graph and streaming
Explicit nodes: resolve_reference, classify_intent, route_intent, retrieve_knowledge, confidence_gate, main_agent, execute_tools, clarify, fallback, final_answer, record_log. Conditional edges enforce knowledge gate BEFORE any main-Agent execution/final-answer delta; business observations skip it. Further knowledge/web observations inside ReAct must also be gated before final streaming. No hidden thinking is streamed; show status and named tool badges, only final answer streams incremental tokens. Keep existing conversation/tool records and citations/history UI.

State carries question, intent/entities, current focus, complete messages/tool observations, evidence/citation manifest, gate verdict/reason, next action, tool signatures/steps, per-turn usage/budget/status and sanitized node log. Reset per-turn counters/evidence on the next turn. Credentials stay exclusively in non-persisted runtime context; never State, RunnableConfig/configurable, metadata, checkpoint, log, model prompts or tool arguments. Checkpointer thread_id is derived from verified owner_hash + conversation UUID, never user-supplied raw thread IDs. Production persistence survives runtime reopening; checkpoint migration/setup is additive and explicit. Existing chat rows continue serving the UI.

## Minimal confidence gate
Deterministic, inspectable heuristic: successful nonempty content, real source/citation coverage and question-specific evidence coverage (metric/entity/period when required). Respect retrieval strategy: calibrated dense cutoff only for dense; BM25/RRF/rerank scores are not interchangeable. No extra model self-judgment call required for this chapter. Retrieval/service failure, empty or weak evidence returns a normal explanatory fallback delta+done and stores a reason in the existing low-confidence pool; main Agent/final LLM never runs for failed pre-gates. No claim that this heuristic proves faithfulness. Fresh-news claims require trustworthy publisher dates; fetched/discovered time is not publication time. Business-data requests bypass RAG gate and missing records are stated honestly.

## ReAct and budget
Reuse execute_tool and validated schemas. Default max_tool_calls=5, max_decision_steps=6, graph recursion limit bounded (e.g.60), total turn deadline=120s, total token budget=32000, per-request decision/final completion caps, context/protocol byte limits preserved. Count actual provider usage when provided; otherwise record explicitly estimated usage and enforce request/context/output caps. Check remaining budget before new model/tool work and reserve final-answer budget; when exhausted output a bounded stop/clarify response without further model calls. Detect repeated canonical name+arguments before repeated execution; don't silently loop on unavailable tools. A model batch may be normalized to one valid operation per observation cycle. Finish on sufficient answer, clarification, unavailable evidence, budget, repeated call, cancellation or deadline. Preserve cancellation persistence and request-slot cleanup.

## Online tool (new, read-only)
One LangChain tool web_research supports a bounded public search query OR a public HTTPS URL. User selected Tavily as the default backend with a separate server-only `TAVILY_API_KEY`; absent key yields an explicit unavailable result and never silently falls back to Bing. Bing RSS remains available only through explicit `WEB_RESEARCH_BACKEND=bing_rss`. Tavily search uses basic depth and bounded results, requesting extracted raw content; snippets are leads, not evidence. Query mode fetches a bounded number of discovered pages where possible. Read mode returns bounded text/HTML excerpts, title, source URL, fetched_at, optional publisher timestamp, coverage/truncation/hash and clear per-source failures. No auth cookies, model key headers, login/paywall/verification bypass, JavaScript rendering, PDF/OCR, uploads or automatic material/ratings writes.

Reuse PinnedPublicTransport with public DNS/IP pinning and permitted internal query flag; validate every redirect hop (max2), HTTPS public 443 only, reject private/loopback/reserved targets and URL userinfo. Strict connect/total timeouts, byte and page/result caps; never return arbitrary provider error bodies. Treat webpage instructions as untrusted data. Search-source and page-excerpt provenance are distinct. Web citations are saved owned conversation snapshots, not invented knowledge chunk UUIDs; UI displays web sources/quotes directly. Existing DB citations retain integrity and owner/time/version checks.

## Citation continuity and audit
Assign stable unique [n] across all observations (saved RAG and web), deduplicate by source snapshot, cap total citations10 and per-observation citations to permit multi-stock work. Never regex-rewrite original quotes. Context can carry the shared citation manifest into retrieval; default direct RAG behavior remains unchanged. Validate old saved citations on history; changing a source invalidates its old citation. Basic operational log records node path, intent, tool names/counts, timing, token usage/mode, verdict/reason and terminal status, scoped to identity. No external tracing/data-flywheel service in this chapter.

## Acceptance
1. Saved AAPL price: one tool, incremental final answer, no RAG gate.
2. Two-stock saved-data comparison: multiple observations, complete tool protocol/history, bounded loop.
3. Strong original/analysis/FAQ → gate pass then Agent; absent/weak/service-error → fallback+pool, zero main-Agent/final-generation calls.
4. Missing stock clarifies; invalid classifier JSON safely stops; unsupported writes invoke zero tools.
5. Repeat/budget/deadline/cancel terminate with slots released and accurate persisted status.
6. Actual disposable PostgreSQL AsyncPostgresSaver reopened across instances/processes restores focus/state; identities separated; unique fake credentials absent from all persisted checkpoint/log records.
7. Multi-tool citations maintain unique stable source mapping; source withdrawal/history guards still work; web UI renders external citations without calling knowledge-chunk endpoints.
8. Real bounded public webpage/search probe plus mocked adverse transport tests, SSRF/redirect/size/challenge cases. Real model acceptance separately labeled from fixtures.
9. Full clean backend suite, relevant frontend tests/build, root independent review, GitHub/Pages + immutable API release activation and public health/flow checks. Update both README capability descriptions to multi-step Agent and internet research only after verified.

## Local implementation note — 2026-10-12

The isolated worktree now executes the full graph via SSE custom events, with the read-only tool loop and evidence gate in graph nodes. Fixture-backed PostgreSQL reopen and owner isolation, bounded loop tests, cancellation, web metadata regressions, and local backend/frontend suites have passed. This note does not represent public activation or live model/Tavily acceptance; the original design acceptance remains the target for root's independent review.
