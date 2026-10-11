# Market Evidence Agent chat UX revision

Focused follow-up completed in source: `AgentChatPanel` notifies `AppEntry` after personal login and guest switching, causing the keyed provider to remount and cancel the prior identity's work. Delayed archive persistence now runs behind the lifecycle token and is covered by a controller-helper interleaving test. Entry copy states that chat messages are stored in server PostgreSQL while model settings and identity remain in the browser.

## Design and implementation

- Replaced the persistent rail with a compact trigger and temporary/pinned deep-green drawer; the drawer is shared by every route. It closes on Escape, outside pointer interaction, route selection, or pointer exit while unpinned.
- Added the one-time visitor/personal entry choice, persistent guest identity with migration from the former session identity, and a distinct stable personal chat credential. In-chat mode changes notify the app entry boundary and remount the keyed provider without a page reload. The first personal switch preserves any legacy guest credential, and mode changes clear the selected conversation plus the session owner operation key. Personal login uses a server-side PBKDF2 verifier from `PERSONAL_CHAT_LOGIN_PATH`; the code is never shipped to the browser. Owner write authorization continues to use its original operation key.
- Added bounded owner-scoped conversation listing, first-question titles, deterministic updated-time/id ordering, and offset pagination. Existing messages restore endpoint remains the history reader. Existing stream implementation was left intact.
- Reworked chat into a history rail plus answer area; archive import/export/connect controls live under settings. Added question jump links, tool status badges without raw tool data, clipboard feedback, scroll-follow only while at the bottom, and GFM Markdown rendering with safe default link handling.
- `react-markdown` and `remark-gfm` are used with a table overflow wrapper. Raw HTML is not enabled.

## Private login setup

The launcher defaults `PERSONAL_CHAT_LOGIN_PATH` to `data/personal-chat-login.json`. Configure it locally by running `./.venv/bin/python scripts/setup_personal_chat_login.py`; the script prompts with `getpass` on a terminal and accepts stdin when piped. It writes `salt`, PBKDF2 `hash`, `iterations`, and a random stable `chat_credential` atomically with mode `0600`. No private login value is stored in this repository note. The runtime config file is not included in the release source.

## Local visual fixture

Start the fixture only on loopback with `./.venv/bin/uvicorn scripts.chat_ux_fixture:app --host 127.0.0.1 --port 8028`, then start Vite with `VITE_API_BASE_URL=http://127.0.0.1:8028 npm run dev -- --host 127.0.0.1 --port 5179`. Open `http://127.0.0.1:5179/#agent`; CORS is explicitly limited to localhost/127.0.0.1:5179. The fake login returns only a fixture identity, guest and personal history differ, and sample AAPL-like tables are fictional. A message containing `[slow]` streams delayed deltas for Stop checks. No provider/model is called. Do not connect this fixture to a production deployment.

## Independent acceptance correction (2026-10-11)

- Moved the chat provider below the selected entry mode; a keyed provider remount separates guest and personal restores. Guest credentials migrate from the legacy session key into persistent local storage before a personal switch; active conversation IDs and local archive IDs are mode scoped. Switching modes clears the session owner operation key.
- History rows now call the provider's abortable restore controller. It commits owned conversation state only after a successful, current response; stale requests cannot overwrite a newer selection or a new chat. The restore token is established before any awaited archive persistence, then checked again before the restore request. Selected server IDs and original creation times are used by archive autosave, including initial cloud restoration. Completed turns refresh history.
- Moved New chat to history only, added a top-of-chat mobile history panel toggle, removed the user role label and duplicate agent breadcrumb, and retained a compact readonly archive return action in chat and settings. Saved model dropdowns show display names only.
- Expanded the answer region and Markdown typography, with a bold-content override, readable tables, and table-only horizontal scrolling. Clipboard operations now time out with visible status and leave the answer selected for manual copying on failure; a failure is never reported as success.
- Setup refuses any pre-existing private login config without opening or replacing it; first setup is distinct from an operator-managed rotation. Fixture credentials and data remain fictional and fixture-only.
- Context7 remained unavailable; the existing `react-markdown`/`remark-gfm` usage follows the already reviewed official integration references.

## Validation

- `frontend/npm run test:i18n`: 58 tests passed, including direct legacy guest credential migration on personal switch, session owner-key clearing, delayed archive-write/new-chat interleaving, and React SSR Markdown table, emphasis, list, safe-link and raw-HTML checks.
- `frontend/npm run build`: passed; Vite reports one large-bundle advisory (Markdown dependencies increase the app bundle).
- `npm audit` reports four high-severity findings in the pre-existing build toolchain (`@vitejs/plugin-react`, `postcss`, `source-map-js`, `vite`); their locked versions match HEAD and npm reports no automatic fix. The newly added Markdown packages are not among the reported packages. Audit JSON: `/tmp/market-chat-ux-20261011/npm-audit.json`.
- `./.venv/bin/pytest -q tests/test_agent_chat_identity.py`: personal login, missing/unavailable config, rate limiting, config non-disclosure, list auth/bounds, restore cross-owner rejection and scoped SQL query checks.
- Setup script smoke check used only a synthetic fixture code in an isolated `/tmp` directory: expected JSON fields present, file mode `0600`, and input absent from captured output.
- Clean staged-source release assembled at `/tmp/market-chat-ux-20261011/clean-release-final` (HEAD plus only staged UX files and synthesized clean `app/main.py`): full backend suite **570 passed** with `CORS_ALLOWED_ORIGINS=https://release-fixture.invalid`; log: `/tmp/market-chat-ux-20261011/backend-tests.log`. This excludes the unrelated working-tree RAG changes.
- Context7 was unavailable in the planned investigation. The documented `react-markdown`/`remark-gfm` integration is used; no Context7 lookup is claimed.
- Browser/device acceptance is still for the primary agent to perform. No API server or launch agent was restarted and no deployment was attempted.
- Exact browser acceptance for the remaining CUA pass: in the fixture, choose guest mode and send `Show the AAPL demo table`; open in-chat mode switching, log in with fixture-only `demo-only`, and confirm the guest AAPL messages disappear while personal history appears. Switch back to guest and confirm guest history returns without personal messages. Repeat the reverse transition if needed; expect no page reload. This browser interaction is distinct from the source tests and build reported for this repair.

## Primary acceptance and publication preparation

- Independent frontend tests: 58 passed; build passed. Independent clean-candidate backend suite: 570 passed. Backend files did not change during the final frontend correction.
- CUA on loopback fixture validated actual in-chat guest/personal switching, separate restores, reload persistence, new-chat cancellation during slow SSE, copy feedback and clipboard text, question navigation, menu click/Escape, settings-only archive controls, and safe rendered two-table Markdown. Desktop viewport1440 had no document overflow; mobile390 had303px table containers with520px internal scroll. Hover dismissal was checked in code rather than a physical hover interaction. Fixture prices are fictional; no live model call was used for this UX acceptance.
- Private login configuration is excluded from source and will be first-created locally by the operator. Existing owner operation authorization remains separate. Unrelated working-tree RAG changes are excluded from this release.

## Four approved chat UX refinements (2026-10-11)

- Fixed the shared API error mapper: it had converted every HTTP 401, including model/chat credential failures, into “此操作仅站点所有者可执行。” Only `requestJson` mutations now identify an owner-write 401. The message distinguishes an absent session operation key from a stored key rejected by the server and explicitly separates personal chat identity from write authorization. Other 401s retain a credential-neutral authentication error.
- Owner-write 401s open an inline modal with a session-only operation-key field. Saving only stores the value and closes the modal; it does not retry the failed write or claim server verification. The message directs the user to manually retry. Settings and personal mode show “尚未设置操作口令” / “操作口令已填写”; the latter describes local state only. Guest-mode switching continues to clear the operation key.
- Desktop navigation can be pinned as a 240px grid column; it remains pinned across route remounts and outside clicks. Escape or the menu control unpins it. At narrow widths the control stays a temporary overlay drawer. This applies to the shared shell and therefore all workspace routes.
- Transcript messages and composer are centered within an 880px maximum column. User messages use fit-content sizing with a desktop 50% cap and an 80% mobile cap; existing GFM table wrappers retain internal horizontal scrolling.
- Replaced numbered question controls with accessible tick buttons and fixed preview cards. Turn previews pair each user prompt with its next assistant answer, remove Markdown/markup, truncate text, and replace raw JSON-shaped previews with a neutral label. Active ticks derive from transcript scroll position. Pure helper regressions cover turn pairing, preview cleanup, and owner/non-owner 401 wording.
- Validation on 2026-10-11: `npm run test:i18n` passed 62/62; `npm run build` passed. Build emits the existing Vite warning that the minified JavaScript chunk exceeds 500 kB. Interactive dock geometry, mobile behavior, pointer preview feel, and protected-action UI still need the primary agent's browser/CUA acceptance; no private service was started and no backend/business write was issued.

## Primary CUA acceptance repair (2026-10-11)

- Primary CUA reported four concrete failures: desktop user bubbles were left aligned by grid `align-self`; the composer center was offset 20px from the answer column; the pinned close trigger was hidden below the rail; and the owner dialog did not receive focus or isolate keyboard/background interaction. CUA also found an unanswered question could borrow a later question's answer in its preview.
- Repair: user bubbles use grid horizontal centering while preserving fit-content, 50% desktop, and 80% mobile caps. Composer width and left offset are calculated from the available column width and the half-rail/gap offset, keeping narrow layouts within the parent. The pinned rail region and trigger have explicit stacked layers on desktop and mobile. The owner dialog autofocuses its password input, traps Tab, makes the rest of the app inert, restores the invoking focus target when still connected, and captures Escape locally so it closes only the owner dialog. Session-only save copy and manual retry behavior are unchanged. Preview answer lookup stops before the next user message.
- Regression added for `[user A, user B, assistant B]`: A has an empty answer and B previews answer B. Existing plain preview sanitization remains unchanged.
- Self-check on the repaired source: `npm run test:i18n` **63 passed, 0 failed**; `npm run build` passed with the existing Vite >500 kB chunk advisory. TypeScript compilation is included in build. No backend files changed, so backend tests were not repeated. The primary CUA's earlier passing checks (240px dock/content geometry, real backend 401 handling, tick navigation and keyboard preview) are inherited findings; final visual confirmation of these repairs remains for the primary agent. No server was restarted, no private config was read, and no write was retried automatically.

## Mobile navigation follow-up (2026-10-11)

- Narrow viewports (760px and below) now ignore hover/pointer movement for drawer state. The menu button toggles a persistent mobile drawer; outside pointer, Escape, and route selection close it. Its accessible label reflects open/closed state.
- A saved desktop pin is treated as inactive at narrow widths while retained for a later desktop resize. This prevents the desktop-pinned layout from forcing the mobile drawer open and lets mobile route selection close it. Desktop pin, hover, route, and outside-click behavior remain unchanged.
- Validation requested for this focused correction: frontend tests and production build. The primary agent will perform the final mobile CUA checks, including existing desktop-pin preference on resize.

## Primary final acceptance of refinements

- Independent frontend test run:63 passed; production build passed. No backend changes were staged; previous570-test backend baseline remains unchanged and is not claimed as a fresh local backend rerun.
- CUA validated desktop dock240px and main offset240px, visible ordinary-click close button, pin persistence outside click, and common user/answer/composer center970.5px at1440px viewport. User bubble222.59px within779px answer column; capped50percent.
- CUA validated tick navigation without visible numbering, plain question/answer preview on keyboard focus, smoothjump alignment, and scroll-based active tick. On390px viewport documentwidth390; table width303px with520px internal scroll; mobileclick open/close, itemselectionclose, and olddesktoppin resizing passed. Temporary viewport override reset after acceptance.
- Actual local API through Viteproxy returned401 without an owner key and with an intentionally invalid fixture key: distinct recoverydialogs appeared, input autofocus, Tab containment and Escapeclose passed. The fixture key was cleared afterward. Both writes were rejected; no SECscan, analysis, realprovidercall, or database mutation occurred during these auth checks. Personal chat login and operation authorization remain separate.
- Scopecheck preserved all60 unrelated existing trackeddiffs. This frontend release excludes uncommitted RAG/backend work.

## User bubble alignment clarification (2026-10-11)

- Direct clarification: “居中是用户的提问右对齐，然后 X轴长度不超过一半”. The assistant reading column and conversation remain centered; user questions align to the right within that column, remain fit-content, and are capped at 50% at every viewport width. Removed the mobile 80%/92% exceptions.
- Updated `.agent-message.is-user` with right-side grid placement and right anchoring. Existing assistant and composer layout rules are unchanged.
- Primary acceptance after clarification: all 63 frontend tests and production build passed. At1440px, user.right=thread.right=1360px and bubblewidth222.59px within779px column; at390px, each bubblewidth164.5px within329px column, rightaligned374px, documentwidth390px. Saved current simulated-data screenshot to data/chat-ux-refined.jpg.
