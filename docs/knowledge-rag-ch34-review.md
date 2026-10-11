# Chapter 3/4 independent acceptance review

## Initial review — 2026-10-11

Status: **not accepted; corrective pass required**. The user authorized immediate execution of the adapted stock-research design. This review concerns the local worktree only, not the published production release.

Independent evidence: inspected the actual implementation, requested a read-only code review, and freshly ran `tests/test_knowledge_quality.py`, `tests/test_knowledge_api.py`, and `tests/test_knowledge_hybrid.py` (9 passed). Frontend tests/build ran successfully (63 frontend checks). Those existing tests did not cover the following defects.

| Finding | Required correction |
|---|---|
| Candidate approval only changes status | Publish a managed product FAQ and queue chunks/index tasks through the existing review helper; show pending indexing honestly. |
| Withdrawn document detail still returns source text | Share active, visibility, owner, integrity and source-current checks across document/citation reads. |
| Citation array is not a numbered model evidence contract | Assign stable ordinals after whole-chunk budget selection, send the same numbered evidence to the model, persist an immutable citation manifest. |
| Pending public candidate is readable by any session | Restrict pending knowledge to owner/operator; publish only after approval. |
| Citation restoration omits chunk hash/generation checks | Verify stored manifest, current source and chunk integrity; preserve unavailable ordinal placeholders. |
| Existing Milvus collection skips schema verification | Validate required fields, dimensions, analyzer, BM25 function and indexes before readiness. |
| Refusal/schema/migration recovery is incomplete | Correct missing message role arguments, require low-confidence table, repair legacy FAQ availability through explicit additive migration. |
| CPU model swap can remain incorrectly ready after failure | Make model release/rerank/reload exclusive and fail readiness if restoration fails. |
| Eight-query report is a component benchmark | Add production-path evaluation with fixed labels, meaningful positive/negative counts and buckets; distinguish retrieval marker diagnostics from generated-answer faithfulness. |
| Admin/demo lacks a complete retrieval path | Add direct supported upload and retrieval test; use an isolated RAG-enabled test database for browser acceptance. |

The earlier reranker kwargs defect was removed during initial implementation and is not an open finding in this review. The initial full backend run reported 606 passed, 15 failed, 3 skipped; the actual failures require investigation and cannot be called a passing suite.

One focused correction brief was prepared for the same `gpt-6-luna` terminal worker. Production port 8000, release pointer, developer database, original collections and original Chapter 3 labels must remain intact. No high-concurrency validation is required in this round.

## Initial acceptance checkpoint (historical)

Pending corrective implementation, fresh targeted checks, production-path four-strategy results and CUA desktop/mobile inspection. Do not infer completion from the initial component benchmark or historical test counts.

## Additional primary review during finishing — 2026-10-11

Two important spec gaps require closure before acceptance:

The reviewer raised a possible compact/spaced JSON citation boundary. Primary reproduction against the actual current source disproved the claimed second truncation: `bounded_tool_result` already counts compact JSON, and a compact 7929-byte/spaced 8005-byte five-row fixture retained all rows and excerpts unchanged. The exact tool-content serializer still uses spaced JSON, so its serialized size can exceed the nominal compact 8000-byte budget by whitespace; this is a narrower serialization consistency concern, not proven citation loss. The reviewer was asked to recheck current workspace code rather than an installed/stale package before assigning severity.

1. `app/agent_chat_stream.py` SYSTEM must explicitly require citation numbers from actual numbered tool evidence, forbid invented/remapped citations, guaranteed returns/direction, stars as rise probability, and stored excerpts as a full financial report. The sufficiency judge alone does not impose this final-generation contract. Add prompt-inspection regression and bounded forbidden-output acceptance.
2. `_head_tail_order` currently yields `[r1,r4,r2,r3]` for four ranked rows. Required strongest evidence at the final sequence ends: e.g. `[r1,r3,r4,r2]`. Assign positions from relevance order, with a regression for at least four entries, and retain stable evidence/citation numbering after budget.

### Developer database migration audit

The finishing worker accidentally invoked the additive knowledge migration without an explicit test database URL during import diagnosis. Primary performed read-only inspection of the local `market_evidence` database: all seven knowledge/low-confidence tables contain zero rows. As the migration does not remove rows and its only data repair targets knowledge FAQ chunks, there were no FAQ chunk records in this target for it to repair. Schema additions may have occurred; no before-schema snapshot exists, so exact schema attribution remains unavailable. No speculative rollback was attempted. Audit: `/tmp/market-rag-ch34-20261011/developer-db-readonly-audit.json`. The live release pointer remains `136c48e7703510897ff013a330a1ac853e6ef2af-verified`, port 8000 remains PID 63297; neither was switched by this work.

## Focused correction pass — 2026-10-11

The requested corrections were implemented and exercised. Targeted backend tests finished **43 passed**; explicit real PostgreSQL/Milvus/BGE/reranker production-path Chapter 4 gate finished **1 passed** and wrote `data/knowledge/ch04/real-four-strategy-report.json`. Frontend i18n tests finished **63 passed** and build succeeded (691.16KB JS bundle warning). Syntax and `git diff --check` passed.

The four-strategy retrieval quality result does **not** meet a useful acceptance bar: heldout Recall@5/MRR were .167/.167 for every strategy, with 6 positive and 6 negative queries each; false positives were 5/6 dense, 4/6 BM25, 5/6 hybrid and 5/6 hybrid_rerank. Scope/stock leak count was zero. Generated claim-level faithfulness was not evaluated. A bounded live DeepSeek equal two-case subset succeeded for dense/BM25/hybrid but hybrid_rerank timed out on its FAQ case before generating an answer. The earlier four-case live result is historical only.

Full backend pytest remains **621 passed, 15 failed, 3 skipped**. Failures are in existing public-access middleware/config expectations and market data ingestion/snapshot/revision and v2-price tests with shared test database data; they were not suppressed or fixed as unrelated behavior. Local isolated launcher syntax is valid, but its smoke run stopped safely with exit 2 because the execution environment did not supply `CH34_TEST_ADMIN_DSN`; no API/UI service was launched and no listener on ports 8018/5180 was found. Primary browser/CUA acceptance remains pending. This implementation is therefore reviewable but not fully accepted, and the quality gate is not passed.

## Additional finishing corrections — 2026-10-11

- The stream SYSTEM prompt now explicitly specifies numbered tool-evidence citations and the requested financial limitations. Prompt text inspection and five bounded synthetic forbidden-output cases were added. These prompt tests do not prove model compliance or claim-level factuality.
- `_head_tail_order` now gives `[1,3,5,4,2]` for five relevance-ranked rows and `[1,3,4,2]` for four. Retrieval builds/assigns citation ordinals and applies byte-budget truncation in relevance order before rearranging the retained complete rows, so weaker rows cannot displace stronger evidence and citation ordinals remain bound to their original manifest rows.
- Admin upload timestamps now initialize from local wall-clock fields and validate local input before ISO UTC serialization.
- Full verification for this finishing pass is recorded below after execution. Browser CUA and real-provider behavior remain primary-owned; the task-only simulated adapter is kept under `/tmp/market-rag-ch34-20261011/browser_demo_app.py` and does not call a provider for the fixed fake profile.

Final verification for these finishing changes: focused chat/retrieval regressions **22 passed**; frontend `npm run test:i18n` **63 passed**; frontend production build succeeded with the existing >500KB main-chunk warning; relevant Python compilation, launcher shell syntax, and `git diff --check` passed. Final full backend suite: **625 passed, 22 failed, 4 skipped, 41 warnings**. Failures remain in legacy public API access/config expectations and shared-database dependent market/evidence/SEC/material API cases; none are in the focused citation/order regressions after updating the contract assertion. This is not a green full-suite result.

The isolated simulated-provider adapter was prepared at `/tmp/market-rag-ch34-20261011/browser_demo_app.py`. No local API or web process was started in this worker: this execution environment had no injected test DB/operation-key environment variables, and the existing profile's tested state is intentionally left for the primary's guarded UI hello action. No migration was rerun, no demo DB was reset, and no uploaded private document was inspected or modified. Primary browser CUA remains pending.

## Final independent acceptance — 2026-10-11

**Local implementation accepted; production rollout and broader quality claims are not accepted by this report.** This section supersedes the historical checkpoints above. The primary inspected current source/diffs, not a stale installed package, and the same implementation worker closed the citation budget, head/tail ordering, SYSTEM constraints, and local-time input defects. The independent read-only reviewer found no remaining Critical/Important defects within the reviewed scope.

Fresh primary verification after the final serialization change:

- `PUBLIC_API_MODE=false .venv-rag/bin/python -m pytest -q --tb=short`: **648 passed, 4 skipped, 41 warnings**, 17.20s. The skipped real-service/provider gates were separately exercised as reported below; ordinary pytest does not prove real-model quality. Earlier auth-related failures came from running with inherited public-mode configuration; final verification uses the explicit intended test configuration without disabling production authentication.
- Focused hybrid/API/chat/tool-timeout regression: **22 passed**. The actual stream now persists/sends the same compact JSON tested by the 8000-byte budget, with complete retained evidence and stable ordinals.
- Frontend `npm run test:i18n`: **63 passed**. Production build succeeded; the main bundle retains the >500KB advisory warning.
- The five original Chapter 3 fixture SHA256 values are unchanged. `git diff --check` passed. No Git commit, push or online release was performed.

### Actual browser acceptance

Primary used CUA against loopback ports 5180/8018 and `test_market_rag_ch34_demo`. Uploaded an explicitly fictional AAPL Markdown document with a FY2025 table (321 million USD), imported it, viewed pending chunks, and indexed **5 chunks with 0 failures**. Retrieval used actual PostgreSQL, Milvus, BGE-M3 and the pinned reranker. The returned evidence preserved year, amount, units and source path.

Primary then tested the fake profile via the guarded local hello endpoint, submitted a question in the actual chat, saw the tool status before the answer, a rendered table, and a clickable `[1]`. Clicking opened the current authorized source excerpt and heading path. Copy returned the complete answer Markdown. 👍 became “已反馈”, both choices locked, and a reload restored the persisted message, citation mapping and local feedback. On 390px the document scroll width was 390px; on 1280px it was 1280px. Desktop user bubble width was 434.3px against an 868.6px answer column; assistant background was transparent with no border. The browser viewport override was reset after inspection.

**Browser final generation was explicitly simulated**, using a task-only adapter outside the repository and synthetic evidence; it is not real-provider faithfulness evidence. Screenshots: `docs/knowledge-rag-ch34-reports/knowledge-chat-desktop.jpg`, `knowledge-chat-mobile.jpg`, `knowledge-admin-mobile.jpg`. The normal launcher does not enable this adapter. The local demo is kept for review only.

### Real evaluation and limits

The actual four-strategy retrieval report uses a fixed tiny synthetic fixture and real PostgreSQL/Milvus/BGE/reranker. Each heldout strategy had **6 positive and 6 negative queries**: dense Recall@5/MRR **0.667/0.667**; BM25, hybrid and hybrid+rerank **1.000/1.000**. Owner/stock leaks were zero in the fixture. Dense therefore **did not reach the 0.85 target**. Hybrid and hybrid+rerank still returned candidates on **5/6 negative questions**; this is a candidate-stage result, not an answer hallucination rate. It shows why sufficiency/refusal remains necessary and why wider real-material validation is still needed.

Dense threshold selection used only the calibration split (heldout excluded). A fixture-name-to-source-UUID mismatch was fixed in the evaluator; original labels were not altered to improve the score. A real candidate approval→FAQ→chunk→vector→retrieval test passed separately.

Two real DeepSeek hybrid+rerank prompts passed bounded number/year/unit/source/citation checks. One prior attempt failed at the sufficiency call and passed on retry; these are point-in-time two-case checks, not a reliability estimate. The offline four-strategy report **does not evaluate all-claim Faithfulness**, and bounded checks do not supply a formal hallucination metric. These quality limits are deliberately left visible, rather than declaring all strategies production-ready.

### Runtime and migration boundary

The live release symlink and port-8000 PID remain unchanged (primary readback: `136c48e7703510897ff013a330a1ac853e6ef2af-verified`, PID 63297). Developer DB schema may have received the accidental additive migration described above; all seven affected knowledge tables were empty at the read-only audit, so there were no FAQ records to repair. No rollback, reset or deletion was attempted. Keep this incident in delivery notes; do not describe the developer database as wholly untouched.
