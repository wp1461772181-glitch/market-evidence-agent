# Saved Analysis RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans task-by-task. Terminal execution worker is the delegation endpoint; no further delegation. User has authorized direct implementation; planner-worker-review owns independent final acceptance.

**Goal:** Make eligible saved AI analysis searchable with provenance and permission checks.
**Architecture:** A dedicated analysis source adapter shares the existing PostgreSQL-to-Milvus import/index pipeline. A separate analysis retrieval kind/tool preserves original-only material search.
**Tech Stack:** Existing FastAPI, SQLAlchemy/PostgreSQL, LangChain tools, pinned BGE-M3 and Milvus; no dependency upgrades.
**Spec:** docs/superpowers/specs/2026-10-12-analysis-rag-design.md

## Global Constraints

Preserve unrelated dirty learning/comment edits and original records. No deletion, credentials in output, new financial predictions, paid provider calls, Agent Loop or unrelated fallback fix. Consult Context7 MCP receipts before library use. Source and derived analysis remain distinguishable.

## Review Focus

- Older analysis encountered later in UUID pagination must not replace newest.
- A valid analysis quotation can become stale when original/payload/ownership changes.
- A private upload can have multiple analysis versions; scope must survive every access path.
- Adding a retrieval kind must not contaminate original-only or FAQ queries, or omit the ANN prefilter.
- Analysis generated after a requested cutoff must not appear earlier; snapshot/current citation rules remain fail-closed.

### Task 1: Analysis source and automatic sync

**Files:** create app/knowledge/analysis_sources.py; modify app/knowledge/source_sync.py and scripts/sync_knowledge_sources.py; create tests/test_knowledge_analysis.py; adjust tests/test_material_sync_acceptance.py only for the new fourth source.
**Interfaces:** `load_analysis_document(session, analysis_id, *, cutoff=None, require_latest=True) -> SourceDocument | None`; source_type=ai_analysis; source_table=material_analysis_versions. Stable source_id per original material, source_version analysis UUID. Sync source_type=analysis, bounded 5 per cycle.
- [x] RED/GREEN focused tests: all six analysis groups with provenance/exact quotes, invalid quote and future timestamp fail closed; analysis ANN prefilter and derived citation metadata; stale current analysis fails closed. Broader mutation/ownership/version cases remain for independent gate.
- [x] Implement using existing schema/frozen-source contracts and latest-version selection.
- [x] GREEN targeted adapter/sync tests. Record RED/GREEN and corrections in dev-notes/analysis-rag.md.

### Task 2: Analysis retrieval and citation contract

**Files:** app/knowledge/retrieval.py, app/knowledge/api.py, app/agent_tools.py, app/agent_tool_executor.py, app/agent_chat_stream.py; tests/test_knowledge_analysis.py and focused chat tests.
**Interfaces:** `retrieve_analysis(symbol, query, limit=5)`; `search_material_analysis` tool; admin source=analysis and retrieval kind=analysis. Reuse existing result envelope and add optional derived metadata to citation detail where needed.
- [x] Focused retrieval checks: analysis-only ANN source prefilter, derived result metadata, citation source URL/version and fail-closed stale-source helper. Full DB-backed mutation and history cases remain for independent gate.
- [x] Implement analysis-specific prefilters and authority revalidation; original/FAQ semantics unchanged. Extend knowledge timeout and quality routing to the new tool.
- [x] Focused backend tests passed; clean-archive full backend gate is left to primary. No live provider test enabled.

### Task 3: Minimal UI, real indexing and publication

**Files:** frontend/src/KnowledgeAdminPage.tsx, necessary existing API type/locales, frontend/src/AgentChatPanel.tsx only for meaningful derived label/detail; docs/dev-notes.
- [x] Add saved-analysis import choice and analysis retrieval test; show derived label, version/model and original quotation details. Frontend uses the user-authorized direct Vibe Coding approach, no new design flow.
- [x] Frontend production build passed. Real disposable PostgreSQL/BGE/Milvus AMD acceptance was not run because no analysis fixture path/runtime acceptance config was supplied to this worker; do not touch production until reviewed.
- [ ] Primary independently review; worker makes focused corrections if needed.
- [ ] Publication/live import/index intentionally deferred to the primary publication phase; no service or production data was changed here.
- [ ] Deliver source summary, tests, release, and dev-notes path.
