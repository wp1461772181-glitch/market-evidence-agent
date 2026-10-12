# Agent routing and reliability implementation

Authority: user-approved requirements snapshot `../specs/2026-10-12-agent-routing-requirements.md`. Implement all 16 acceptance scenarios; P0 first, then P1 in this authorized run. LangGraph 1.2.14 and built-in PostgreSQL saver remain fixed. No destructive action, unrelated learner edits, credential migration, concurrency expansion, or implicit business writes.

## Baseline and confirmed defects

- Isolated worktree `/tmp/market-workflow-20261012/worktree`, baseline 3effdb0; 752 backend passed, 7 skipped. Main has 65 unrelated dirty paths, preserved.
- Read-only reproduction of the actual failed AAPL turn against `deepseek-flash`: HTTP 400 says thinking-mode `reasoning_content` must be returned. Synthetic assistant tool requests are not genuine model messages and cannot manufacture missing reasoning. Root privately performed this request; no key is in this plan.
- AAPL analysis exists in SQL while its semantic chunks are pending; exact summary must not require a vector hit. Apple news body uses company name rather than ticker. Clarification must retain the pending task and source scope.

## Task 1 — provider compatibility and typed failures

Modify `app/agent_chat_provider.py`, `app/agent_chat_stream.py`, graph final-answer integration; add focused provider tests. Verify latest official DeepSeek docs and Context7 before library/API usage. Introduce typed safe provider failure with stage, HTTP status, provider request ID, sanitized reason and retryability; do not retry unchanged 400. Capture finish reason and actual usage from both final-choice and usage-only frames. Distinguish structured JSON classification from tool decisions and final generation; avoid unnecessary empty tools/auto fields. Genuine model tool messages retain exact bounded continuation fields and paired IDs. Application-prepared evidence is supplied as an explicitly untrusted evidence envelope, not fabricated model tool-call protocol. Normalize legacy incomplete protocol history without inventing reasoning or silently discarding visible conversation. Prevent partial protocol markers leaking into output, including markers split across chunks. Do not globally disable thinking to disguise the defect; any explicit stage mode has a documented rationale and tests.

## Task 2 — classification, slots and pending requests

Modify `app/agent_workflow/routing.py`, `state.py`; create focused parsing/resolution module(s), integrate graph. Classifier response is exactly intent/confidence, eight labels from spec; one fenced-JSON unwrap and one bounded repair request. Slots are separately validated: symbols, form, period, accession, source ID, action, topic and explicit source requirements. Use deterministic validated aliases and ordinary exact commands to avoid needless ambiguity; arbitrary tickers still supported. Pending slot replies resume original request; explicit new requests replace it. APPL correction is a selectable suggestion, not silent replacement. Multiple-company selections fill only missing slots. Persist original/canonical question, pending request ID, selected scopes and task states without credentials.

## Task 3 — precise saved source reads and scoped retrieval

Modify `app/agent_tools.py`, `app/knowledge/runtime.py`, `retrieval.py` as needed; introduce source-selection module with SQL tests. Source resolution uses explicit accession/form/period first, otherwise filed/published date; financial report means 10-Q/10-K, not default newest 8-K. Select latest valid analysis of that source. Extend exact reads to verified source IDs and form/date filters while preserving existing provenance, content hashes, ownership and cutoff rules. Support scoped semantic retrieval by source ID/version; do not filter only by stock when user selected a document. Return distinct no-source/no-analysis/stale/index-pending/service-unavailable conditions. Exact reads and semantic alternatives can use stored original passages when indexing unavailable, preserving partial coverage and citations. Do not substitute derived summaries for original facts. At most one alternative lookup per subtask; query expansion at most three within aggregate turn limits and deduplicated citations.

## Task 4 — deterministic five-route graph and partial results

Refactor `app/agent_workflow/graph.py` with small focused modules where useful, retain real LangGraph nodes/checkpointer/SSE. Build validated `Subtask` records (task ID, route, source scope, status, observations, error, result IDs); deterministic route order from spec. Comparisons and compound source requests fan out across stock/source/topic; each evidence gate checks its own question and scope. One missing subtask does not erase other verified results. Web queries use canonical bilingual company/topic terms and aliases; evidence checking recognizes company names without requiring ticker text, retains requested financial metric/period/date checks, rejects unrelated bodies. Gate new observations before facts stream. General greeting and capabilities can be deterministic; substantive general uses no business tools. Statuses distinguish succeeded/partially_succeeded/failed/waiting_for_input/cancelled; deadline/cancel preserves useful results. Retain five tools, six decisions, 120s and 32k default configurable budgets, repeated normalized arguments guard, global source ordinal integrity.

## Task 5 — authorized system tasks and idempotency

Create `app/agent_workflow/actions.py` and a small durable task adapter/registry as needed with explicit additive migration. Reuse existing functions in SEC fetch, `material_analysis.request_material_analysis/get_job`, `forecast_v2_api` or service-level enqueue, and knowledge import/index functions. Required operations: fetch/refresh inventoried SEC original, request analysis for an exact source, import/index selected existing materials, enqueue a prediction, read their status. No localhost self-HTTP calls that bypass authentication. Every execution validates actual runtime owner credential and source ownership, not an LLM assertion or caller-supplied owner hash. Guest cannot write; regular explicit owner action requires no second operation password. Validate source and options before executing; destructive actions remain outside automatic tool scope. Stable request/action idempotency binds payload and owner, returns existing job on retry, detects payload mismatch, and maps actual underlying job status. Queued/running is never reported as completed. Keys remain outside State/job payload/logs.

## Task 6 — chat selection cards, status and error display

Modify `frontend/src/AgentChatPanel.tsx`, `api.ts`, chat types/local archive/i18n helpers only as needed. Stream clarification cards with safe stock/source/form/period choices bound to pending request ID; free text also supported. Click resumes same pending task; server validates chosen option. Show tool names/status/short result before answer, no raw arguments/protocol JSON. Render job cards and truthful waiting/partial/failure status; retain citations and partial text on errors. Existing table rendering, copy, history and user-right/assistant-left alignment are preserved. Add meaningful front lifecycle/card/error tests; no full UI redesign.

## Task 7 — acceptance and documentation

Map all 16 spec rows to tests and acceptance script(s), plus real saved-record source resolution on AAPL/AMD and actual DeepSeek flash protocol tests performed privately by root. Use disposable PostgreSQL for mutation/idempotency/ownership/resume tests. Inspect checkpoint and SQL records for fake secret sentinels, owner isolation and complete traces. Test no-repeat writes after cancellation/retry, exact source selection, malformed classifier recovery, index-pending direct read, Chinese web aliases, compound partial evidence, citation restore and budget release. Run full backend, front tests and production build. Append `dev-notes/ch06.md` incrementally at task/test/review milestones; report remaining external limits honestly. README/docs describe only accepted functionality. Worker never reads real env/profile/login credentials, performs production writes, pushes or deploys. Root independently reviews and repeats real provider/browser/public acceptance before any publication.

## Review focus

- Synthetic tool history versus genuine thinking-model continuation; old failed/successful conversations remain usable.
- Exact accession/period source cannot silently change to another latest record; original and derived evidence remain distinct.
- Pending correction and multiple-stock switches do not contaminate source binding or grant permission.
- Partially complete output never makes unsupported comparison claims, and protocol/error chunks never leak raw markup or keys.
- System-task retry can neither duplicate a real mutation nor announce an uncompleted durable job as success.
