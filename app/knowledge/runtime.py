from __future__ import annotations
import asyncio, contextvars, json, logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from .embedding import DenseEmbedder
from .settings import KnowledgeSettings
from .vector_store import MilvusIndex
log=logging.getLogger(__name__)

@dataclass
class ResearchContext:
    owner_hash: str
    cutoff: object|None=None
    citations: list[dict]|None=None
_context: contextvars.ContextVar[ResearchContext|None]=contextvars.ContextVar('knowledge_research_context',default=None)
_runtime=None

@contextmanager
def research_context(owner_hash: str, cutoff=None):
    value=ResearchContext(owner_hash,cutoff,[])
    token=_context.set(value)
    try: yield value
    finally: _context.reset(token)

def current_research_context() -> ResearchContext|None: return _context.get()

class BoundedInference:
    def __init__(self, function, *, workers: int=1, queue_capacity: int=1):
        self.function=function; self.executor=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='bge-m3')
        self.slots=asyncio.Semaphore(workers+queue_capacity)
    async def run(self,*args): return await self.run_fn(self.function,*args)
    async def run_fn(self,function,*args):
        await self.slots.acquire()
        loop=asyncio.get_running_loop()
        future=loop.run_in_executor(self.executor,function,*args)
        def release(_): loop.call_soon_threadsafe(self.slots.release)
        future.add_done_callback(release)
        return await asyncio.shield(future)
    def close(self): self.executor.shutdown(wait=True,cancel_futures=False)

class KnowledgeRuntime:
    def __init__(self,settings: KnowledgeSettings,embedder:DenseEmbedder,index:MilvusIndex,threshold:float):
        self.settings=settings; self.embedder=embedder; self.index=index; self.threshold=threshold
        # Reranking unloads the shared BGE model; a single worker makes that
        # unload/reload sequence exclusive with every encode call.
        workers=1 if settings.strategy=='hybrid_rerank' else settings.max_concurrency
        self.executor=BoundedInference(embedder.encode,workers=workers,queue_capacity=settings.queue_capacity)
        self.ready=False
    def warm(self):
        self.index.ensure_collection(self.settings.generation)
        vector=self.embedder.encode(['warmup dense retrieval'])
        if len(vector)!=1 or len(vector[0])!=1024: raise RuntimeError('embedding warmup invalid')
        self.index.client.load_collection(self.index.collection_name)
        self.index.health(); self.ready=True
    async def encode(self,text): return (await self.executor.run([text]))[0]
    async def search(self,vector,filter_expr,limit): return await self.executor.run_fn(self.index.search,vector,filter_expr,limit)
    async def search_strategy(self,strategy,query,filter_expr,limit):
        if strategy=='dense':
            vector=await self.encode(query)
            return await self.executor.run_fn(self.index.search_dense,vector,filter_expr,limit)
        if strategy=='bm25':return await self.executor.run_fn(self.index.search_bm25,query,filter_expr,limit)
        vector=await self.encode(query)
        return await self.executor.run_fn(self.index.search_hybrid,vector,query,filter_expr,limit)
    async def rerank(self,query,passages):
        return await self.executor.run_fn(self._rerank_exclusive,query,passages)
    def _rerank_exclusive(self,query,passages):
        from .reranking import rerank,unload
        released=False
        try:
            self.embedder.release_model();released=True
            return rerank(query,passages,str(self.settings.reranker_path),self.settings.reranker_revision)
        except Exception:
            if not released:self.ready=False
            raise
        finally:
            try:
                if released:
                    unload();self.embedder.reload_model()
            except Exception as exc:
                self.ready=False
                raise RuntimeError('knowledge runtime unavailable after model restore failure') from exc
    def health(self): return {'ready':self.ready,'generation':self.settings.generation,'milvus':self.index.health()}
    def close(self): self.ready=False; self.executor.close(); self.index.close()

def _threshold(settings):
    try: value=json.loads(settings.threshold_path.read_text('utf-8'))
    except (OSError,ValueError) as exc: raise RuntimeError('calibrated RAG threshold file is missing or invalid') from exc
    if value.get('generation')!=settings.generation: raise RuntimeError('calibrated threshold generation mismatch')
    threshold=value.get('cosine_threshold')
    if not isinstance(threshold,(int,float)) or not -1<=threshold<=1: raise RuntimeError('invalid calibrated RAG threshold')
    return float(threshold)

def start_runtime(settings: KnowledgeSettings|None=None):
    global _runtime
    settings=settings or KnowledgeSettings.from_env()
    if not settings.enabled: return None
    if _runtime and _runtime.ready: return _runtime
    from sqlalchemy import inspect
    from ..database import engine
    required={'knowledge_documents','knowledge_chunks','knowledge_index_tasks','knowledge_candidates','knowledge_extraction_runs','knowledge_upload_owners','low_confidence_questions'}
    missing=required-set(inspect(engine).get_table_names())
    if missing: raise RuntimeError('explicit knowledge migration is required before enabling RAG')
    embedder=DenseEmbedder(settings.model_path,cpu_threads=settings.cpu_threads)
    index=MilvusIndex(settings.milvus_uri,settings.generation)
    instance=KnowledgeRuntime(settings,embedder,index,_threshold(settings) if settings.strategy=='dense' else 0.0)
    instance.warm(); _runtime=instance
    return instance

def stop_runtime():
    global _runtime
    if _runtime:
        _runtime.close(); _runtime=None

def get_runtime() -> KnowledgeRuntime|None:
    return _runtime if _runtime and _runtime.ready else None

def get_tokenizer():
    runtime=get_runtime()
    if runtime:return runtime.embedder.tokenizer
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(KnowledgeSettings.from_env().model_path),local_files_only=True)
