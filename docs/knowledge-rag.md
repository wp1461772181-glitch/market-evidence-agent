> 当前状态：部分实现完成。原独立验收发现的 SEC 来源快照和公开审核 FAQ 两项问题已由阶段 15 修复；但代表性补充评估显示单一 dense threshold 未达到 Recall@5 目标，因此 RAG 保持默认关闭。使用前请阅读[独立验收报告](knowledge-rag-review.md)和[阶段 15 报告](knowledge-rag-round-15-report.md)。

# Dense knowledge retrieval (local RAG)

This optional service adds semantic retrieval to the existing research FAQ and saved-material chat tools. PostgreSQL is authoritative for source snapshots, owner/scope, current permissions, and retrieval results. Milvus stores rebuildable dense vectors. The first profile is local BAAI/bge-m3 (1024-dimensional dense vectors) with Milvus 2.6.13. No external embedding service is used.

## Runtime prerequisites

The base app imports knowledge support lazily and starts with ordinary `.venv` dependencies. RAG remains disabled by default. Runtime retrieval requires the pinned optional dependency lock, Python 3.12, local BGE-M3 and reranker snapshots, loopback Milvus, and the already reviewed knowledge schema. Knowledge writes/indexing have the independent `RAG_INDEXING_ENABLED` gate.

For a persistent macOS LaunchAgent, place these values in its `EnvironmentVariables` dictionary: `MARKET_EVIDENCE_API_PYTHON` set to the absolute `.venv-rag/bin/python` path; `RAG_ENABLED=true`; `RAG_INDEXING_ENABLED=true`; `RAG_RETRIEVAL_STRATEGY=hybrid_rerank`; `RAG_MILVUS_URI=http://127.0.0.1:19530`; and absolute `RAG_MODEL_PATH` / `RAG_RERANKER_PATH` values pointing at the pinned snapshots. Review the plist and explicitly reload the agent when applying such a change. No migration or enablement is performed by this candidate. Never index test fixtures or synthetic demo materials in the public runtime.

Infrastructure readiness does not establish answer quality. The latest held-out report remains below its retrieval target, so production RAG must stay disabled pending an accepted quality gate.

## Local setup

Use Python 3.12 and the dedicated exact dependency lock. `RAG_ENABLED` remains off by default, so a fresh checkout can start the base app without model files or the RAG packages loaded.

```sh
python3.12 -m venv .venv-rag
.venv-rag/bin/python -m pip install -r requirements-rag.lock

docker compose -f deployment/milvus-compose.yml up -d
.venv-rag/bin/python -c 'from huggingface_hub import snapshot_download; snapshot_download(repo_id="BAAI/bge-m3", revision="5617a9f61b028005a4858fdac845db406aefb181", local_dir="data/knowledge/models/bge-m3", allow_patterns=["config.json", "modules.json", "pytorch_model.bin", "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt", "1_Pooling/config.json"])'
```

Set `RAG_MODEL_PATH` only if the snapshot is stored elsewhere. To enable an explicitly approved local runtime, configure the complete prerequisite set above, ensure the pinned model files are present, and point `RAG_THRESHOLD_PATH` at a calibration artifact bound to the current generation.

## Source and index operations

Run the explicit additive migration before ingest. It creates only the knowledge tables; startup never creates these tables implicitly.

```sh
.venv-rag/bin/python -m scripts.migrate_knowledge
.venv-rag/bin/python -m scripts.ingest_knowledge --source faq --id 1 --id 2 --id 3
.venv-rag/bin/python -m scripts.ingest_knowledge --source upload --id <uploaded-evidence-uuid> --allow-public
.venv-rag/bin/python -m scripts.ingest_knowledge --source sec --id <sec-filing-uuid>
.venv-rag/bin/python -m scripts.index_knowledge --resume --max-tasks 10000
```

Old uploads are not indexed by scanning. Public indexing requires the explicit allowlist flag; private indexing requires an existing verified upload-owner mapping. A changed source snapshot or embedding generation gets separate immutable chunks. PostgreSQL rechecks current source content and permissions before returning a hit. Withdrawn or changed sources are not trusted because a stale vector still exists.

## Optional conversation extraction

Extraction is off unless the independent operator profile is explicitly enabled. Configure `KNOWLEDGE_OPERATOR_ENABLED`, `KNOWLEDGE_OPERATOR_MODEL`, and the operator credential through the process environment; the browser chat key is never reused. Batch cursors are scoped to the sorted conversation IDs supplied to the command. Only completed opted-in turns are considered, candidates retain exact evidence quotes, and review is required before product FAQ publication.

```sh
.venv-rag/bin/python -m scripts.extract_knowledge once --conversation <conversation-uuid>
.venv-rag/bin/python -m scripts.extract_knowledge review <candidate-uuid> --decision approve --reviewer <reviewer-id> --scope private
```

The review command only publishes `product_faq`; financial claims and predictions remain unpublished candidates. No recurring scheduler is installed.

## Calibration, app startup, and demo

The calibration and held-out fixture runner uses the real local BGE model, Milvus, and automatically disposable `test_*` PostgreSQL database. It saves its threshold from calibration queries only and reports original and separately adjudicated held-out labels.

```sh
HF_HOME="$PWD/data/knowledge/hf" HF_HUB_CACHE="$PWD/data/knowledge/hf/hub" XDG_CACHE_HOME="$PWD/data/knowledge/xdg" TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 PUBLIC_API_MODE=false KNOWLEDGE_REAL_INTEGRATION=1 KNOWLEDGE_DEMO_SSE=1 .venv-rag/bin/python -m pytest tests/test_knowledge_evaluation_real.py -q
RAG_ENABLED=true .venv-rag/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health/knowledge
.venv-rag/bin/python -m scripts.demo_knowledge_chat
```

The integration uses actual chat routes, session ownership, conversation/message persistence and history reload, tools, PostgreSQL, Milvus and BGE. Only provider decision and answer functions are mocked, so it makes no paid LLM request. The SSE history path is verified, but no dedicated browser acceptance server/launcher was delivered in this round.

## Limits

- The benchmark is a small synthetic corpus using DEMO/DEMB names, not real issuer evidence or production throughput.
- Calibration is specific to the saved generation and fixture corpus. Stage 15 measured threshold `0.6206313967704773`; original adjudicated held-out Recall@5 was `0.381` and supplemental held-out positive recall was `0.25`, below the `0.85` target. Do not enable RAG based on this fixture threshold.
- SEC retrieval returns only stored text. Truncated excerpts are explicitly labelled with their content source URL and content observation time.
- Internal historical retrieval now selects frozen eligible knowledge snapshots using persisted availability, observation and record-creation times, while checking current authorization. It is not a model point-in-time claim; legacy sources without persisted observation/availability timestamps are excluded.
- Operator extraction is independent and disabled by default; no scheduler is installed. Only explicitly selected conversations are considered. Financial claims remain candidates and cannot be published by the review CLI.
- No production load, browser, local browser-test launcher, or live paid language-model validation is claimed.

## 2026-10-11 focused correction status

The current calibration artifact was selected from 26 calibration cases (original plus separately labelled supplement) and errata; held-out labels were not used in threshold selection. After pre-ANN authorized-ID filtering, the current synthetic report records threshold `0.4296050072`, strict held-out recall 15/20 (0.75), separately adjudicated recall 16/21 (0.762), supplemental held-out positives 2/4 (0.50), and raw ANN Recall@5 21/21 (1.0), with zero cross-owner leaks. The requested 0.85 held-out retrieval target is not met, so semantic retrieval quality remains partial; do not tune on held-out cases or treat this fixture as issuer-level quality evidence. Preserve the threshold JSON generated by the real integration runner; rerun that gate after changing corpus, chunker, model revision, or labels.

A migration adds nullable `knowledge_documents.snapshot_integrity`. Existing snapshots without this value intentionally fail closed; after migration, re-ingest and re-index approved sources to make them eligible. Retrieval selects current or cutoff-eligible authorized chunk IDs in PostgreSQL before Milvus ANN ranking. The eligible ID filter is bounded to 5,000 entries and fails closed above that bound.

For the focused correction evidence, limitations and provider behavior, see `knowledge-rag-review.md` and `dev-notes/ch03.md`. Browser API fixture launcher and browser verification remain pending.
