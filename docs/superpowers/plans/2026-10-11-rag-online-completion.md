# RAG online completion: incremental execution plan

Date: 2026-10-11. This note extends the completed Chapter 3/4 work without changing PostgreSQL, Milvus 2.6.13, BGE-M3, or the pinned reranker.

## Scope and order

1. Restore the existing Milvus container and add bounded recovery/readiness behavior without touching volumes or collections.
2. Remove legacy SEC human-review gates while preserving fetched/body-hash/time/cutoff/source-version checks and private-upload ownership.
3. Add a saved-analysis read tool with provenance distinct from raw filing retrieval; improve SEC excerpts to select substantive text.
4. Make source indexing resumable and idempotent for explicitly selected existing sources and newly saved eligible material; never publish unknown-owner uploads.
5. Run a fixed, genuine-source-grounded retrieval gate using local BGE/Milvus; report limitations without tuning labels or thresholds against held-out outcomes.
6. Prepare isolated API launch/rollback commands only. Do not change private profiles, root env, launch agents, or the live release.

## Guardrails

- Preserve the current worktree and all PostgreSQL/Milvus data. No down-v, volume removal, data reset, model/database swap, secret output, production activation, commit, or push.
- Use only bounded CLI source IDs and the disposable `test_*` database for new fixtures.
- No provider calls. AI analyses stay separate from raw authoritative evidence.
- Record phase outcomes, exact commands, failures, health, measured retrieval outcomes, and remaining gaps in `dev-notes/rag-online.md`.

## Acceptance evidence

- Regression tests reject tampered, future, body-missing, unauthorized, or out-of-cutoff material while allowing valid fetched SEC material in `pending_review`.
- Saved-analysis tool selects latest version and verifies source/evidence linkage, carries original quotes when present, and does not expose provider configuration or hidden reasoning.
- Sync retries/resumes idempotently and reports per-source readiness; no unknown-owner legacy upload is bulk published.
- Actual BGE/Milvus retrieval outcomes include a substantive paragraph beyond the opening excerpt, paraphrase including Chinese-to-English, exact accession/symbol/year, unsupported facts, and cross-owner/symbol checks.
- API remains available with RAG explicitly enabled but unready; readiness/status calls remain bounded.
- No workflow/agent-loop or intent/conversation architecture work in this increment.
