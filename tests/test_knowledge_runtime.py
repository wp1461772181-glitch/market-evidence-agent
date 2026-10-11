import asyncio, threading

def test_disabled_settings_do_not_require_model_files(monkeypatch,tmp_path):
    from app.knowledge.settings import KnowledgeSettings
    monkeypatch.setenv('RAG_ENABLED','false');monkeypatch.setenv('RAG_MODEL_PATH',str(tmp_path/'missing'))
    assert KnowledgeSettings.from_env().enabled is False

def test_enabled_settings_require_model_files(monkeypatch,tmp_path):
    from app.knowledge.settings import KnowledgeSettings
    monkeypatch.setenv('RAG_ENABLED','true');monkeypatch.setenv('RAG_MODEL_PATH',str(tmp_path/'missing'))
    import pytest
    with pytest.raises(ValueError,match='model files are missing'): KnowledgeSettings.from_env()

def test_default_retrieval_strategy_uses_measured_hybrid_tradeoff(monkeypatch,tmp_path):
    from app.knowledge.settings import KnowledgeSettings
    monkeypatch.setenv('RAG_ENABLED','false');monkeypatch.setenv('RAG_MODEL_PATH',str(tmp_path/'missing'))
    monkeypatch.delenv('RAG_RETRIEVAL_STRATEGY',raising=False)
    assert KnowledgeSettings.from_env().strategy=='hybrid'

def test_cancelled_waiter_keeps_capacity_slot_until_worker_finishes():
    from app.knowledge.runtime import BoundedInference
    started=threading.Event(); finish=threading.Event(); calls=[]
    def blocking(values):
        calls.append(values); started.set(); finish.wait(3); return values
    async def scenario():
        runtime=BoundedInference(blocking,workers=1,queue_capacity=0)
        first=asyncio.create_task(runtime.run(['first']))
        await asyncio.to_thread(started.wait,1)
        first.cancel()
        try: await first
        except asyncio.CancelledError: pass
        second=asyncio.create_task(runtime.run(['second']))
        await asyncio.sleep(.05)
        assert len(calls)==1
        finish.set()
        assert await second==['second']
        runtime.close()
    asyncio.run(scenario())

def test_explicit_index_use_still_validates_missing_model(monkeypatch,tmp_path):
    import pytest
    from app.knowledge.settings import KnowledgeSettings
    monkeypatch.setenv('RAG_ENABLED','false');monkeypatch.setenv('RAG_MODEL_PATH',str(tmp_path/'missing'))
    with pytest.raises(ValueError,match='model files are missing'):
        KnowledgeSettings.from_env(validate_model=True)

def test_hybrid_rerank_forces_single_worker_even_when_configured_higher():
    from types import SimpleNamespace
    from app.knowledge.runtime import KnowledgeRuntime
    settings=SimpleNamespace(strategy='hybrid_rerank',max_concurrency=8,queue_capacity=0)
    runtime=KnowledgeRuntime(settings,SimpleNamespace(encode=lambda x:x),SimpleNamespace(),0.0)
    assert runtime.executor.executor._max_workers==1
    runtime.executor.close()

def test_rerank_restore_failure_marks_runtime_unavailable(monkeypatch):
    from types import SimpleNamespace
    import pytest
    import app.knowledge.reranking as reranking
    from app.knowledge.runtime import KnowledgeRuntime
    class Embedder:
        def encode(self,values):return values
        def release_model(self):pass
        def reload_model(self):raise OSError('reload failed')
    class Index:
        def close(self):pass
    settings=SimpleNamespace(strategy='hybrid_rerank',max_concurrency=4,queue_capacity=0,reranker_path='unused',reranker_revision='fixed')
    runtime=KnowledgeRuntime(settings,Embedder(),Index(),0.0);runtime.ready=True
    monkeypatch.setattr(reranking,'rerank',lambda *args: [(0,1.0)])
    monkeypatch.setattr(reranking,'unload',lambda:None)
    with pytest.raises(RuntimeError,match='runtime unavailable after model restore failure'):
        runtime._rerank_exclusive('q',['p'])
    assert runtime.ready is False
    runtime.executor.close()

def test_runtime_readiness_requires_low_confidence_table(monkeypatch):
    import pytest
    from types import SimpleNamespace
    import sqlalchemy
    import app.knowledge.runtime as runtime_module
    from app.knowledge.models import KnowledgeCandidate,KnowledgeChunk,KnowledgeDocument,KnowledgeExtractionRun,KnowledgeIndexTask,KnowledgeUploadOwner
    required={'knowledge_documents','knowledge_chunks','knowledge_index_tasks','knowledge_candidates','knowledge_extraction_runs','knowledge_upload_owners'}
    monkeypatch.setattr(sqlalchemy,'inspect',lambda _engine:SimpleNamespace(get_table_names=lambda:sorted(required)))
    monkeypatch.setattr('app.database.engine',object())
    settings=SimpleNamespace(enabled=True)
    with pytest.raises(RuntimeError,match='explicit knowledge migration'):
        runtime_module.start_runtime(settings)


def test_health_endpoint_does_not_keep_stale_ready_after_refresh_deadline(monkeypatch):
    import time
    from types import SimpleNamespace
    import app.knowledge.runtime as runtime_module
    from app.main import knowledge_health
    runtime=SimpleNamespace(ready=True,last_health_check=time.monotonic()-31,
        settings=SimpleNamespace(generation='g'*64),health_error=None)
    monkeypatch.setattr(runtime_module,'get_runtime',lambda:runtime)
    monkeypatch.setenv('RAG_ENABLED','true')
    result=knowledge_health()
    assert result['enabled'] is True and result['ready'] is False
    assert result['milvus']=='stale_or_unavailable' and runtime.ready is False


def test_unreachable_milvus_and_core_health_are_bounded(disposable_database,monkeypatch):
    import socket,time
    import pytest
    import app.knowledge.runtime as runtime_module
    from app.knowledge.vector_store import MilvusIndex
    from fastapi.testclient import TestClient
    from app.main import app
    probe=socket.socket();probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
    started=time.monotonic()
    with pytest.raises(Exception):MilvusIndex(f'http://127.0.0.1:{port}','unreachable-test',timeout=0.5)
    assert time.monotonic()-started<2.0
    monkeypatch.setenv('RAG_ENABLED','true')
    monkeypatch.setattr(runtime_module,'start_runtime_background',lambda:None)
    with TestClient(app) as client:
        started=time.monotonic();knowledge=client.get('/health/knowledge');core=client.get('/health')
        assert knowledge.status_code==200 and knowledge.json()['ready'] is False
        assert core.status_code==200 and core.json()['status']=='ok'
        assert time.monotonic()-started<2.0


def test_runtime_health_failure_clears_ready_and_schedules_reconnect(monkeypatch):
    import time
    from types import SimpleNamespace
    import app.knowledge.runtime as runtime_module
    failed=threading.Event();retried=threading.Event()
    class DeadIndex:
        def health(self):raise ConnectionError('local test endpoint unavailable')
    class Runtime:
        ready=True;last_health_check=time.monotonic()-4;index=DeadIndex();health_error=None
        def close(self):failed.set()
    original=runtime_module._runtime
    original_thread=runtime_module._recovery_thread
    runtime_module._runtime=Runtime()
    runtime_module._recovery_thread=None;runtime_module._recovery_stop.clear()
    monkeypatch.setattr(runtime_module,'start_runtime',lambda *args,**kwargs:retried.set())
    try:
        runtime_module.start_runtime_background(retry_seconds=1)
        assert failed.wait(2)
        assert retried.wait(3)
        assert runtime_module._runtime is None
    finally:
        runtime_module._recovery_stop.set()
        runtime_module._runtime=original
        runtime_module._recovery_thread=original_thread
