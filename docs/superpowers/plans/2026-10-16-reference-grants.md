# Reference resolution and write grants implementation plan

> **For agentic workers:** Use superpowers:executing-plans. The primary model implements inline; one independent whole-branch review follows.

**Goal:** Fix the B-answer/reference dead ends and grant only user-requested or server-approved write tools.

**Architecture:** Keep the direct workflow, evidence gate and consent executor. Separate B generation from routing; validate copied referents before classifying, then validate only A/D business scopes. Derive write grants from the classifier and catalogue; use the existing durable consent capability for agent suggestions, including a separate content-confirmation stage for web ingestion.

**Tech stack:** Python/FastAPI/Pydantic/LangGraph/PostgreSQL; React/TypeScript; official DeepSeek-Flash.

**Spec:** [Approved user request](../specs/2026-10-16-reference-grants.md).

## Global constraints

- Base: origin/main c9a75bc; deployed rollback: fc58db3-verified.
- Worktree /tmp/market-reference-grants-20261016; preserve the 56 learner-file hashes.
- Do not change unsupported_numbers, CLAIM_FEATURES, evidence-gate rules, or consent execution/locking/idempotency semantics.
- One development cycle and at most one production rollout/restart; no failed-run replacement.
- Official model deepseek-flash, thinking enabled, reasoning_effort=low; cumulative new evaluation cap USD 0.50.
- No API keys in state, audit, SSE, git or evidence artifacts.
- Guest local queue actions remain allowed; server business writes and request_action remain owner-only (user confirmed).
- Literal scope grounding permits a verified company/ticker correspondence (user confirmed); no hand-written company alias table.

## Review focus

- A referent quote from the wrong message or unowned result set must not become a target.
- Missing first-turn lists and several plausible lists must request clarification rather than guessing.
- B retries must be bounded and never expose tools or mark empty output successful.
- A suggestion approval must not bypass frozen-content confirmation or repeat external preparation on replay.
- Tool-output injection, negated writes, forged tool calls and cross-owner card clicks must not create server writes.

### Task 1: A1–A4 reference and B pipeline

**Files:** direct.py, routing.py; new references.py; new tests/test_agent_reference_v4.py.

**Interfaces:** Reference.referents; validated source text; audit.stages; B independently generated from resolved question and bounded history.

- [x] Add failing tests for B empty/retry/protocol and copied referent/forged target/scope checks.
- [x] Implement source-normalized referents, classifier-only scope, separate B generation, and bounded stage audit.
- [x] Run focused and full backend suite; record legacy answer-field test migration; commit A1–A4.

### Task 2: B1/B2/B4 grants and tool exposure

**Files:** agent_tool_catalog.py, routing.py, authorization.py, direct.py; new grants.py; new tests/test_agent_grants_v4.py.

**Interfaces:** catalogue-derived WriteTool enum; requested_actions; per-turn immutable grants; write_tool_not_granted errors; normalized negation veto.

- [x] Add failing tests for synonyms, invalid enum filtering, unexposed writes, negation, guests and result-set action grants.
- [x] Register write functions only when granted; list remaining operations as unavailable descriptions; check every native invocation before dispatch.
- [x] Preserve guest browser queue operations as client-local actions. Check structured entity mappings against verified data, not capitalization heuristics.
- [x] Run focused and full backend suite; commit B1/B2/B4.

### Task 3: B3/B5/B6 server suggestion consent

**Files:** grants.py, direct.py, tools.py, AgentChatPanel.tsx; backend consent tests and frontend tests.

**Interfaces:** request_action(tool, arguments, reason); existing server capability values; suggestion display marker; audit.write_outcome.

- [x] Add failing tests for one suggestion per turn, typed arguments, cancellation, owner/session/expiry, replay/concurrency and no preapproval side effects.
- [x] Reuse consent.propose/execute without changing their semantics. Capability metadata is the only proposal-stage DB write; business data and external fetches remain untouched.
- [x] For ingest_web_source, approval consumes a grant capability and produces one durable, frozen-content confirmation card; replay returns the same result/card.
- [x] Preserve confirm/change/cancel/reply compatibility and support server-mapped authorize values; explain refusals instead of falling back to empty-evidence prose.
- [x] Add suggestion label to existing card UI and tests; run backend/full frontend checks; commit B3/B5/B6.

### Task 4: Evaluation, review and one release

**Files:** scripts/evaluation/cases/agent-routing-v4.jsonl, evaluation runner/fixtures; inventory, migration, v4 verification report.

- [ ] Add v4 model scenarios, ten consecutive repetitions each, deterministic safety probes and original 50-case comparison. Preserve source/case hashes and all failures; enforce the cumulative USD 0.50 ceiling before each request.
- [ ] Check >=9/10 required success rates, zero direct injected-write proposals, <=1 labelled suggestion, original 50/50 and 47/47, single-source median <=3 and p50 <=5194.2 ms (4722*1.1).
- [ ] Run full backend in production and independent interpreters; frontend test/tsc/build; independently review whole branch, fix meaningful findings with regression tests.
- [ ] Update reports with actual results, test migration, costs, scope decisions, and three owner self-test steps. Push grouped commits and verify CI.
- [ ] Only if acceptance gates pass: verify Git snapshot blobs, restart once, health warm-up/local/public checks, Pages, public guest smoke, protected hashes. Rollback target remains fc58db3-verified.

## Decisions and progress

- User confirmed verified ticker mapping and guest-local queues on 2026-10-16.
- Base suite: 1270 passed, 7 skipped with production interpreter; source baseline is clean.
- Consent capability persistence is required by the spec; "no proposal-stage DB writes" means no business data writes, not suppressing the capability ledger itself.
- All future design changes and deviations must be recorded here and in the final report before implementation.
- Entity mapping implementation: classifier may attach `company_mention`, copied literally from allowed user/referent context, while `company`/`symbol` name the interpreted issuer. The server verifies the English issuer/ticker pair against official SEC metadata; translation of the original mention remains the classifier's semantic responsibility. Failure to verify asks for clarification. This is the user-approved mapping exception to literal symbol matching, not a company alias dictionary.
- User further confirmed: ALL owner/server writes, including reversible queue/relevance changes, require a server confirmation capability. Guest browser-local queue remains immediate. Original v3 R16/R27 owner queue expectations will be changed to confirmation cards; no silent exception to the token invariant.

- A-only export 3a544532 passed 1289/7 before commit 2a09139. Literal issuer mismatch counterexample passed after 82a412e.
- Independent reviewer found a caller-controlled confirmed boolean bypass; removed it and checked durable executing consent+exact operation in the internal reversible dispatcher. RED→GREEN evidence retained.
- Interim complete suites passed 1306/7 in both interpreters; final post-review suites are running. Frontend 87 tests and tsc/Vite build pass. One source freeze precedes paid model acceptance; no production release has happened.

- User approved earlier assistant list sources by message ID. Implemented internal assistant_message IDs in bounded history and exact current-conversation quote validation (15a831a); V02 continues actual V01 conversations (1280881). Full post-change suites: 1316/7 in both interpreters. Independent follow-up provenance review found no blocker (32 focused checks).
- Native first full corrected-harness trial: 29/50, natural27/47; preserved before over-rejection corrections. First v4 source trial retained incomplete after user approved source extension; latest v4 source run in progress on1280881. Same cumulative ledger, no failed-result replacement.
