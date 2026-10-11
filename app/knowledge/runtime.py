from __future__ import annotations
import asyncio, contextvars, json, logging, threading, time
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
_recovery_thread=None
_recovery_stop=threading.Event()
_recovery_lock=threading.Lock()
_runtime_lock=threading.RLock()

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
        self._foreground_condition=threading.Condition()
        self._foreground_waiters=0
    async def run(self,*args): return await self.run_fn(self.function,*args)
    async def run_fn(self,function,*args):
        with self._foreground_condition:
            self._foreground_waiters+=1
            self._foreground_condition.notify_all()
        try:
            await self.slots.acquire()
        except BaseException:
            with self._foreground_condition:
                self._foreground_waiters-=1
                self._foreground_condition.notify_all()
            raise
        loop=asyncio.get_running_loop()
        future=loop.run_in_executor(self.executor,function,*args)
        def release(_):
            loop.call_soon_threadsafe(self.slots.release)
            with self._foreground_condition:
                self._foreground_waiters-=1
                self._foreground_condition.notify_all()
        future.add_done_callback(release)
        return await asyncio.shield(future)
    def run_background_sync(self,function,*args):
        """Run one exclusive background slice, yielding between foreground calls."""
        with self._foreground_condition:
            self._foreground_condition.wait_for(lambda:self._foreground_waiters==0)
            # Keep the lock through submit so a foreground call arriving now is
            # queued behind at most this one bounded background slice.
            future=self.executor.submit(function,*args)
        return future.result()
    def close(self): self.executor.shutdown(wait=True,cancel_futures=False)

class KnowledgeRuntime:
    def __init__(self,settings: KnowledgeSettings,embedder:DenseEmbedder,index:MilvusIndex,threshold:float):
        self.settings=settings; self.embedder=embedder; self.index=index; self.threshold=threshold
        # Sync jobs, retrieval embeddings, and reranking share one model instance.
        # A single worker keeps release/reload and embedding calls exclusive.
        workers=1
        self.executor=BoundedInference(embedder.encode,workers=workers,queue_capacity=settings.queue_capacity)
        self.ready=False
        self.last_health_check=0.0
        self.health_error=None
    def warm(self):
        self.index.ensure_collection(self.settings.generation)
        vector=self.embedder.encode(['warmup dense retrieval'])
        if len(vector)!=1 or len(vector[0])!=1024: raise RuntimeError('embedding warmup invalid')
        self.index.client.load_collection(self.index.collection_name)
        self.index.health(); self.ready=True
    async def encode(self,text):
        try:return (await self.executor.run([text]))[0]
        except Exception:
            self.ready=False
            raise
    async def _search_call(self,function,*args):
        try:return await self.executor.run_fn(function,*args)
        except Exception:
            self.ready=False
            raise
    async def search(self,vector,filter_expr,limit): return await self._search_call(self.index.search,vector,filter_expr,limit)
    async def search_strategy(self,strategy,query,filter_expr,limit):
        if strategy=='dense':
            vector=await self.encode(query)
            return await self._search_call(self.index.search_dense,vector,filter_expr,limit)
        if strategy=='bm25':return await self._search_call(self.index.search_bm25,query,filter_expr,limit)
        vector=await self.encode(query)
        return await self._search_call(self.index.search_hybrid,vector,query,filter_expr,limit)
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
    def health(self):
        age=max(0.0,time.monotonic()-self.last_health_check)
        healthy=bool(self.ready and age<=30.0)
        if not healthy:self.ready=False
        return {'ready':healthy,'generation':self.settings.generation,
                'milvus':'healthy' if healthy else 'stale_or_unavailable','health_age_seconds':round(age,1)}
    def close(self):
        self.ready=False;self.executor.close()
        try:self.index.close()
        finally:self.embedder.release_model()

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
    with _runtime_lock:
      if _runtime and _runtime.ready: return _runtime
      if _runtime:
        try:_runtime.close()
        except Exception:pass
        _runtime=None
      from sqlalchemy import inspect
      from ..database import engine
      required={'knowledge_documents','knowledge_chunks','knowledge_index_tasks','knowledge_candidates','knowledge_extraction_runs','knowledge_upload_owners','low_confidence_questions'}
      missing=required-set(inspect(engine).get_table_names())
      if missing: raise RuntimeError('explicit knowledge migration is required before enabling RAG')
      embedder=None; index=None; instance=None
      try:
        embedder=DenseEmbedder(settings.model_path,cpu_threads=settings.cpu_threads)
        index=MilvusIndex(settings.milvus_uri,settings.generation)
        instance=KnowledgeRuntime(settings,embedder,index,_threshold(settings) if settings.strategy=='dense' else 0.0)
        instance.warm(); instance.last_health_check=time.monotonic(); _runtime=instance
        return instance
      except Exception:
        if instance:
            try:instance.close()
            except Exception:pass
        else:
            if index:
                try:index.close()
                except Exception:pass
            if embedder:
                try:embedder.release_model()
                except Exception:pass
        raise

def stop_runtime():
    global _runtime
    _recovery_stop.set()
    with _runtime_lock:
        if _runtime:
            _runtime.close(); _runtime=None

def get_runtime() -> KnowledgeRuntime|None:
    return _runtime if _runtime and _runtime.ready else None

def start_runtime_background(retry_seconds:float=15.0):
    """Keep API startup independent of Milvus/model warmup; retry bounded reconnects."""
    global _recovery_thread
    if _recovery_thread and _recovery_thread.is_alive():return
    _recovery_stop.clear()
    def recover():
        global _runtime
        while not _recovery_stop.is_set():
            try:
                runtime=get_runtime()
                if runtime is None:
                    # Serialize the final stop check and initialization with shutdown.
                    # start_runtime may load settings before taking this reentrant lock.
                    with _runtime_lock:
                        if not _recovery_stop.is_set():start_runtime()
                elif time.monotonic()-runtime.last_health_check>=min(10.0,max(2.0,retry_seconds)):
                    try:
                        runtime.index.health()
                        runtime.last_health_check=time.monotonic();runtime.health_error=None;runtime.ready=True
                    except Exception as exc:
                        runtime.ready=False;runtime.health_error=type(exc).__name__
                        log.warning('knowledge backend health check failed; reconnect scheduled (%s)',type(exc).__name__)
                        with _runtime_lock:
                            if _runtime is runtime:
                                try:runtime.close()
                                except Exception:pass
                                _runtime=None
            except Exception as exc:
                log.warning('knowledge runtime is not ready; retry scheduled (%s)',type(exc).__name__)
            _recovery_stop.wait(max(1.0,retry_seconds))
    _recovery_thread=threading.Thread(target=recover,name='knowledge-runtime-recovery',daemon=True)
    _recovery_thread.start()

def get_tokenizer():
    runtime=get_runtime()
    if runtime:return runtime.embedder.tokenizer
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(KnowledgeSettings.from_env().model_path),local_files_only=True)
