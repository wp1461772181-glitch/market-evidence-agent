# Semantic workflow continuation checkpoint

Status: implementation draft exists; acceptance FAILED. No commit, push, integration or deployment of this draft. User requested immediate wrap-up because quota is nearly exhausted. Preserve the draft; do not restart implementation from scratch.

## Workspace and evidence

- Worktree: `/tmp/market-workflow-20261012/worktree`; baseline `8cfbd5d07a8b59d34c5d87d689c468a4f060cfa9`.
- Approved design: `docs/superpowers/specs/2026-10-13-semantic-workflow-design.md`.
- Plan: `docs/superpowers/plans/2026-10-13-semantic-workflow.md`.
- Incremental notes: `dev-notes/semantic-workflow.md`.
- Worker result: `/tmp/market-semantic-workflow-20261013/result.txt`.
- Real selected-model acceptance output: `/tmp/market-semantic-workflow-20261013/preliminary-live.jsonl`.
- Live helper: `/tmp/market-semantic-workflow-20261013/live_check.py`; the temporary shadow server on 9030 has been stopped.
- Main checkout contains 56 protected learner edits; hashes: `/tmp/market-semantic-workflow-20261013/main-dirty-hashes.json`. Preserve these and the pre-existing untracked `.superpowers/`.

## Confirmed acceptance blockers

1. Real DeepSeek/deepseek-flash returns natural-language task purposes because planner prompt omits the allowed purpose enum. Validator correctly rejects them with `plan_purpose_invalid`, before any news search. Fix the prompt/schema/example, not by relaxing the tool/write whitelist.
2. Ordinary task dependencies are not enforced: a dependent forecast executes after its price parent fails. Enforce dependency status for every task; preserve independent partial results.
3. Pending `import` followed by a source UUID loses the selected action and returns `implicit_write_rejected`. Restore only a server-validated pending continuation, retaining existing owner/action permission checks.
4. Latest financial AI analysis only passes symbol; preserve `financial_only`/form/latest and same-source binding to avoid a newer 8-K replacing a 10-Q/K.
5. Query variants are OR-joined and then truncated to an 80-character keyword; this is not multiple retrievals. Expand knowledge tasks conditionally, execute bounded variants with identical frozen filters, deduplicate evidence. Do not expand web search unnecessarily.
6. Reference validation prevents removal but allows added companies/periods/negations: `AAPL news` can become `AAPL NVDA news` or `AAPL not revenue news`. Validate conservative equivalence, permitting only supported reference completion.
7. Planner schema cannot express per-task source/accession/form/period; global entities are copied to all tasks. Add validated per-task scopes and retain source ownership safeguards.

Independent review reproduced items 2–6 without network or production data. Current-turn result reuse was inspected and preserves selected item IDs/URLs; this does not override the blockers above.

## Verification completed and its limits

Worker reported 816 backend tests passed / 7 skipped before its final topic constraint edit; 20 semantic tests passed after that edit; 71 frontend checks and build passed. These are controlled tests, not proof of real provider compatibility. Root's actual model test failed at planner schema. The shadow server used RAG disabled to avoid loading a second embedding instance; it was not production RAG acceptance.

## Resume once quota allows

Use the existing planner-worker-review terminal runner and SAME worker route (`gpt-6-sol`), sending one focused repair brief containing these confirmed findings. No repair round has yet been used. Root must independently verify the actual diff and affected tests, rerun search-two-Apple-revenue-articles → summarize-those-two → explain-second without new searches, checkpoint reopen, independent multi-company and failed dependency cases, source-bound RAG and pending action continuation. Verify final broad tests after stable changes. Do not publish until accepted.

Then explicitly stage task files only, commit/push, preserve main learner edits while integrating, and publish using the established immutable release process plus Pages CI. Current production remains the prior verified `8cfbd5d` release; no rollback is needed.
