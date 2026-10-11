# Model profile storage implementation

## Approved brief and plan

Human-approved two-mode design: local downloaded runs may persist profiles in a server-side JSON file behind loopback-only guarded API; the public website keeps browser storage and gains explicit private JSON backup/restore containing API keys. Never rewrite `.env`, expose local shared profiles publicly, include keys in chat archives/logs, or use real keys in tests. Preserve existing dirty work. Plan: backend persistence/API TDD; frontend storage coordinator and backup tests; UI/i18n; targeted checks and report.

## TDD red

Pending backend tests and initial red run.

## Implementation

Added `app/local_model_profiles.py` with dynamic enable/public-mode guards, loopback/authority/origin/proxy checks before file access, 128 KiB streaming limit, schema validation, safe no-store errors, process-local serialized atomic writes, and router registration. The configured path is server-only. The frontend now probes only loopback page/API combinations, migrates existing browser data only to an empty local file, uses the selected backend for mutations, and provides validated 128 KiB export/import with collision remapping and imported verification cleared. Settings are gated while loading/testing/sending. Added docs and English/Chinese strings.

## Checks / limitations

Initial backend TDD red: module missing as expected. After implementation, pytest setup attempts the repository's autouse disposable PostgreSQL fixture; this sandbox denied loopback DB connections (`Operation not permitted`), so the targeted backend test run could not reach test execution. Re-run recorded below after finishing code checks. No real API keys or provider calls used.

## Final verification (2026-10-11)

- TDD red was observed before module creation (`ModuleNotFoundError` for `app.local_model_profiles`). The exact required pytest invocation could not initialize the repository's autouse disposable PostgreSQL database because this execution environment rejects loopback DB connections (`Operation not permitted`); it reported 27 setup errors across the new route and existing agent-chat API/stream tests. No product test body was reached in that invocation.
- To exercise the DB-independent route tests without changing production guards or repository fixtures, copied the new test module to `/tmp` and ran it with the repository on `PYTHONPATH`: `PYTHONPATH="$PWD" .venv/bin/python -m pytest /tmp/test_local_model_profiles.py -q` → **5 passed**. `py_compile` for `app/local_model_profiles.py` and `app/main.py` passed.
- `cd frontend && npm run test:i18n` → **35 passed, 0 failed** (includes existing model-profile, URL, lifecycle, archive, watchlist, and locale coverage plus new backup/local-probe cases).
- `cd frontend && npm run build` → TypeScript and Vite production build passed.
- No real provider calls, production restart, deploy, or live browser/API acceptance were performed. The parent remains responsible for live acceptance. Existing worktree modifications outside this task remain untouched.

## 2026-10-11 independent acceptance correction and recovery

Independent acceptance found mode-specific copy misleading in local-file mode, a lost-update risk if import captured profiles before queued work, backup rejection of empty-key legacy profiles, bracketed IPv6 detection, and startup instructions that did not work directly with Vite CORS. The correction now restores locale files from the exact pre-correction snapshots under `/tmp/market-model-files-20261011/before/` and merges this correction’s copy additions. Baseline English translations and all Chinese phrases are retained. Import parse/read-current/merge/save/state-apply execute inside the serialized mutation coordinator; chat sending and controls are blocked during load/mutation, and selection applies only after successful persistence. Import clears imported verification only; existing profile verification remains. Local file and browser mode now have separate explanations and composer text. GET exposes its configured display path through CORS. Backup import permits structurally valid unverified profiles with empty keys. IPv6 loopback and bounded local reads are covered by focused tests. Startup docs include the exact local backend and Vite commands.

Verification for this correction: `npm run test:i18n` passes 43 tests; `npm run build` passes; the uniquely copied focused backend file under `/tmp/test_local_model_profiles-correction-20261011.py` passes 10 tests; locale snapshot comparison confirms every baseline Chinese phrase and English translation survives; `git diff --check` passes. Synthetic fixtures only; no provider or real key was used.

Final focused correction checks: frontend i18n/model/chat suite 43 passed; frontend TypeScript/Vite production build passed; copied backend profile suite 10 passed; `git diff --check` passed; locale baseline comparison passed. The pre-correction combined backend suite was 27 passed; request a fresh combined run after this correction's schema bounds.

## Primary independent acceptance — partial

Primary reran the combined backend checks in the unrestricted host: 32 passed, and freshly reran frontend 43 tests plus production build successfully. Real IAB acceptance used synthetic-only keys and the actual local profile router; hello was an explicitly mocked fixture. Import/save/export file equality, service-process restart without file changes, browser-mode refresh, corrupt-file rejection, and composer selection persisted correctly. All 1,010 pre-turn Chinese phrases and English translations survive unchanged. Production services, real keys and `.env` remain untouched; no push/deploy.

Two independent failures remain after the one focused correction: parsing an empty-key legacy backup succeeds but merging still enforces requireKeys=true and rejects it; Chinese settings mode descriptions render only semantic label strings rather than full explanations. The worker's broader backup/copy completion claims are superseded by `docs/model-profile-storage-acceptance.md`; the round is PARTIAL, not all acceptance passed.
