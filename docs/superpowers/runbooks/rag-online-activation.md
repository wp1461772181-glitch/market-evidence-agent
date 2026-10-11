# RAG controlled local activation and rollback

Release status (2026-10-11): independent review accepted both final guards; both regressions passed, and the isolated exact-tree release candidate passed the ordinary backend suite (679 passed, 6 skipped), frontend i18n tests, and production build. This is candidate verification only. Live health, indexing, browser acceptance, and online readiness remain pending host activation and live checks.

This is preparation only. Do not run it until the independent review approves the activation. It does not edit `.env`, private profiles, launch agents, or the release tree. Automatic sync indexes public SEC/FAQ rows and only private uploads with an existing verified owner mapping; unmapped legacy uploads remain excluded.

The configured default is `hybrid`: on the synthetic four-strategy heldout set, hybrid and hybrid-rerank both reached Recall@5 1.0 / MRR 1.0 with 5/6 negative retrieval candidates; p50 latency was 91 ms versus 2,250 ms on local CPU. This is a resource tradeoff, not a claim of production factuality.

The fixed real AMD gate has since passed in a disposable test database and unique nonproduction Milvus collection. Production is still disabled; the commands below remain for the primary reviewer to execute after independent acceptance.

## Review deployment allowlist

Include only these RAG implementation files from the current working tree; preserve unrelated learner-comment edits and other pre-existing dirty files:

```text
app/agent_chat_stream.py
app/agent_tools.py
app/knowledge/api.py
app/knowledge/retrieval.py
app/knowledge/runtime.py
app/knowledge/settings.py
app/knowledge/source_sync.py
app/knowledge/sources.py
app/main.py
deployment/milvus-compose.yml
frontend/src/AgentChatPanel.tsx
frontend/src/KnowledgeAdminPage.tsx
frontend/src/locales/en-US.json
frontend/src/locales/zh-CN.json
scripts/run_api_server.sh
scripts/sync_knowledge_sources.py
```

Review-only regression/evidence files are `tests/test_agent_tools.py`, `tests/test_api_server_launchagent.py`, `tests/test_knowledge_amd_real.py`, `tests/test_knowledge_chat.py`, `tests/test_knowledge_runtime.py`, `tests/test_knowledge_source_sync.py`, `tests/test_knowledge_sources.py`, `tests/test_material_sync_acceptance.py`, `docs/superpowers/runbooks/rag-online-activation.md`, and `dev-notes/rag-online.md`. The existing project deployment uses the stable root `.env` and `data/releases/current`; do not publish `.env`, `data/`, `.venv-rag/`, model weights, test taskdir fixtures, or credentials.

## Inspect IDs without reading provider configuration

```sh
cd /Users/wupeng/monash-ai/projects/market-evidence-agent
PUBLIC_API_MODE=false .venv/bin/python -c 'from sqlalchemy import text; from app.database import engine; c=engine.connect(); print("FAQ IDs:", c.execute(text("select id from agent_faq order by id")).scalars().all()); print("AMD filing:", c.execute(text("select id from sec_filing_inventory where id=:id and symbol=:symbol and content_status=:status and content_excerpt is not null and content_excerpt_sha256 is not null"), {"id":"9af2ef08-e7e6-4e2d-bf47-68d1a163957c","symbol":"AMD","status":"fetched"})).scalars().all()); c.close()'
```

## First bounded activation after review

Run from the project root in the local service shell. The command uses the pinned local environment/models and overrides flags for this process only.

```sh
RAG_ENABLED=true RAG_INDEXING_ENABLED=true RAG_RETRIEVAL_STRATEGY=hybrid \
RAG_MODEL_PATH=/Users/wupeng/monash-ai/projects/market-evidence-agent/data/knowledge/models/bge-m3 \
RAG_RERANKER_PATH=/Users/wupeng/monash-ai/projects/market-evidence-agent/data/knowledge/models/bge-reranker-v2-m3 \
RAG_THRESHOLD_PATH=/Users/wupeng/monash-ai/projects/market-evidence-agent/data/knowledge/calibration/threshold.json \
/bin/sh scripts/run_api_server.sh
```

For the existing LaunchAgent, the reviewed nonsecret `.env` additions are:

```dotenv
RAG_ENABLED=true
RAG_INDEXING_ENABLED=true
RAG_RETRIEVAL_STRATEGY=hybrid
RAG_MODEL_PATH=/Users/wupeng/monash-ai/projects/market-evidence-agent/data/knowledge/models/bge-m3
RAG_RERANKER_PATH=/Users/wupeng/monash-ai/projects/market-evidence-agent/data/knowledge/models/bge-reranker-v2-m3
RAG_THRESHOLD_PATH=/Users/wupeng/monash-ai/projects/market-evidence-agent/data/knowledge/calibration/threshold.json
```

After review and applying those values to the existing stable `.env`, the activation command is:

```sh
launchctl kickstart -k "gui/$(id -u)/com.market-evidence-agent.api"
curl --fail --max-time 2 http://127.0.0.1:8000/health
curl --fail --max-time 2 http://127.0.0.1:8000/health/knowledge
```

On startup, the bounded MaterialLibrary sync worker automatically queues and indexes sources from PostgreSQL. Its first cycle prioritizes the saved AMD target plus the three seeded product FAQ entries. Further fetched SEC rows are backfilled in keyset pages of at most five per source type and sync cycle. Only uploads with a `knowledge_upload_owners` mapping are eligible; the legacy unmapped uploads remain out of scope. The owner mapping controls private retrieval. It does not re-upload the source body.

Use the administrative sync status endpoint to inspect cursors and processed counts, and use the existing exact-ID import API only to reconcile a known changed source immediately:

```sh
curl --fail --max-time 2 -H 'X-Chat-Session: <verified-owner-session>' -H 'X-App-Access-Key: <local-operator-key>' http://127.0.0.1:8000/knowledge/sync-status
curl --fail --max-time 2 http://127.0.0.1:8000/health/knowledge
```

Index tasks are idempotent and retry after expired leases; PostgreSQL remains authoritative and Milvus remains rebuildable. The CLI remains available for bounded exact-ID SEC/FAQ reconciliation. The authenticated import API remains the owner-verified path for known uploads. No path bulk publishes unknown-owner rows or fetches remote source text.

## Rollback

Set `RAG_ENABLED=false` in the stable `.env` and restart the existing API LaunchAgent with the same `launchctl kickstart -k` command. Core API continues to run with RAG disabled. Indexed documents and source records remain in place; do not drop collections, delete volumes, or remove source rows. Report exact health/index status before any recovery action.
