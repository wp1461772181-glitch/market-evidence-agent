# Knowledge RAG Chapter 3/4 API evidence

Checked 2026-10-11 through the configured Context7 MCP helper. Captured source responses under `/tmp/market-rag-ch34-20261011/`.

| Library | Locked/runtime version | Official API checked | Source/evidence |
|---|---:|---|---|
| Milvus / PyMilvus | 2.6.13 | `FunctionType.BM25`, analyzer-enabled VARCHAR, sparse vector index, `AnnSearchRequest`, `MilvusClient.hybrid_search`, `RRFRanker(k=60)` and request-level `filter` | Context7: `milvus-schema.sse`, `milvus-bm25-hybrid.sse`; runtime `inspect.signature` output recorded in `worker-events.jsonl` |
| FlagEmbedding | 1.3.5 | `FlagReranker` CPU/device configuration and `compute_score` over query-passage pairs with batch size | Context7: `flagembedding-rerank.sse`, `flagembedding-rerank-1_3_5.sse`; installed model revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` |
| FastAPI | repository lock/environment | `APIRouter`, `Header`, `Query`, `include_router`; implemented additive router mounted by `app.main` | Context7 response: `/tmp/market-rag-ch34-20261011/fastapi-ch34.sse` |
| SQLAlchemy | 2.0.45 | `select`, ORM scalar queries, session transaction context, additive `create_all(checkfirst=True)` migration | Context7 response: `/tmp/market-rag-ch34-20261011/sqlalchemy-ch34.sse` |

The new collection is `knowledge_hybrid_v1_<BGE generation>`. It has `auto_id=False`, scalar authority fields, analyzer-enabled `text`, 1024-dimension dense `embedding`, BM25 output `sparse`, and a sparse inverted index. The existing dense collection schema is not upgraded in place. Actual local Milvus reported `pkg/v2.6.13`; the new collection was created, loaded, and exercised by real four-strategy evaluation.

The reranker snapshot was downloaded from the model repository at the immutable revision above. Runtime swaps models serially on CPU: release BGE-M3 weights, score at most 50 PG-approved candidates with the cross-encoder, release cross-encoder weights, then restore BGE-M3. `RAG_ENABLED` remains false by default; admin indexing has its own explicit `RAG_INDEXING_ENABLED` gate.

## 2026-10-11 schema validation follow-up

- PyMilvus 2.6.13 `describe_collection()` returns VARCHAR analyzer configuration under `fields[].params` (with `enable_analyzer` serialized as `"true"` and `analyzer_params` as JSON text), and exposes BM25 under `functions[]` as a `FunctionType.BM25` enum. The local schema validator follows this returned representation, validates 1024 dimension and index type/metric, creates only absent dense/sparse indexes, and fails actionably on incompatible existing schemas.
- The analyzer configured here is Milvus's built-in `jieba` tokenizer with `cnalphanumonly` filter, an equivalent Chinese analyzer path. It is recorded in schema and must be present for an existing collection to pass readiness.
