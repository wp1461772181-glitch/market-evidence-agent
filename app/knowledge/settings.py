from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from .chunking import CHUNKER_VERSION, FORMAT_VERSION
from .repository import embedding_generation
MODEL_ID='BAAI/bge-m3'
MODEL_REVISION='5617a9f61b028005a4858fdac845db406aefb181'
RERANKER_ID='BAAI/bge-reranker-v2-m3'
RERANKER_REVISION='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
@dataclass(frozen=True)
class KnowledgeSettings:
    enabled: bool
    model_path: Path
    revision: str
    generation: str
    milvus_uri: str
    cpu_threads: int
    max_concurrency: int
    queue_capacity: int
    threshold_path: Path
    reranker_path: Path=Path('data/knowledge/models/bge-reranker-v2-m3')
    reranker_revision: str=RERANKER_REVISION
    strategy: str='dense'
    @classmethod
    def from_env(cls, *, validate_model: bool | None=None):
        enabled=os.getenv('RAG_ENABLED','false').strip().lower() in {'1','true','yes'}
        root=Path(__file__).resolve().parents[2]
        revision=os.getenv('RAG_MODEL_REVISION',MODEL_REVISION)
        if revision!=MODEL_REVISION: raise ValueError('unsupported BGE-M3 revision; create a new embedding generation')
        path=Path(os.getenv('RAG_MODEL_PATH',root/'data/knowledge/models/bge-m3')).expanduser()
        if (enabled if validate_model is None else validate_model) and (not (path/'config.json').is_file() or not any((path/n).is_file() for n in ('pytorch_model.bin','model.safetensors'))):
            raise ValueError('pinned BGE-M3 model files are missing; download the fixed revision first')
        generation=embedding_generation(MODEL_ID,revision,FORMAT_VERSION,CHUNKER_VERSION)
        strategy=os.getenv('RAG_RETRIEVAL_STRATEGY','dense').strip().lower()
        if strategy not in {'dense','bm25','hybrid','hybrid_rerank'}: raise ValueError('unsupported RAG retrieval strategy')
        reranker_path=Path(os.getenv('RAG_RERANKER_PATH',root/'data/knowledge/models/bge-reranker-v2-m3')).expanduser()
        reranker_revision=os.getenv('RAG_RERANKER_REVISION',RERANKER_REVISION)
        if reranker_revision!=RERANKER_REVISION: raise ValueError('unsupported reranker revision')
        return cls(enabled,path,revision,generation,os.getenv('RAG_MILVUS_URI','http://127.0.0.1:19530'),
          max(1,int(os.getenv('RAG_CPU_THREADS','4'))),max(1,int(os.getenv('RAG_MAX_CONCURRENCY','1'))),
          max(0,int(os.getenv('RAG_QUEUE_CAPACITY','1'))),Path(os.getenv('RAG_THRESHOLD_PATH',root/'data/knowledge/calibration/threshold.json')),
          reranker_path,reranker_revision,strategy)
