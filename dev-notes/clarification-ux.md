# Clarification and saved-material read fix

## Scope and evidence

- At `aa74dcc`, the safe conversation audit shows a news-to-library upload request followed by two attempts to read Apple's latest saved material. The old `missing_action_type` checkpoint swallowed the read request and resumed the upload question.
- The upload of arbitrary web news is unsupported. Existing import works only for a registered saved source ID or accession. No web upload adapter is being added.
- Owner credential enforcement for write actions, checkpointer, provider, and provenance flow remain in place.

## Stage 1: failing regressions

Cover a legacy pending operation, selected operation through source entry, comparison stock continuation, unsupported web upload, and persisted clarification choices. Frontend checks cover card selection, freeform reply validation, locking, and read-only archives.

## Stage 2: implemented behavior

- Pending state resumes only for a value matching the expected slot. A complete read request, including “那你去看下资料库中苹果最新的资料说了什么” and “can you check Apple saved materials”, starts a fresh read turn and clears the old stock, source, action, and pending scope. Plain `AAPL`, `就是AAPL`, and a second comparison symbol still continue.
- Operation cards offer stable action values. The selected action lives in pending state while source, stock, or task ID is collected. The pending UUID survives slot fills. The server checks a supplied pending UUID against the current checkpoint; the action executor still checks actual owner credentials for writes.
- A request to upload retrieved web news ends with an explicit unsupported message. It creates no source ID, import, media upload, index, or write task. Existing registered-source import remains available.
- Saved material reads use the existing source route. Generic library reads first request the latest saved SEC source as a fallback when the knowledge index has no usable result; the existing citation/provenance path labels original and derived content.
- The card has a title specific to its missing slot, choice buttons, and its own Unicode freeform input and submit button. Both card paths call the same SSE sending function. Prior, answered, and imported archive cards are locked; the main composer remains available. SQL stores the emitted choices, and legacy empty action cards regain the option list on restore.

## External boundary

These tests use an in-memory checkpointer or mocked external tools and the disposable PostgreSQL test fixture. Real DeepSeek and running RAG acceptance belongs to the primary reviewer. No production database or personal login material was accessed.

## Checks

- Failing tests were observed first: backend import of `is_pending_reply` failed; frontend import of the new card helpers failed.
- Final backend full suite: `788 passed, 7 skipped` on the disposable test database. The subsequent English regression parameterization passed in the focused suite (`10 passed`).
- Frontend `npm run test:i18n`: `71 passed`; `npm run build`: success (Vite reports the existing large chunk advisory).
- `git diff --check`: clean. No commit, push, deployment, or live provider/RAG call was made.

## Focused correction pass (2026-10-13)

- Completed turns retain stock focus for natural pronoun follow-ups such as “那它最新价格呢”; an independent request or mismatched pending reply still starts a new scope. A pending read after an old action remains independent.
- Pending stock, comparison, and action-source replies accept bounded standalone tickers through the existing ticker extraction and normalizer (including PLTR and BRK.B). Full read questions cannot fill those slots. The exact offered label “生成 AI 分析” now wins over the general new-request detector; “生成 AMD 财报分析” remains independent.
- Generic latest saved-library reads resolve the selected saved SEC filing first, then pass its verified source ID into `query_materials`. A missing, invalid, or mismatched source stops that material lookup; returned rows and citations are restricted to the selected source. This path describes the available saved SEC source, not a stock price or an uploaded-media lookup.
- Focused backend check after corrections: `40 passed` (`tests/test_agent_request_resolution.py` and `tests/test_agent_workflow_execution.py`). Full checks follow below.
- Final check after adding the selected saved SEC label: full backend `794 passed, 7 skipped`; frontend `npm run test:i18n` `71 passed`; `npm run build` succeeded with Vite's existing large-chunk advisory; `git diff --check` clean. All external retrieval in these regressions was mocked. No real provider, RAG service, production database, commit, push, or deployment was used.

## Second routing correction (2026-10-13)

- Independent review found that the generic saved-library shortcut overrode explicit saved AI summary and original-plus-analysis requests. InMemory regressions first failed with `source_qa` for both. The shortcut now applies only when no AI analysis is requested and the classifier did not select the existing compound route. The saved AI summary uses `query_material_analysis`; original plus AI analysis keeps the original and analysis tools.
- Independent review also found that a completed turn dropped both company and material focus on “这份资料说了什么”. An explicit document reference now retains the prior source ID for that same-source read. A new action, independent Apple price request, or different company clears the old material focus.
- All added tests use an in-memory checkpointer and mocked tools. Final check results are recorded in the task report.

## Primary acceptance before publication

- Independently inspected the final diff and reran the three affected backend suites: 55 passed. The worker's final full disposable suite produced 797 passed, 7 skipped. Frontend helpers independently passed 71 tests; the production build and diff check passed. The prior compact citation rendering remains unchanged.
- Browser acceptance on an isolated SSE fixture showed selectable operation buttons with a bottom freeform field. Button and manual entry both continued; replaced cards were disabled. This is UI evidence, not a real-model claim.
- Real DeepSeek prepublication shadow testing confirmed operation selection retained the request ID, then the exact Apple question exited the pending operation and used query_sec_filings/query_materials. Arbitrary web-news upload returned an explicit unsupported response without a loop. Shadow RAG was disabled to avoid a duplicate local model load; public RAG verification follows publication.
- Read-only service/data checks found the running RAG healthy, but the latest saved AAPL source has pending chunks in the active generation. This data limit must be reported independently of the routing fix. No reindexing, new analysis, source upload or business task was silently initiated.
- The main checkout's 56 pre-existing learner files still matched their SHA256 snapshot; changed task paths do not overlap them.
