# Personal Agent Phase 1 — Mac sync and deploy notes

Phase 1 was built and tested on CodeBot's Linux box in worktree branch `worktree-personal-agent`.
Nothing was deployed and nothing was pushed. The Mac steps below are still to do.

## Commits (branch `worktree-personal-agent`)

| Commit | Subject |
|---|---|
| `bfcb274` | docs: personal agent plan and staged execution plan |
| `86440b3` | feat: owner profile with remember/forget tools and quoted auto-extraction |
| `2b90fbc` | feat(frontend): personal agent shell, cool-gray/signal-blue theme, mobile tabs |
| `5859fea` | chore: in-process live check for the owner profile (real model, disposable DB) |
| (next) | docs: Phase 1 deploy notes (this file) |

The branch forked from main at `f82c220`. Main has two commits the branch does not: `f299d8f` (the same
tree as the branch's `470477c`) and `7aa0f1d` (claim evidence binding). `git merge-tree` shows a clean merge.
The suggested way to land it is to cherry-pick the Phase 1 commits onto main, which keeps main linear:

```bash
git checkout main            # must be clean
git cherry-pick bfcb274^..worktree-personal-agent
```

## Before deploying (on the Mac)

```bash
.venv/bin/python -m pytest -q tests/test_agent_profile.py tests/test_agent_tool_catalog.py \
  tests/test_agent_tool_labels.py tests/test_claim_*.py tests/test_jev_claim_support.py
cd frontend && npm ci && npm run test:i18n && npm run build && cd ..
```

Claim gates: the code does not read or change `CLAIM_QUOTE_GATE` / `CLAIM_JEV_SUPPORT`. Leave `.env` as it is.

## Live check (real model, Mac only)

This could not run on CodeBot. The provider transport's SSRF guard (`app/agent_chat_api.py`, `unsafe_endpoint`)
refuses `api.deepseek.com` there, because the sandbox's DNS resolves it to a non-public tunnel address.
The guard is working as designed and was left alone. On the Mac:

```bash
.venv/bin/python scripts/live_check_profile.py
```

It uses `DEEPSEEK_API_KEY` (and optionally `DEEPSEEK_MODEL`) from `.env`, creates and drops its own
`test_live_profile_*` database, and prints card titles, profile texts and one answer, never the key.
Expect `RESULT: PASS`:
1. "以后金额都用亿美元表示" gives a 「记住这条偏好」 card. After the press, one explicit item exists.
2. The extractor gives inferred items whose quotes are substrings of the message.
3. A new conversation's first answer uses 亿美元.

Manual check in the UI after deploy: say 「以后金额用亿美元」, press 同意, open a new conversation,
ask a revenue question, and expect 亿美元. Then say 「忘掉亿美元那条」, press 同意, and the next answer
should no longer follow it.

## Deploy

```bash
python scripts/deploy.py --backend-only        # needs a clean main commit
curl -s http://127.0.0.1:8000/health
```

- **Schema:** the new table `agent_profile_items` is created by `Base.metadata.create_all` at API startup, so no
  migration script is needed. It is additive and touches no existing table.
- **New endpoints** (owner only): `GET /agent-chat/profile`, `POST /agent-chat/profile/{id}/confirm`,
  `DELETE /agent-chat/profile/{id}`.
- **Frontend:** `--backend-only` skips the Pages push. The new shell ships when the owner approves a push
  (pushing to GitHub needs the owner's go-ahead, per the plan).
- **Rollback:** `deploy.py` rolls back by itself if `/health` fails. To roll back by hand, redeploy the
  previous main commit. The extra table is harmless to older code.

## Known pre-existing test failures on CodeBot (not caused by Phase 1)

These fail the same way on the untouched baseline (`470477c`):
- `tests/test_agent_citation_restore_v2.py::test_price_citation_round_trips_and_tamper_is_rejected`
- `tests/test_agent_citation_restore_v2.py::test_more_than_ten_source_identities_survive_history_restore`
- `tests/test_agent_web_ingestion.py::test_web_snapshot_deduplicates_url_and_body_and_keeps_type`
- `tests/test_agent_web_ingestion.py::test_saved_web_survives_sync_index_retrieval_and_owner_citation_restore`

Verified on CodeBot: the Phase 1 commits cherry-picked onto main `7aa0f1d` (temporary worktree) give
1036 passed and the same 4 failures across `tests/test_agent_*.py tests/test_claim_*.py tests/test_jev_claim_support.py tests/test_reviewed_claims.py`.

Check whether they also fail on the Mac before you read them as regressions.

## Phase 3 — tasks, inbox, background model, alerts (Mac steps)

Commits: `5db3da4` (backend, tests, runner, launchd installer) and `4875544` (frontend), plus the notes commit.
Land the branch on a clean main as before (`git cherry-pick bfcb274^..worktree-personal-agent` lands every phase).

1. **Tests on the Mac**
   ```bash
   .venv/bin/python -m pytest -q tests/test_agent_tasks.py tests/test_agent_tool_catalog.py tests/test_agent_*.py \
     tests/test_claim_*.py tests/test_jev_claim_support.py tests/test_reviewed_claims.py tests/test_official_monitor_v2.py
   cd frontend && npm run test:i18n && npm run build && cd ..
   ```
   The same 4 pre-existing failures are expected (see the list above). Nothing else should fail.
2. **Live check (real model)**: `.venv/bin/python scripts/live_check_agent_tasks.py`. It uses
   `DEEPSEEK_API_KEY` from `.env` and a disposable `test_live_tasks_*` database, and never prints the key. Expect `RESULT: PASS`:
   the unattended run creates no consent card and leaves the research queue unchanged, a brief lands in the inbox, and after revoking the key
   the task shows 未运行：缺少模型.
3. **Deploy backend**: `python scripts/deploy.py --backend-only`, then `curl -s http://127.0.0.1:8000/health`.
   Startup creates `agent_tasks`, `agent_inbox_items` and `agent_background_model` (additive, `create_all`).
   `MODEL_CREDENTIAL_KEY` (or `APP_API_ACCESS_KEY`, already set) encrypts the background key. Do not change `.env`.
4. **Install the runner** (after the deploy):
   ```bash
   .venv/bin/python scripts/install_agent_tasks_launchagent.py --print   # review
   .venv/bin/python scripts/install_agent_tasks_launchagent.py           # com.market-evidence-agent.agent-tasks, every 300 s
   .venv/bin/python scripts/run_agent_tasks.py --dry-run                 # lists due tasks, runs nothing
   tail -f logs/agent-tasks.out.log
   ```
   The runner acts as the owner credential in `data/personal-chat-login.json`, and only one runner works at a time.
   For a SEC-monitor alert hook right after each scan, the monitor must run in `v2` mode; otherwise the alerts task's own schedule picks up new filings.
   The market refresh calls the price-line check whenever it stores new bars.
5. **Manual check after deploy**: the frontend needs a Pages push, so do this on a local `vite` build, or after the owner approves the push.
   - In 设置 → 后台任务模型, choose a tested model profile, then 用这个模型. The page shows only 「已保存 key」.
   - In 简报 → 任务 → 新建任务, create 工作日 08:00 研究队列简报. For a quick test, set the time a few minutes ahead and wait for the next tick.
   - The brief appears in 简报 with [n] sources, and 打开对话 continues it as a chat.
   - If the instruction asks for a write (e.g. 加入研究队列), the suggestion appears in 待你确认 and nothing changes until you press
     生成确认卡 → 同意 in the opened conversation. The card's capability lasts 10 minutes from 生成确认卡.
   - 撤销 key → the next run shows 未运行：缺少模型.
6. **Frontend push** (briefs page, settings card) needs the owner's OK: `deploy.py` without `--backend-only`.

Not in Phase 3: the Telegram channel is stored, but delivery comes in Phase 4. Such briefs stay in the inbox and say so.
Phase 5 (Skills) fills `skill_id`.

## Phase 4 — Telegram

Commits: (next) (backend, bridge, launchd installer, tests, frontend settings card).
Land the branch on a clean main as before (`git cherry-pick bfcb274^..worktree-personal-agent` lands every phase).

1. **Tests on the Mac**
   ```bash
   .venv/bin/python -m pytest -q tests/test_telegram_bridge.py
   ```
   Expect 22 passed.

2. **Get a bot from @BotFather** on Telegram: `/newbot` → name it → copy the token (looks like `1234567890:AA...`).

3. **Deploy backend**: `python scripts/deploy.py --backend-only`, then `curl -s http://127.0.0.1:8000/health`.
   Startup creates `agent_telegram_bind` (additive, `create_all`).

4. **Configure in the UI** (needs frontend push, or a local vite build):
   - 设置 → Telegram 随身入口 → paste the Bot Token → 保存 Token.
   - 生成绑定码 → copy the code → on Telegram, send `/bind <code>` to your bot.
   - The card shows "已绑定 @yourbot".

5. **First run**:
   ```bash
   .venv/bin/python scripts/run_telegram_bridge.py --dry-run   # validates config
   .venv/bin/python scripts/run_telegram_bridge.py              # long-poll forever
   ```
   The bridge needs the background model configured in 设置 → 后台任务模型 (same as Phase 3).
   It calls the local agent-chat stream API at `http://127.0.0.1:8000`; the API server must be running.
   On first message from the bound chat, a 「手机」 conversation is created.

6. **Verify**:
   - Send a question on Telegram. The answer arrives with citation markers.
   - Press a confirmation button (when the agent proposes a write). The second press replays, doesn't re-execute.
   - Create a task with channel=Telegram; the brief arrives in the Telegram chat.
   - Send a message from a different Telegram account: no response.

7. **Install the bridge as a LaunchAgent** (KeepAlive, runs forever):
   ```bash
   .venv/bin/python scripts/install_telegram_bridge_launchagent.py --print   # review
   .venv/bin/python scripts/install_telegram_bridge_launchagent.py           # com.market-evidence-agent.telegram-bridge
   tail -f logs/telegram-bridge.out.log
   ```

8. **Live check** (no real Telegram API calls, tests bind flow and consent in-process):
   ```bash
   .venv/bin/python scripts/live_check_telegram.py
   ```
   Expect `RESULT: PASS`. Never prints tokens.

9. **Frontend push** (Telegram settings card) needs the owner's OK: `deploy.py` without `--backend-only`.

### Endpoints (owner-only)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/agent-chat/telegram/status` | Has token, is bound, bot username, chat id (never the token) |
| POST | `/agent-chat/telegram/token` | Save/update bot token (body: `{"bot_token":"..."}`) |
| POST | `/agent-chat/telegram/bind` | Generate one-time bind code (returns `{"code":"abcdef01"}`) |
| POST | `/agent-chat/telegram/unbind` | Clear chat/user binding (token stays) |

### New files

| File | Purpose |
|------|---------|
| `app/agent_telegram.py` | Bind store (encrypt token, generate bind code, confirm/unbind) |
| `app/agent_chat_models.py` | `AgentTelegramBind` model |
| `app/agent_chat_stream.py` | 4 new owner-only endpoints |
| `scripts/run_telegram_bridge.py` | Long-poll getUpdates, forward to agent, handle inline buttons |
| `scripts/install_telegram_bridge_launchagent.py` | LaunchAgent installer |
| `scripts/live_check_telegram.py` | In-process live check (no real Telegram API) |
| `tests/test_telegram_bridge.py` | 22 tests |
| `frontend/src/AgentChatPanel.tsx` | `TelegramBindCard` in 设置 page |
| `frontend/src/api.ts` | `TelegramBindStatus` type, 4 API functions, 6 error labels |
| `frontend/src/PersonalPages.tsx` | Updated channel option label and pending-delivery note |
