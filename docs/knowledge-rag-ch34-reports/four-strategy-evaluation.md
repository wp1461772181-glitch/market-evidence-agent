# Chapter 4 retrieval evaluation

Fixture: `ch04-synthetic-v1` — synthetic only. Real PostgreSQL, Milvus, BGE-M3 and cross-encoder. Production section calls `app.knowledge.retrieval._retrieve`; component section calls index APIs directly.

Milvus pkg/v2.6.13; reranker revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`.
Dense cosine threshold calibrated on calibration split only: `0.595427` (generation `3386fed36ac953aaed803573790c82d70818e8e42a9c5f0cd9e20d554413275d`, selection F1 0.941); heldout excluded.

## Production retrieval path

| Split | Strategy | queries (+/-) | Mean ms | P50 ms | Recall@5 positive | MRR positive | Negative FP | Scope leaks |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| calibration | dense | 12 (8/4) | 88.8 | 93.4 | 1.000 | 1.000 | 1 | 0 |
| calibration | bm25 | 12 (8/4) | 6.1 | 5.5 | 1.000 | 1.000 | 3 | 0 |
| calibration | hybrid | 12 (8/4) | 89.9 | 93.6 | 1.000 | 1.000 | 3 | 0 |
| calibration | hybrid_rerank | 12 (8/4) | 2507.1 | 2510.6 | 1.000 | 1.000 | 3 | 0 |
| heldout | dense | 12 (6/6) | 87.7 | 92.9 | 0.667 | 0.667 | 1 | 0 |
| heldout | bm25 | 12 (6/6) | 4.3 | 4.5 | 1.000 | 1.000 | 4 | 0 |
| heldout | hybrid | 12 (6/6) | 86.9 | 94.7 | 1.000 | 1.000 | 5 | 0 |
| heldout | hybrid_rerank | 12 (6/6) | 2533.0 | 2216.0 | 1.000 | 1.000 | 5 | 0 |

## Direct-index component benchmark

| Split | Strategy | queries (+/-) | Mean ms | P50 ms | Recall@5 positive | MRR positive | Negative FP | Owner leaks |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| calibration | dense | 12 (8/4) | 5.4 | 1.0 | 1.000 | 1.000 | 3 | 0 |
| calibration | bm25 | 12 (8/4) | 1.2 | 0.9 | 1.000 | 1.000 | 3 | 0 |
| calibration | hybrid | 12 (8/4) | 1.2 | 1.1 | 1.000 | 1.000 | 3 | 0 |
| calibration | hybrid_rerank | 12 (8/4) | 435.4 | 115.5 | 1.000 | 1.000 | 3 | 0 |
| heldout | dense | 12 (6/6) | 0.8 | 0.8 | 1.000 | 1.000 | 5 | 0 |
| heldout | bm25 | 12 (6/6) | 0.9 | 0.9 | 1.000 | 1.000 | 4 | 0 |
| heldout | hybrid | 12 (6/6) | 1.2 | 1.1 | 1.000 | 1.000 | 5 | 0 |
| heldout | hybrid_rerank | 12 (6/6) | 128.2 | 122.0 | 1.000 | 1.000 | 5 | 0 |

Formal all-claims Faithfulness was not measured. `retrieval_marker_diagnostic` is not a generated-answer or claim-level measure. On held-out production retrieval, dense positive Recall@5 was 0.667, and hybrid produced negative candidates in 5 of 6 negative queries. Held-out labels were fixed in the fixture before this run; no labels or thresholds are tuned from heldout results.

Per-query-type positive/negative counts and failed cases are in the JSON report.

## Answer generation acceptance boundary

A separate DeepSeek `hybrid_rerank` SSE subset ran against real disposable PostgreSQL, Milvus 2.6.13, BGE-M3, and the pinned cross-encoder. Two synthetic fixture prompts completed with tool, sufficiency, and answer steps; number/unit/source/citation checks passed. One prior run had a sufficiency failure for the second prompt; a retry passed. This bounded two-case live subset is point-in-time evidence and not a general hallucination rate. The retrieval marker metric above is not answer faithfulness.

## Verification commands and companion acceptance

- Final independently verified backend suite: **648 passed, 4 skipped, 41 warnings** (17.20s). This is the current final result; earlier counts are superseded and are not repeated here.

## Incremental documentation-stage note

This documentation cleanup records current launcher requirements, direct TXT/Markdown/PDF text uploads (without OCR), and the separation between the fail-closed optional simulated profile, the primary's `/tmp/market-rag-ch34-20261011/browser_demo_app.py` visual adapter, and live generation evidence. No implementation or fixture labels were changed in this documentation stage. The primary owns final review and screenshots.
