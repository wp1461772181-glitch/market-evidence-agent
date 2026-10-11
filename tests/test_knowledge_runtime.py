import asyncio, threading, time

def test_disabled_settings_do_not_require_model_files(monkeypatch,tmp_path):
    from app.knowledge.settings import KnowledgeSettings
    monkeypatch.setenv('RAG_ENABLED','false');monkeypatch.setenv('RAG_MODEL_PATH',str(tmp_path/'missing'))
    assert KnowledgeSettings.from_env().enabled is False

def test_enabled_settings_require_model_files(monkeypatch,tmp_path):
    from app.knowledge.settings import KnowledgeSettings
    monkeypatch.setenv('RAG_ENABLED','true');monkeypatch.setenv('RAG_MODEL_PATH',str(tmp_path/'missing'))
    import pytest
    with pytest.raises(ValueError,match='model files are missing'): KnowledgeSettings.from_env()

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
