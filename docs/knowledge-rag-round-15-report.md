# RAG repair and acceptance round 15

Date: 2026-10-11. Scope excludes concurrency/load testing. This records implementation and worker-side verification only; the primary agent retains independent final acceptance.

## Reproduced and repaired

- Copied the independent probe into `tests/test_rag_review_acceptance.py` so pytest loads the disposable `test_*` database fixture. Initial failures reproduced SEC source URL edits remaining retrievable and approved public FAQ snapshots being rejected. SEC current-source validation now checks the full frozen `source_version`; reviewed candidates derive publication scope from `approved_public`/`approved_private`, reviewer and review time while retaining original owner provenance. The test exercises `retrieve_faq` for a positive result and confirms revocation blocks that result.
- Added cutoff retrieval for frozen document versions using persisted availability, observed and creation times. A PostgreSQL test verifies a version before the cutoff is chosen while the live query selects the newest version. Naive and future cutoffs return `invalid_historical_cutoff`. Historical SEC reads validate current filing authorization separately and do not compare frozen text/provenance to a subsequently changed SEC row body.
- Retrieved the exact SQLAlchemy 2.0 ORM select documentation through Context7 before adding cutoff predicates. Evidence: `/tmp/market-rag-accept-20261011/context7-sqlalchemy-history.sse` (the first versioned library ID lookup was unavailable; resolved the official SQLAlchemy ORM docs and queried again).

## Real evaluation and limitations

- Original `corpus.json`, `queries.json` and `adjudications.json` were left byte-for-byte unchanged during this round. Their current SHA-256 values are recorded in the command log for this run. Added `queries-supplement.json` with unrelated FAQ/material negatives, paraphrases, absent facts, wrong symbol/owner, and truncated excerpt boundary cases.
- Calibration threshold selection now maximizes calibration F1 with a precision guard and prefers higher thresholds on exact ties. It uses calibration entries only; held-out queries do not choose the threshold.
- Real BGE-M3 + Milvus 2.6.13 + disposable PostgreSQL integration completed, including two indexing crash/recovery points, retry/idempotency, reconciliation, retrieval, and two SSE chat turns. Threshold: `0.6206313967704773`; calibration F1: `0.75`. Original adjudicated held-out Recall@5: `0.381` (8/21). Supplement held-out positive recall: `0.25` (2/8); unrelated rejections: `5/5`. Strict negative failures: 0; cross-owner leakage: 0; maximum tool JSON: 617 bytes. The target Recall@5 ≥ 0.85 is not met. This small synthetic corpus cannot support an acceptable single dense threshold; RAG should remain disabled pending a larger representative evidence set and a separate approved retrieval-design decision.
- Exact cited excerpt checks apply to the reported positive queries. Because the threshold rejects many relevant queries, a 100% fact-coverage result is not claimed across the full held-out set.
- Machine-readable detail: ignored runtime artifact `data/knowledge/logs/rag-evaluation.json`; original and supplemental splits are reported separately. The SSE artifact is `data/knowledge/logs/knowledge-chat-sse-demo.json`.

## Chat and other checks

- Real API/TestClient path used actual session ownership, PostgreSQL conversation/message persistence and reload history, real tools, BGE embedding, Milvus index and retrieval. Only provider decision/answer functions were replaced by explicit test functions. Two turns produced two tool calls and two decision + two answer requests; another session received 404 on history. The fixture checks SSE `conversation`, `tool_start`, `tool_result`, `delta` and `done` events with completed tool traces.
- Focused RAG/source/repository/extraction/indexing/runtime/upload/chat tests: 32 passed. Full backend suite: 570 passed, 1 skipped, 40 warnings (`PUBLIC_API_MODE=false`). Real integration: 1 passed. Frontend `test:i18n`: 28 passed. `git diff --check`: passed.
- Frontend production build remains blocked by pre-existing uncommitted archive UI changes in `frontend/src/AgentChatPanel.tsx` and `frontend/src/chat-archive.mjs`: missing `writeAtomically` export/signature mismatch and `Array.findLastIndex` target-library errors. These are outside the RAG integration changes and were left untouched.
- A dedicated local browser acceptance server/launcher was not added or started. The real TestClient/API integration is available; browser acceptance remains unverified. No live paid provider calls, production DB writes, deployment, or concurrency tests were run.
