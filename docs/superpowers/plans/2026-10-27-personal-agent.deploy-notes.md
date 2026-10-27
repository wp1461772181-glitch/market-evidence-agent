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
