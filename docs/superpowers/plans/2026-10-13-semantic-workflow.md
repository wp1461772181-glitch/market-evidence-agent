# Semantic Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The terminal worker must not delegate further.

**Goal:** Separately call the selected LLM for task understanding, intent, bounded planning and conditional query expansion; correctly continue prior verified results.

**Architecture:** Integrate strict stage schemas and bounded result sets with the existing LangGraph state/checkpointer. Execute validated dependent tasks using existing tools and confidence checks, then stream one evidence-grounded answer.

**Tech Stack:** Existing FastAPI, LangGraph/AsyncPostgresSaver, SQLAlchemy/PostgreSQL, BGE-M3/Milvus, current provider adapter and React SSE frontend. No dependency/provider/database substitutions.

**Spec:** docs/superpowers/specs/2026-10-13-semantic-workflow-design.md

## Global Constraints
- Use the user-selected provider/model/base URL in separate calls; request-only secrets never enter state, logs, notes, test fixtures or model-profile copies.
- At most four task-plan entries, three retrieval queries (including original), six saved result sets, 64 KB saved-result total. Existing turn budgets/permission/provenance/citation guards remain enforced and include understanding-stage costs.
- Do not touch main learner changes, production DB, real .env/profile/login material, commit, push or deploy in the worker. Disposable test databases only. Primary owns real acceptance and deployment.

## Review Focus
- Same article set after reload/model switch versus expired, invalid or ambiguous legacy references.
- Malicious article text/unknown result IDs/another owner's objects never authorize facts or writes.
- Sequential search→summary and mixed-company partial results maintain distinct dependencies and source scopes.
- Explicit original vs derived analysis, negation/period constraints and optional query expansion preserve meaning.
- Cancellation/provider errors/budget exhaustion surface stage errors instead of unrelated action cards or lost historical results.

### Task 1: Strict semantic stages and reference-result persistence
**Files:** app/agent_workflow/state.py; new focused stage/result modules under app/agent_workflow; app/agent_chat_stream.py; tests/test_agent_semantic_workflow.py and related checkpoint/SSE tests.
**Consumes:** current selected AgentRuntimeContext, same-owner SQL history and checkpoint state.
**Produces:** validated canonical request, real reference IDs and persisted passing result sets; no arbitrary model objects.
- [ ] Write failing regressions for Apple news→summary, second article, legacy verified sidecars, missing/unknown refs and cross-owner isolation.
- [ ] Confirm failures before implementation.
- [ ] Implement bounded stage schemas/calls and result catalogs; fetch current Context7 official LangGraph/provider docs before using APIs.
- [ ] Verify target tests and checkpoint reopen; record dev-notes/semantic-workflow.md incrementally.

### Task 2: Intent, task dependencies and conditional retrieval expansion
**Files:** app/agent_workflow/graph.py; stage modules from Task 1; app/agent_tools.py / app/knowledge/query_understanding.py as needed; tests/test_agent_semantic_workflow.py and existing routing/tool tests.
**Consumes:** validated resolved request and trusted result IDs.
**Produces:** validated intent/task plan, scoped existing-tool execution, independent query variants, verified evidence for final generation.
- [ ] Write failing tests for NVIDIA search→summary, Apple price+NVIDIA news, saved AI/original/compound routes, no implicit writes and protected expansion terms.
- [ ] Implement distinct nodes/calls and acyclic execution; retain permission/source checks and bounded ReAct fallback. Summarizing existing results does not re-search.
- [ ] Verify per-stage usage accounting, failed dependency handling and strict current citation mapping; do not leak failed evidence into answer context.
- [ ] Run targeted backend tests and append concrete results/limitations to dev-notes.

### Task 3: User-visible progress, reference choices and acceptance
**Files:** frontend/src/api.ts / AgentChatPanel.tsx / chat-clarification.mjs / i18n only if needed; existing frontend lifecycle/helper tests; dev-notes/semantic-workflow.md.
**Consumes:** SSE status/clarification/task events; retains current message/citation contract.
**Produces:** plain stage progress, real reference choices, unchanged compact citation/layout behavior.
- [ ] Directly implement minimal frontend changes in the authorized Vibe workflow; test lifecycle guards meaningfully.
- [ ] Run full backend suite, frontend test:i18n, production build and diff check once stable.
- [ ] Return exact changed paths, stage-by-stage evidence and honest real-provider boundaries; no worker commits/deployment.

## Primary acceptance and publication
Primary independently inspects diff, uses a fresh reviewer, reruns focused tests and real DeepSeek/Tavily sequences without exposing keys, then syncs GitHub and immutable backend release/Pages. Verify exact published assets, public health/RAG readiness and preserved learner hashes. Any unresolved important defect blocks success claims and publication.

## Independent plan review additions
Task 1 must exercise legacy gate proof/fingerprints and fail-closed unknown-reference handling. Task 2 must freeze active task scopes and assert no cross-company source IDs, and test stage usage/reserve thresholds including intent repair. These are mandatory invariants from the spec's final section, not optional followups. Read the complete current spec again before final implementation acceptance.
