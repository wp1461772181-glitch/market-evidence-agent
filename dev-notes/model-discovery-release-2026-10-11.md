# Provider discovery and local chat configuration release

Release scope: provider model discovery, loopback-only local model profile persistence, frontend profile backup/restore, and browser-local chat archive persistence. The API restart and GitHub Pages publication are tracked in this note after they occur.

Included: `app/agent_chat_api.py`, `app/agent_chat_middleware.py`, `app/local_model_profiles.py`, the selected `app/main.py` router/middleware/validation/CORS wiring, frontend chat/profile modules and UI, feature tests, and the discovery/local profile/chat persistence documentation.

Explicitly excluded: dense RAG implementation, fixtures, dependencies, scripts, deployment configuration, RAG startup/health/ownership changes, tutorial-only source comments, and unrelated existing worktree edits. No migration or database mutation is part of this release. The local profile API remains disabled by default and returns 404 in public mode.

Pre-release validation: staged backend feature/security subset passed 98 tests; frontend production build passed and `npm run test:i18n` passed 48 tests. The complete backend suite reported 551 passed and 14 failures in unrelated existing market-data assumptions/data collisions plus legacy access tests before the middleware runtime-config compatibility fix; the focused rerun after that fix passed 98 tests. The release tree was built from the index and contains no RAG router/runtime wiring. Secret-pattern scan found four matches, all in synthetic test fixtures; staged filenames contained no sensitive names. Deployment, API restart, and live checks are pending.

CI run 38118103281 (commit e505860) failed only on the launcher's `--print` output assertion (1 failed, 564 passed); the public-access test mentioned in the warning section was not a failure. Launcher output was adjusted to retain its established command summary while also reporting the clean runtime source and env-file paths.

Release result: `da6626d487c67bd3fc2d59fd3dde0c71fe08cd7f` passed CI run 38118238026 and Pages deployment run 38118332643. The deployed Pages document and JavaScript both returned HTTP 200; bundle checks found the model discovery UI, profile backup actions, local profile API integration, and chat archive UI. A clean immutable source tree was materialized at `data/releases/da6626d487c67bd3fc2d59fd3dde0c71fe08cd7f`, with `data/releases/current` pointing to it. LaunchAgent restart was attempted using the authorized `launchctl kickstart -k gui/$(id -u)/com.market-evidence-agent.api` command but the execution sandbox returned `Operation not permitted`. Before restart, local and Funnel `/health` returned 200, while Funnel `POST /agent-chat/models` returned 401, confirming the old process is still serving. Host restart and post-restart model discovery / public profile-route checks remain pending.

## Primary final live acceptance — completed

The primary orchestrator executed the already-authorized host LaunchAgent restart successfully after the worker's sandbox-only restriction. New API PID 48577 listens exclusively on 127.0.0.1:8000 and its command uses `data/releases/current`; the resolved runtime is the exact deployed `da6626d` source. All 68 released backend/launcher files match the commit, no RAG package or copied `.env` is present, and the original 58 modified backend files retained identical working bytes.

Independent public HTTP checks: health 200; OpenRouter no-key model discovery 200 with five conversational candidates, timestamp ordering, no-store and exact Pages CORS; `/agent-chat/local-model-profiles` 404; `/health/knowledge` 404; protected owner write without key 401; existing `/v2/stocks/AAPL/workspace` 200. No paid model calls or real provider keys were used.

Independent browser acceptance on the published Pages URL confirmed the new settings UI, five real OpenRouter candidates, and selection filling `stepfun/step-5-preview` in Model ID. No profile was saved during this public check. Screenshot: `/tmp/market-release-20261011/online-model-discovery.png`. Independent clean-source backend suite: 565 passed; feature/security regression: 103 passed; final launcher tests: 2 passed. Frontend checks: 48 passed and production build passed. Both CI and Pages runs are confirmed successful at `da6626d`.

Final status supersedes the worker's pending-restart report above: GitHub synchronized, Pages and API published, live browser/API verified. Later documentation-only commits use `[skip ci]`; the deployed product source remains `da6626d`.
