# Provider discovery and local chat configuration release

Release scope: provider model discovery, loopback-only local model profile persistence, frontend profile backup/restore, and browser-local chat archive persistence. The API restart and GitHub Pages publication are tracked in this note after they occur.

Included: `app/agent_chat_api.py`, `app/agent_chat_middleware.py`, `app/local_model_profiles.py`, the selected `app/main.py` router/middleware/validation/CORS wiring, frontend chat/profile modules and UI, feature tests, and the discovery/local profile/chat persistence documentation.

Explicitly excluded: dense RAG implementation, fixtures, dependencies, scripts, deployment configuration, RAG startup/health/ownership changes, tutorial-only source comments, and unrelated existing worktree edits. No migration or database mutation is part of this release. The local profile API remains disabled by default and returns 404 in public mode.

Pre-release validation: staged backend feature/security subset passed 98 tests; frontend production build passed and `npm run test:i18n` passed 48 tests. The complete backend suite reported 551 passed and 14 failures in unrelated existing market-data assumptions/data collisions plus legacy access tests before the middleware runtime-config compatibility fix; the focused rerun after that fix passed 98 tests. The release tree was built from the index and contains no RAG router/runtime wiring. Secret-pattern scan found four matches, all in synthetic test fixtures; staged filenames contained no sensitive names. Deployment, API restart, and live checks are pending.

CI run 38118103281 (commit e505860) failed in backend suite on the launcher's `--print` output assertion and `test_public_mode_allows_reads_but_protects_owner_writes`; the latter passes in the isolated local staged tree with the workflow's CORS configuration. Launcher output is being adjusted to retain its established command summary while also reporting the clean runtime source and env-file paths. Follow-up CI is pending.
