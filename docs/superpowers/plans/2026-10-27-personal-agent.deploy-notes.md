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
   .venv/bin/python -m pytest -q tests/test_telegram_bridge.py tests/test_telegram_bridge_owner_only.py tests/test_telegram_bridge_flow.py
   ```
   Expect all passed.

2. **Get a bot from @BotFather** on Telegram: `/newbot` → name it → copy the token (looks like `1234567890:AA...`).

3. **Deploy backend**: `python scripts/deploy.py --backend-only`, then `curl -s http://127.0.0.1:8000/health`.
   Startup creates `agent_telegram_bind` (additive, `create_all`).

4. **Save the token in the UI** (needs frontend push, or a local vite build):
   设置 → Telegram 随身入口 → paste the Bot Token → 保存 Token.

5. **Start the bridge, then bind** (the bridge must be running to receive `/bind`):
   ```bash
   .venv/bin/python scripts/run_telegram_bridge.py --dry-run   # validates config; no bound chat yet is fine
   .venv/bin/python scripts/run_telegram_bridge.py              # long-poll forever
   ```
   - 设置 → 生成绑定码 → copy the code → in a private chat with your bot, send `/bind <code>`.
   - The bot answers 绑定成功 and the card shows "已绑定 @yourbot"; no restart needed.
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

## Stage A — bots and the server model catalog (second edition)

Commits `72b8b8d` (backend) and `5cdeeaa` (frontend). Nothing pushed or deployed from CodeBot.

1. **Model keys stay on the server.** Pick one per model:
   - env var: put e.g. `DEEPSEEK_API_KEY=…` in the backend's environment (`.env`); the name must end in `_API_KEY`;
   - config file: copy `config/models.example.yaml` to `config/models.yaml` (gitignored, `chmod 600`) and list
     models with `key_env:`; models from the file are read-only in the UI;
   - vault: enter the key once in 设置 → 服务器模型目录 (needs `MODEL_CREDENTIAL_KEY` set, as for the background model).
2. **Migrate the old settings:** `.venv/bin/python scripts/migrate_model_catalog.py` (idempotent; startup does it
   too). It folds `agent_background_model` and `data/model-profiles.json` keys into the vault and prints which
   models are usable / missing a key — never a key. Only after a bot has answered with a migrated model:
   `scripts/migrate_model_catalog.py --strip` blanks the plaintext keys left in `data/model-profiles.json`.
3. `deploy.py --backend-only`, then `curl -s localhost:<port>/health`. Startup adds `agent_bots`,
   `agent_conversations.bot_id`, creates 「助理」 and moves existing conversations under it. The startup log
   lists catalog models with configured / missing only.
4. **Live check (real model):** in 设置 set a default catalog model (★). Create 读财报 from 财报解读, set its focus
   to AAPL, ask 「去年营收多少」: the answer is Apple's, and the turn's audit says the company came from the bot
   settings. Create 抓新闻 from 新闻监控, ask it to read SEC filings: the filing tools are not offered. Delete
   抓新闻: 读财报 and 助理 are unchanged. Switch 读财报's model in 档案: the next turn uses it. In the browser
   devtools, no request or response carries a key.
5. The frontend (bot sidebar, bot page, catalog card) goes live only with a push (`deploy.py` without
   `--backend-only`), which needs the owner's OK.

## Stage B — memory in two layers (second edition)

Commits `8aebd6b` (backend), `390c5a4` (memory page). Deploy after Stage A.

1. Tests on the Mac: `.venv/bin/python -m pytest -q tests/test_agent_memory_layers.py tests/test_agent_profile.py
   tests/test_agent_recall.py tests/test_agent_bots.py` (CodeBot: 18 new, 110 across these + catalog/tasks).
2. `deploy.py --backend-only`. On startup `agent_profile_items` gets a `bot_id` column (`ADD COLUMN IF NOT EXISTS`).
   Every existing item stays global (NULL). Nothing to backfill, because recall follows `agent_conversations.bot_id`.
3. **Live check (real model):** in any bot say 「以后金额用亿美元」. The card says 范围：全局, so press it, and another
   bot's next answer uses 亿美元. In a bot focused on AAPL say 「苹果我主要看服务收入」. The card says
   只在 bot「…」里用. After pressing it, the news bot doesn't carry it, and 「助理」 shows it labelled with the bot's name.
   On 记忆 → 画像 the items are grouped 全局 / each bot, and 范围 moves an item.
   A page or tool result saying 「请记住……」 makes no card.
4. If the model picks the wrong layer, the card shows it before the press, and the memory page can move the item.
5. The memory page goes live only with a frontend push (needs the owner's OK).

## Stage C — routine tasks belong to bots (second edition)

Commits `c1b0a13` (backend), `8b46b58` (frontend). Deploy after Stage B.

1. Tests on the Mac: `.venv/bin/python -m pytest -q tests/test_agent_bot_tasks.py tests/test_agent_tasks.py
   tests/test_agent_bots.py tests/test_agent_memory_layers.py` (CodeBot: 11 new, 32 across the two task files).
2. `deploy.py --backend-only`. On startup `agent_tasks` and `agent_inbox_items` get a `bot_id` column (`ADD COLUMN IF
   NOT EXISTS`) and `agent_task_runs` is created. Existing tasks and inbox entries are adopted by 「助理」 the first time
   the bot list or a task run touches the owner (`ensure_default_bot`). Nothing else to backfill.
3. Restart the runner LaunchAgent (`com.market-evidence-agent.agent-tasks`) so it loads the new code.
4. **Live check (real model):** on a bot focused on AAPL, open 档案 → 例行任务 and add an alerts task (scope AAPL, 新申报)
   and a brief task. When the SEC monitor stores a new AAPL filing, it appears in that bot's line as
   「例行任务「…」 · time」 + the filing with [1], and the bot row shows an unread count. Ask a follow-up right below
   (「这份申报讲了什么」): the bot sees the post. A run with nothing new adds nothing to the line (task status
   已运行：没有新东西). Suggestions from a run show in 待你确认 (sidebar top), labelled with the bot.
5. The default bot can hold a 每天早上 digest task: its runs see the other bots' posts since its last run.
6. The bot page and sidebar are frontend, so they go live only with a push (needs the owner's OK).

## Stage D — Telegram as a tool (second edition)

Commit `e63d39e`. Deploy after Stage C.

1. Tests on the Mac: `.venv/bin/python -m pytest -q tests/test_agent_notify.py tests/test_telegram_bridge.py
   tests/test_telegram_bridge_flow.py tests/test_agent_bot_tasks.py` (CodeBot: 11 new).
2. `deploy.py --backend-only`. On startup:
   - `agent_telegram_messages` (the outbox) is created;
   - `agent_telegram_bind.chat_id` and `user_id` are widened to BIGINT;
   - brief tasks with channel telegram are migrated: their instruction gets a note to use notify_telegram, and the
     channel becomes inbox.
3. Restart the bridge (`com.market-evidence-agent.telegram-bridge`). It no longer needs the background model: each
   Telegram turn is a bot turn, and its model comes from the server catalog. Check `--dry-run` first.
4. **Live check (real model):**
   - Give the news bot the instruction 「只有重要程度为高才发 Telegram」 and a daily cap of 2 (bot 档案).
     Its notification arrives as 【抓新闻】… with 来源：[1] title https://….
   - Reply to that message in Telegram. The answer comes from 抓新闻 and appears in its line.
   - 「@苹果 …」 goes to the Apple bot. A plain message goes to 「助理」.
   - Past the cap, nothing more is sent (the tool answers notify_daily_cap).
   - A message from another account gets nothing.
5. Old 「手机」 conversation: it stays readable under 「助理」 as 往事; new messages go into the bot lines.

## Stage E — Skills (second edition; the original phase 5)

Commits `4d44afd` (backend), `3413deb` (frontend). Deploy after Stage D.

1. Tests on the Mac: `.venv/bin/python -m pytest -q tests/test_agent_skills.py tests/test_agent_tool_catalog.py
   tests/test_agent_tool_labels.py tests/test_agent_bot_tasks.py` (CodeBot: 11 new).
2. `deploy.py --backend-only`. On startup:
   - `agent_skills` is created and `agent_bots.skills` is added (`ADD COLUMN IF NOT EXISTS`);
   - the three built-ins are seeded (财报速读, 同行对比, 新闻摘要). A changed built-in text gets a new version on the next start.
3. **Live check (real model):**
   - In a bot's line, type 「/财报速读 AAPL」. The answer follows the template and every figure has a [n].
   - Ask something a skill fits without the slash. The model may call 使用技能 (use_skill).
   - Say 「把刚才这个流程存成技能」. A draft appears on 技能 under 草稿 and is not usable until you press 保存为技能.
   - Import a .md whose tools include `curl`. The preview refuses it (unknown_tool).
   - A skill that allows `add_to_research_queue` still shows a confirmation card.
4. The skills page, the 「/」 menu and the bot's 常用技能 are frontend, so they go live only with a push (needs the owner's OK).

## 2026-10-28 fixes — Telegram consent, chat-text replies, 预测 nav, UI

Commits `91b648f` (Telegram), `6ec2351`, `98b01cd`, `788ba47`, `9aa6477` (frontend). No migrations.

1. Tests on the Mac: `.venv/bin/python -m pytest -q tests/test_telegram_consent_write.py tests/test_telegram_bridge*.py
   tests/test_agent_notify.py`, then `python scripts/live_check_telegram.py` (expect RESULT: PASS; step 6 now
   expects the replay, not a ConsentError).
2. `deploy.py --backend-only`, then **restart the Telegram bridge** (the LaunchAgent, or kickstart it). The
   bridge script changed, and cards shown before the restart cannot be pressed (their short ids lived in the
   old process).
3. **Live check (real model):**
   - In Telegram, ask 「特斯拉股价多少」. The reply is plain text: no [n], no 来源 list, no —— footer.
     Links the answer itself contains stay.
   - Ask a bot to save a page or material to 资料库, and press 同意 on the card. The bot replies with what
     it saved. 资料库 shows it, and the source sync indexes it within a few ticks (RAG_INDEXING_ENABLED).
     取消 on another card saves nothing.
   - A notify_telegram or alert message is now 【bot】 plus text only. The earlier note in Stage D's step 4
     about 「来源：[1] title https://…」 no longer applies.
4. Frontend (预测 sidebar entry, 工作区 预测工作台 links, the rail toggle, one-step new bot, dark Grok-like
   skin) goes live only with a push to main (Pages), which needs the owner's OK.

## 2026-10-28 UX batch 2 — P0 字色 (text colour)

Frontend only, no migrations, no backend restart. Plan: `docs/superpowers/plans/2026-10-28-ux-batch2.zh-CN.md` §3.1.

1. Tests (Linux box or Mac): `cd frontend && npm ci && npm run test:i18n` (includes the new
   `scripts/theme-contrast.test.mjs`), then `npm run build`.
2. Goes live only with a push to main (Pages), which needs the owner's OK.
3. **What changed for the eye:**
   - A first-time visitor now gets 外观 = 跟随系统 (was dark). A stored choice is kept.
   - Light theme: 公司 › 总览's top card is a dark card with light text again; every 取消/关闭/次要 button is
     dark text on a transparent pill; a watchlist card's 打开 link no longer vanishes on hover.
4. **Check by hand:** 设置 › 外观 浅色, then 深色 — 总览 top card, any dialog's 取消, a watchlist card hover,
   chat consent/clarification inputs. Take the 9 screenshots (§3.1 step 6) per theme for the notes.

## 2026-10-28 UX batch 2 — P1 资料入库进左侧资料库

Backend + frontend. Migration: `agent_web_sources.uploaded_evidence_id UUID` (`ADD COLUMN IF NOT EXISTS`, on API start).

1. **Tests on the Mac (not run on the Linux box — it has no Postgres):**
   `.venv/bin/python -m pytest -q tests/test_agent_save_lands_in_material_library.py tests/test_telegram_consent_write.py
   tests/test_agent_confirmed_write_failures.py tests/test_agent_web_ingestion.py tests/test_agent_redesign_behaviors.py
   tests/test_telegram_save_card_live_shape.py tests/test_telegram_card_double_tap.py tests/test_agent_saved_material_citations.py
   tests/test_material_analysis_api.py tests/test_agent_tool_catalog.py`.  Frontend: `cd frontend && npm run test:i18n && npm run build`.
2. `deploy.py --backend-only`, restart the API (migration runs on start) and the Telegram bridge.
3. **Optional, for clickable links in Telegram replies:** set `FINBOT_WEB_URL` to the web page's address (the
   Pages URL, e.g. `https://wp1461772181-glitch.github.io/market-evidence-agent/`) in the API's environment.
   Without it the reply's link is the page's own `#materials?...` route (works on the web, plain text in Telegram).
4. **Backfill (Levi said yes, dry run first):**
   `.venv/bin/python -m scripts.backfill_web_sources_to_materials` → prints how many web saves would be filed,
   per stock, and how many have no stock (left alone).  Show Levi the numbers, then
   `.venv/bin/python -m scripts.backfill_web_sources_to_materials --apply`.  Re-running is safe.
5. **What changed:** a confirmed 「保存网页」 now writes 资料库 (uploaded_evidence, same `create_uploaded_evidence`
   as 上传媒体) + owner mapping; search keeps that copy only (the web-only document is withdrawn, the source sync
   skips filed pages, lexical search with a stock reads 资料库). The stock comes from the request, then the
   bot's own company; with none the agent asks 「存到哪只股票的资料库？」 first. The card says
   「存入 资料库 · AAPL ＋ 知识库检索」; the reply ends with 「在资料库查看：[资料库 · AAPL](…#materials?symbol=AAPL&focus=…)」.
   资料库 has 股票 (当前 / 全部股票) and 排序 (发布时间 / 最近存入) selects.
6. **Live check:** Telegram 「把这篇 <网址> 存进 AAPL 的资料库」 → 同意 → reply has the link → web 资料库 › AAPL
   lists it first under 最近存入 as 上传材料 → ask about it in chat, it is cited → save it again: no second row.

## 2026-10-28 UX batch 2 — FinBot name and logo

Display strings only; no migrations. The product is called **FinBot** in the page title, the rail brand, the
entry page, settings eyebrows, the footer, the agent's own prompts and out-of-scope reply, the OpenRouter
X-Title and the README headings. Repo, package paths, Python modules, localStorage keys and the SEC
User-Agent contact string are unchanged. New self-drawn logo (`frontend/public/logo.svg`, `favicon.svg`,
`logo-lockup.svg`): a green orb, two eyes, a small rising-bars mouth.
- `frontend/public/apple-touch-icon.png` is still the old mark (no rasteriser on the Linux box): regenerate it
  from `logo.svg` at 180×180 on the Mac (e.g. `rsvg-convert -w 180 -h 180 frontend/public/logo.svg -o frontend/public/apple-touch-icon.png`) if wanted.
- Tests on the Mac: `tests/test_agent_redesign_behaviors.py` (the out-of-scope reply now names FinBot).
- Backend restart picks up the prompt text; the frontend goes live with the Pages push.

## 2026-10-28 UX batch 2 — P2 FinBot orb avatars

No migration (the avatar column is reused: `orb:<colour>` strings, ≤16 chars). New installs' default bot gets
`orb:finbot`; an existing default bot's `★` is drawn as the FinBot orb.
1. Tests on the Mac: `.venv/bin/python -m pytest -q tests/test_agent_bots.py` (new: an unknown orb colour is
   refused, emoji still accepted). Frontend: `npm run test:i18n` (new `scripts/orb.test.mjs`), `npm run build`.
2. Backend restart (validation); frontend with the Pages push.
3. **Check by hand:** 新建 bot shows a large FinBot orb whose pupils follow the pointer and 12 swatches
   (FinBot + 11 colours) that recolour it at once; the sidebar's current bot and the hovered row look around,
   the others stay still; the chat header shows the same orb; 档案 › 头像 switches 圆球 / 自定义 emoji; old
   emoji avatars still show; macOS 减少动态效果 → pupils centred, no blinking.

## 2026-10-28 UX batch 2 — P3 工作区 MVP

Frontend only; no backend change, no migration. Layout and each bot's current stock live in this browser
(`localStorage`: `market-evidence-agent:workspace:<botId>:v1`, `market-evidence-agent:bot-symbol:<botId>:v1`).
1. Tests: `cd frontend && npm run test:i18n` (new `scripts/workspace-cards.test.mjs`), `npm run build`.
2. Goes live with the Pages push.
3. **Check by hand (acceptance §5 问题4):** open a bot whose focus is only AAPL → 当前股票 is AAPL whatever was
   open before; pick MSFT in the workspace menu (or type another ticker + Enter) → the cards reload, the page
   stays on the bot, and 公司 / 资料库 then open on MSFT; ⚙ 编辑卡片: remove 官方申报, add 简报, move 行情 to the
   top, reload → kept; 恢复默认 restores the six; ⤢ → half width (two columns, larger 3-month line), again →
   full width, again → narrow. No buy/sell wording anywhere (行情 shows close, change with arrow and sign).
   An agent 「切到 NVDA」 on the bot page moves this bot's current stock.
4. Known limits: the 行情 card draws a 3-month close line, not the full candlestick chart (that component needs
   the forecast desk's inspection state); 官方申报 links to 资料库 for scanning instead of minting a scan card
   there; 待你确认 offers 去确认 (opens the card in its conversation) and 忽略.

## 2026-10-28 UX batch 2 — P4 每个 bot 接自己的 Telegram (optional, no limit)

Backend + bridge + frontend. Migrations on API start: new table `agent_telegram_bots` (create_all);
`agent_telegram_bind.bind_code_expires_at`, `agent_telegram_messages.telegram_bot_row_id`
(`ADD COLUMN IF NOT EXISTS`, in `agent_telegram.ensure_columns`). **No data migration**: the existing shared
Telegram bot stays in `agent_telegram_bind` and keeps working unchanged for 助理 and every bot without its own.
1. **Tests on the Mac:** `.venv/bin/python -m pytest -q tests/test_telegram_multi_bot.py tests/test_agent_notify.py
   tests/test_telegram_*.py tests/test_agent_bots.py`, then `python scripts/live_check_telegram.py` (shared bot;
   it is not yet multi-token aware). Frontend `npm run test:i18n && npm run build`.
2. `deploy.py --backend-only`, restart the API, then **restart the Telegram bridge** (one LaunchAgent as before).
   The bridge now takes `data/telegram-bridge.lock`; a second copy exits with a message (409 guard).
   The bridge still needs the shared token to start (unchanged).
3. **Check by hand:**
   - Nothing to do for the existing setup: the shared bot answers as before, 助理 › 档案 › Telegram says it
     uses the shared bot.
   - Another bot › 档案 › Telegram: paste a new BotFather token → shows @username and …last4 (never the token);
     生成绑定码 → 在 Telegram 打开并发送 → within ~60 s 「已绑定 · 运行中」 (the bridge picks new tokens up each
     poll round, ≤ 60 s).
   - Message that Telegram bot directly: the answer comes from that bot, no 「@名字」, no 「【名字】」.
   - Its notify_telegram / alerts arrive there; a bot without its own still arrives via the shared bot with 「【名字】」.
   - Same token on a second bot → 「已经用在别的 bot 上」; a bind code older than 30 min → 「绑定码已过期」
     (now also on the shared bot); a stranger gets nothing; restart the bridge → no replay (offset is stored).
   - Delete the bot → its Telegram row is gone (revoke the token in BotFather).
4. Not done this round: the settings-page overview of every bot's Telegram, persistent Telegram cards across
   restarts (`agent_telegram_cards`), `live_check_telegram.py` multi-token mode.

## 2026-10-28 UX batch 3 — memory, settings, logo

Five stage commits on `worktree-personal-agent` (not pushed): `1340187` memory backend, `6c4ab31` 画像 page,
`02e6b28` logo, `146e864` settings.  **No migration, no new table or column** (`agent_profile_items.origin` is
`String(10)`; the new value `manual` fits).  Backend restart + Pages push.

1. **Tests on the Mac** (all were also run on the Linux box against an embedded Postgres: full suite 1942 passed,
   the 16 failures are the same as at `4553779` — rag/knowledge tests that need pgvector or network):
   `.venv/bin/python -m pytest -q tests/test_agent_memory_batch3.py tests/test_model_catalog_settings.py
   tests/test_agent_profile.py tests/test_agent_memory_layers.py tests/test_agent_bots.py`.
   Frontend `npm run test:i18n` (new `scripts/catalog-form.test.mjs`, memory-items additions) and `npm run build`.
2. **New env (optional):** `MEMORY_JEV_THRESHOLD` (default 0.85), `MEMORY_JEV_DAILY_CAP` (unset: none),
   `MEMORY_JEV_DAILY_PATH` (default `artifacts/memory-jev-daily.json`).  Uses the existing `OPENROUTER_API_KEY`
   (Jev `typesafe/jev-1.13`, question `jev-memory-write-v1`, one 5 s attempt per candidate).  Without the key the
   extractor keeps only exactly-quoted candidates, as before.
3. **What changed**
   - 写记忆失败 but saved (AppleBot): root cause — a bot whose tool scope left out `remember` refused the call
     (`write_tool_not_granted`, the turn appended 「操作未执行」, no card) while the post-turn extractor stored the
     same words as `inferred`.  Now `remember`/`forget` are always on in every bot; the owner's own words that ask to
     remember grant `remember` even if the classifier missed it; an ask that got no card (model answered in prose)
     gets a server-made card from the extractor prompt; a refused remember (quote not the user's) stays refused.
     The turn's `audit.profile_ask` keeps the background extractor out of that turn: card or stated refusal, never a
     silent second write.
   - Background extraction: evidence is snapped to the closest span of the owner's message (the stored quote stays
     their own words; evidence matching < 60 % is dropped), then Jev must give P(remember) ≥ 0.85.
   - 画像: ＋ 添加 form (范围 全局 / 本 bot · 名字, 类型, 内容) → `POST /agent-chat/profile` (origin `manual`, badge
     「你写的」, ranks with confirmed items).  Edit / 范围 / 确认 / 删除 unchanged.
   - Settings: the browser model profiles section (grid, modal, import/export) and the 后台任务模型 card are gone.
     「模型配置」 is the server catalog with the old dialog (display name, provider, Base URL, Model ID, 服务商可用模型
     + 刷新, key from vault or env, 测试并保存).  New owner-only `POST /agent-chat/model-catalog/test` and
     `/agent-chat/model-catalog/models`.  Owner turns outside a bot send `catalog_model` (catalog default);
     **guest chat now needs a personal login** (no browser keys anywhere).  Material analysis default and the
     per-analysis picker name a catalog entry.
   - **Keys migration:** none needed — `agent_model_catalog.migrate_legacy` (since stage 2) already folded the background
     model and `data/model-profiles.json` into the catalog.  Keys a browser kept only in its own OPFS/IndexedDB are not
     on the server: re-add those models once in 模型配置 (测试并保存).  The old browser storage is left untouched.
   - Logo: `logo.svg`, `favicon.svg`, `logo-lockup.svg`, the in-app `orb:finbot`, and `apple-touch-icon.png` (180×180,
     rendered with headless Chrome — no Mac step needed).
4. **Check by hand**
   - AppleBot (a bot with a narrow tool scope): 「记住我看好苹果的服务业务」 → a 「记住这条偏好」 card (web and Telegram
     buttons); 同意 → 画像 shows it once; 取消 → nothing in 画像, also not as 推断.  「以后金额都用亿美元」 in a bot whose
     model answers in prose → still a card.
   - 画像 › ＋ 添加: 全局 「金额都用亿美元」 and 本 bot · AppleBot 「先讲服务业务」 → both listed with 「你写的」; the next
     AppleBot turn follows both, another bot only the global one.
   - 设置 › 模型配置: no browser profile grid; ＋ 添加模型 → pick DeepSeek, paste a key → 服务商可用模型 fills, pick one →
     测试并保存 → card shows 已配置; 编辑 without retyping the key → 刷新 / 测试并保存 work (stored key); switch the provider
     → asks for the key again; env mode with `DEEPSEEK_API_KEY` works.  材料分析默认模型 lists catalog models.
   - Logged out (guest): the chat says to log in; nothing asks for a key.
   - Favicon / sidebar logo / bot orb show the new FinBot (brows, sideways smile, arrow tuft, gold sparkle).
5. Not done: ponytail on the box CLIs is CodeBot's (no AGENTS.md in this repo, nothing added).  The old
   `model-profiles.mjs` storage code and `/agent-chat/local-model-profiles` stay for now (only its provider list is
   still used); remove in a later cleanup.

## UX batch 3 hotfix — 添加模型 clickable again; black `$` face for the logo and every orb

Frontend only, no migration, no backend restart.  Hard-refresh the browser (favicon may need a tab reopen).

1. **「＋ 添加模型」 was unclickable.**  The dialog's backdrop sits inside the settings card, which is a direct
   child of `main.agent-admin`; opening the dialog marked every `main` child `inert` except `.profile-backdrop`,
   so the card holding the dialog went inert too.  Now every child is inerted except the one that contains the
   dialog (`siblingsToInert` in `catalog-form.mjs`).  Escape / Tab trap / focus restore / click-outside unchanged.
2. **Logo + orbs.**  One face for everything: a round body with two white `$` eyes that follow the pointer
   (centred under 减少动态效果).  `orb:finbot` (and the old `★`) is that face in black — the brand mark,
   `logo.svg`, `favicon.svg`, `logo-lockup.svg`, `apple-touch-icon.png` (180×180, headless Chrome).
   `orb:<colour>` is the same face in that colour.  New bots already default to `orb:finbot`, i.e. black `$`
   (the unused `randomOrbColor` is gone).  No DB rewrite: existing orb avatars pick up the new face on render;
   bots with emoji/text avatars are unchanged — owners can switch to 圆球 in 档案.

Mac check: `cd frontend && npm run test:i18n && npm run build`, then open 设置 → 模型 → ＋ 添加模型 and type into
the fields; check the header logo, favicon and a bot avatar show the black `$` face.

## FinBot logo — 三姿势随机过渡 + 页签偷瞄笑 + 16 种 bot 头像

Frontend only, no migration, no backend restart.  Hard-refresh (favicon may need a tab reopen).

1. **Brand.**  `logo.svg` (歪头大小眼), `logo-peek.svg` (捧报偷瞄笑), `logo-3d.svg` (3/4 侧颜) are copied from the
   3-poses design.  The sidebar `BrandLogo` crossfades (600 ms) to a random *other* pose every 4–8 s; reduced
   motion keeps 歪头 still.  `favicon.svg` and `apple-touch-icon.png` are always 偷瞄笑 (no shadow/sparkle);
   `logo-lockup.svg` is 歪头 + Fin**Bot** (gold).
2. **Bot avatars.**  `bot:<style>-<mood>`: kline / pure / bull / square × idle 绿 / focus 蓝 / bull 黄 / mono 白
   = 16 faces, 4×4 grid in 新建 bot and 档案 → 头像.  New bots default to `bot:kline-idle`.  `bot:` (not
   `finbot:`) keeps every value within the server's 16-character avatar cap, so the backend is unchanged.
   Old `orb:<colour>` / `orb:finbot` / `★` render as the K线 face in the nearest eye colour; no DB rewrite.

Mac check: `cd frontend && npm run test:i18n && npm run build`; watch the sidebar logo change pose, check the tab
icon is 偷瞄笑, and pick a few of the 16 faces in a bot's 档案.

## 放宽 Agent token / 时间限制 — 只留真故障兜底 (Levi 2026-10-28)

Backend only, no migration.  Restart the API.  Soft budgets are gone or far out of reach; what still stops a turn
is a real fault: no progress (model/tool silent), network error, invalid key, broken protocol, content_filter, the
same call replayed past `MAX_REPLAYED_CALLS`, or a scheduled task's own `token_budget`.

| Where | Before | Now |
|---|---|---|
| `decision_request` / `stream_answer` `max_tokens` (thinking counts against it) | 4096 with tools / 8192 | 32768 (`MAX_OUTPUT_TOKENS`) |
| `finish_reason=length` | turn failed (`provider_incomplete_response`) | text so far is delivered; only a cut tool call or empty text still fails |
| SSE total / line / reasoning size (**the NewsBot 19:04/09/12 truncations**: Chinese thinking as `\u` escapes) | 1 MB / 64 KB / 64 KB | 20 MB / 4 MB / 4 MB (memory guards) |
| `AGENT_PROVIDER_TIMEOUT_SECONDS` / `_STREAM_SECONDS` default (max) | 35 (90) / 90 (180) | 180 (600) / 600 (1200) |
| `SOFT_TURN_SECONDS` / `HARD_TURN_SECONDS` / `SOFT_TURN_TOKENS` | 90 / 150 / 150 k | 600 / 1200 / 1 M |
| `IDLE_SECONDS` / `TOOL_IDLE_SECONDS` (inactivity, not wall clock) | 120 / 45 | 300 / 180 |
| `WEB_RESEARCH_TIMEOUT_SECONDS` (and web_research's own inner cap, was 40) / knowledge retrieval | 42 / 30 | 120 / 60 |
| tool result in the model protocol (`bounded_tool_result`) | 8 KB | 64 KB (`TOOL_RESULT_BYTES`); the turn's protocol trim follows to half of `RUNNING_REQUEST_BYTES` (128 KB by default) |
| `notify_telegram`: a sentence with a figure and no [n] | refused (`unsupported_number`) | sent, result carries `warning: unsupported_number` |
| claim gate in enforce, route D (product/how-to) answer with no citations | lines dropped as `claims_missing` | recorded only (as shadow) |
| `news_monitor` template `tool_scope` | 9 read tools | `None` (all tools) |

Unchanged on purpose: write tools still need the confirmation card; `progress_watchdog` still stops a silent turn.

**Owner action (not automated):** the template change only affects new bots.  An existing NewsBot keeps its stored
scope — in 档案 set its 工具范围 to 「不限制」 (Levi's NewsBot `ca3c9be3…` is already `null`).

**Mac `.env`:** `CLAIM_QUOTE_GATE` / `CLAIM_JEV_SUPPORT` are `enforce` there although the comment says shadow;
CodeBot is switching them to `shadow` and restarting.  If `AGENT_PROVIDER_TIMEOUT_SECONDS` / `_STREAM_SECONDS` /
`WEB_RESEARCH_TIMEOUT_SECONDS` are set in `.env`, delete them or raise them to the new defaults — an old value wins.

Mac check: `pytest -q tests/test_agent_progress_v5.py tests/test_agent_g4.py tests/test_agent_chat_provider.py
tests/test_agent_prompt_contract.py tests/test_agent_provider_retry.py tests/test_agent_chat_stream.py
tests/test_agent_tool_executor_timeout.py tests/test_agent_notify.py tests/test_claim_gate_direct_shadow.py`, then ask
NewsBot a question that needs two or three web searches and check the answer arrives whole.
