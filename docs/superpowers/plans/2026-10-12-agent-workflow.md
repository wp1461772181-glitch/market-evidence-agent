# Agent Workflow Implementation Plan

> For terminal worker: use Superpowers executing-plans and test-driven-development; no further delegation. Root owns final independent review.

**Goal:** Upgrade the existing stock-research SSE chat to a persistent LangGraph workflow with bounded ReAct and public web research.
**Architecture:** Code-routed StateGraph, request-only runtime context, built-in PostgreSQL checkpointer, existing visible chat persistence and tools; gate knowledge before answer streaming.
**Spec:** docs/superpowers/specs/2026-10-12-agent-workflow-design.md
**Stack:** Python3.12, LangGraph1.2.14, langgraph-checkpoint-postgres3.1.2, existing FastAPI/PostgreSQL/BGE/Milvus/LangChain/React.

## Constraints and review focus
- Preserve dirty learner code in the main checkout. Work only in this clean worktree; do not edit private production config or source data.
- Main HTTP API/provider SSRF/identity protections, frozen originals and RAG guards remain. No formal coreference, automatic material publishing/rating, fine-tuning or broad UI redesign.
- Review secrets in checkpoint/config metadata, incomplete cancelled turns, ordinal collisions, estimator-vs-actual budget, knowledge/web bypass after first observation, malformed classifier/tool protocol, ownership on resumed threads, and side effects of untrusted pages.

## Task 1 — Foundation, routing and durable state
Files: create app/agent_workflow/{state,routing,checkpoints,settings}.py (or similarly focused modules); dependency pins in pyproject.toml; additive scripts/migrate_agent_workflow.py; tests/test_agent_workflow_persistence.py.
- [ ] Read actual Context7 receipts before APIs; prepare isolated development environment; record baseline700/7.
- [ ] RED: strict JSON labels/missing entity/focus switching + actual PG reopen/private-key absence.
- [ ] Implement TypedDict State and Runtime context; built-in AsyncPostgresSaver setup/lifecycle (no production InMemory fallback).
- [ ] GREEN: real disposable PG restore/isolation and routing sample evaluation; no raw secrets in state/config/logs.

## Task 2 — Public web research
Files: app/agent_web_research.py; registry/executor additive changes; tests/test_agent_web_research.py.
- [ ] RED public URL/query contracts, nonpublic DNS/redirect, resource limits, challenge and metadata timestamps.
- [ ] Add one @tool, search adapters, bounded safe text parser/transport; provider key isolated in environment/runtime.
- [ ] GREEN transport cases and real anonymous public page read; report search limitations honestly.

## Task 3 — Confidence, bounded ReAct and streaming integration
Files: app/agent_workflow/graph.py, agent_chat_stream.py, agent_chat_provider.py, existing knowledge/runtime.py+retrieval.py if shared ordinals needed; tests/test_agent_workflow_graph.py and affected existing SSE tests.
- [ ] RED graph-node path, one-tool convergence, multi-observation comparison, pre-gate zero-Agent, repeated tools and budget/deadline/cancel.
- [ ] Compile code-routed StateGraph and custom SSE streaming, per-turn resets and operational logging/low-confidence reasons. Keep visible conversation rows/history.
- [ ] Add actual/estimated usage caps using provider completion budget; all protocol messages bounded and coherent.
- [ ] GREEN weak evidence is normal fallback+done, business skips gate, one read-only tool reused per loop, no extra business writes.

## Task 4 — Citation/history UI
Files: frontend/src/api.ts, AgentChatPanel.tsx or focused web citation component, locales, relevant frontend scripts/tests.
- [ ] RED multi-RAG/web ordinal collisions, literal original quotes preserved, history withdrawal and external-source display.
- [ ] Add typed web citation snapshot rendering; reuse current layout/tool badges and retain DB citation revalidation.
- [ ] GREEN relevant front tests/build + actual screen walkthrough; no overall frontend redesign.

## Task 5 — Full acceptance and handoff
Files: dev-notes/ch05.md (append each milestone), docs/agent-workflow.md, both README language variants, isolated test/demo command if needed.
- [ ] Run labeled routing/gate samples, full backend, relevant front tests/build, actual PG checkpoint restart and real public web test. Keep simulated provider vs real-model evidence distinct.
- [ ] Return diff/task-path manifest, test receipts, migration commands, safe runtime dependencies, screenshots if available. NO staging/commit/push/deploy before root independently accepts.
- [ ] Root reviewer requests code review then independent fresh acceptance; one focused correction through same worker if needed.
- [ ] Authorized publish uses explicit task files, preserves main learner edits; apply additive checkpointer migration and minimal dependency additions, then immutable API release + Pages. Never claim live success before actual public verification.

## Worker implementation status — 2026-10-12

Tasks 1–4 have fixture-backed implementation in this isolated worktree: actual PostgreSQL saver reopen/owner isolation, bounded public web adapter, graph-owned SSE/ReAct path, and citation/history UI continuity. Task 5 local backend/frontend checks passed; authenticated Tavily, real provider, independent review, migration on the deployment database, and publication remain with root. The worker did not commit or deploy. See `dev-notes/ch05.md` and `docs/agent-workflow.md` for checks and limitations.
