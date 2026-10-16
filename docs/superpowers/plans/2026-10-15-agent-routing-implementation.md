# Agent routing redesign implementation status

Approved scope: [C1–C14 specification](../specs/2026-10-15-agent-routing-redesign.zh-CN.md). Actual starting revision: `81f62be32fbcf9dbbd329e34673aed705567da4b`. Implementation proceeds in an isolated checkout and preserves the 56 protected files in the main checkout.

## Implemented and under final verification

- C1/C2/C6: independent reference and classification stages, first standalone request shortcut, four categories, one direct loop without planner/purpose/DAG routing.
- C3/C4/C5: tool-free B/C behavior, mandatory saved-read/evidence gate for A, shared A/D native tool loop, server clarification and write cards.
- C7/C9/C10: one typed catalogue, argument validation and source ownership checks, explicit freshness arguments, no company alias dictionary in routing/filtering.
- C8/C13: 34 permission-bearing tools mapped to real services; owner database queue and guest browser queue; browser stock/page actions with replay protection.
- C11: distinct success, transient retry, hard call, token and wall-clock budgets; separate stop reasons and retained partial sources; malformed native protocol handling.
- C12: layered context/checkpoint API preserved, whole-message bounded reference history, Simplified Chinese body formatting with original source titles preserved.
- C14: frozen bounded web snapshots; owner/conversation/parameter-bound expiring random confirmation capability; durable execution/replay results; private URL/content deduplication and web_news sync/index/retrieval provenance.
- Independent review fixes: reject unrelated event claims using price citations and reversed event negation, bind punctuation-adjacent references, block rejected source bodies from tool events, freeze accepted web content through confirmation.
- Documentation and migration rationale: workflow/context/READMEs, tool inventory, test migration and source-backed verification report.

## Remaining acceptance and delivery

- [x] Complete the user-selected official DeepSeek-Flash 50-case run with a frozen source manifest; OpenRouter debugging is retained separately.
- [x] Run complete same-model baseline; compare end-to-end latency, quality and call counts without mixing controlled probes or fast failures into success claims.
- [x] Export `cd6e0c6`, verify all 474 blobs and run with the production interpreter: 1224 passed / 7 skipped. Production/test interpreters in the worktree also pass 1224 tests / 7 skipped; frontend 86 checks and production build pass.
- [ ] Update verification report with final artifacts, limitations and costs; independently recheck protected hashes.
- [ ] Make reviewable commits grouped by C IDs, then push and verify CI.
- [ ] Only after all acceptance gates pass, run the specified release/health/Pages/guest-smoke workflow and record the actual deployed SHA.

The task is not complete while any required acceptance or release item remains. Local green tests do not establish model quality, source entailment or production readiness. Current evidence and known failures: [verification report](../../reports/agent-routing-verification.md).
