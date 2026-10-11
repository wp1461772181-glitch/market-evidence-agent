# Local Chapter 3/4 Knowledge RAG

Production retrieval remains disabled by default. The review launcher binds only to loopback and targets a local PostgreSQL database named `test_*`.

## Isolated local review launcher

Supply all four `CH34_TEST_*` settings explicitly in the shell. Use local test-only values; do not put credentials in source files or reports:

```sh
export CH34_TEST_ADMIN_DSN='<local PostgreSQL admin DSN targeting postgres on 127.0.0.1:55432>'
export CH34_TEST_DATABASE_NAME='test_market_rag_ch34_demo'
export CH34_TEST_DATABASE_URL='<local PostgreSQL URL targeting test_market_rag_ch34_demo on 127.0.0.1:55432>'
export CH34_TEST_OPERATION_KEY='<separate random test-only key, at least 32 characters>'
./scripts/run_ch34_local.sh
```

The launcher validates the admin and target URLs, creates the test database if needed, migrates its knowledge tables, seeds synthetic FAQ content, and starts the API at `http://127.0.0.1:8018` and web app at `http://127.0.0.1:5180`. It enables `hybrid_rerank` with the pinned local BGE-M3 and reranker snapshots. It does not read `.env`, alter a production release, or clean up the named test database on exit.

Ports 8018 and 5180 must be free before launch. If either loopback port is already occupied, the script exits; retain the configured ports and do not replace their listeners.

## Knowledge Admin and direct uploads

Open the web app and choose Agent → Settings → Research Knowledge. Knowledge Admin supports direct TXT, Markdown, and PDF uploads. Text is extracted from those files; PDF OCR is not supported. Uploads are associated with the current verified session, then can be imported and indexed. Existing FAQ and accepted SEC excerpts can also be selected for import. Review chunks and indexing status in the admin UI. The production owner login authorizes knowledge administration writes; guest chat session keys do not grant admin access. The isolated demo has its own test-only operation key.

The normal launcher has no simulated model profile by default. If `CH34_ENABLE_SIMULATED_PROVIDER=true` is explicitly set, it writes a clearly labelled fake profile to `/tmp/market-rag-ch34-20261011/local-model-profiles.json`; requests through that profile fail closed. It can support limited visual interaction but does not simulate successful final generation and is not provider acceptance evidence. The separate `/tmp/market-rag-ch34-20261011/browser_demo_app.py` adapter was used only for the primary's isolated visual acceptance; it is not the normal launcher or a reproducible final-generation path. Do not describe launcher operation as final generation acceptance.

## Operator extraction

Conversation extraction is a separate opt-in operator task, not part of launcher startup. Set `DATABASE_URL` to an appropriate local database, plus `KNOWLEDGE_OPERATOR_ENABLED=true`, `KNOWLEDGE_OPERATOR_MODEL`, and `KNOWLEDGE_OPERATOR_API_KEY` in the process environment. `KNOWLEDGE_OPERATOR_BASE_URL` is optional. The operator credential must be independently configured; the browser chat key is not reused. Provide only explicitly opted-in conversation UUIDs.

Run a bounded pass once:

```sh
DATABASE_URL='<local PostgreSQL URL>' KNOWLEDGE_OPERATOR_ENABLED=true KNOWLEDGE_OPERATOR_MODEL='<operator model name>' KNOWLEDGE_OPERATOR_API_KEY='<operator credential from your secret manager>' .venv-rag/bin/python -m scripts.extract_knowledge once --conversation '<conversation-uuid>'
```

Repeat at a fixed interval (minimum 60 seconds):

```sh
DATABASE_URL='<local PostgreSQL URL>' KNOWLEDGE_OPERATOR_ENABLED=true KNOWLEDGE_OPERATOR_MODEL='<operator model name>' KNOWLEDGE_OPERATOR_API_KEY='<operator credential from your secret manager>' .venv-rag/bin/python -m scripts.extract_knowledge interval --seconds 3600 --conversation '<conversation-uuid>'
```

The interval command runs until stopped and keeps a cursor scoped to the supplied conversation IDs. No scheduler is installed by the project. Extraction stages review candidates; it does not automatically publish them.

## Evaluation and limits

Run the synthetic four-strategy evaluation only with a disposable `test_*` database URL:

```sh
TEST_DATABASE_URL='<local disposable test_* PostgreSQL URL>' PUBLIC_API_MODE=false .venv-rag/bin/python scripts/evaluate_rag_ch04.py
```

See [the four-strategy evaluation](knowledge-rag-ch34-reports/four-strategy-evaluation.md) for unchanged retrieval metrics and current result counts. Its synthetic marker check is a retrieval proxy, not generated-answer faithfulness. Formal all-claims Faithfulness was not measured. Two live synthetic cases are bounded checks only; they do not establish a general hallucination rate. Held-out hybrid retrieval returned negative candidates in 5 of 6 negative queries, and dense held-out positive Recall@5 was 0.667. No held-out labels or thresholds were tuned from these results.
