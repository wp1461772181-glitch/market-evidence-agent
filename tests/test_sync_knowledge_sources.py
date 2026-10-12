from types import SimpleNamespace
from uuid import uuid4


def test_scoped_analysis_cli_passes_only_selected_generation_chunks(monkeypatch, capsys):
    import sys
    import scripts.sync_knowledge_sources as cli
    import app.knowledge.indexing as indexing
    import app.knowledge.runtime as runtime

    selected_id=uuid4();source_id=uuid4();generation='g'*64
    doc=SimpleNamespace(source_table='material_analysis_versions',source_id=f'uploaded_media:{source_id}',
                        source_version=str(uuid4()),source_hash='a'*64)
    class Scalars:
        def all(self): return [selected_id]
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def scalars(self,statement): return Scalars()
        def commit(self): pass
    settings=SimpleNamespace(generation=generation,model_path='unused',cpu_threads=1,milvus_uri='unused')
    captured={}
    monkeypatch.setattr(sys,'argv',['sync_knowledge_sources.py','--type','analysis','--source-id',str(source_id),'--index','--batch-size','500'])
    monkeypatch.setattr(cli,'SessionLocal',Session)
    monkeypatch.setattr(cli.KnowledgeSettings,'from_env',classmethod(lambda cls,**kwargs:settings))
    monkeypatch.setattr(cli,'load_analysis_document',lambda *args:doc)
    monkeypatch.setattr(cli,'get_tokenizer',lambda:object())
    monkeypatch.setattr(cli,'ingest_documents',lambda *args:{'documents':1,'chunks':1})
    monkeypatch.setattr(runtime,'get_runtime',lambda:SimpleNamespace(embedder=object(),index=object()))
    monkeypatch.setattr(indexing,'index_pending',lambda *args,**kwargs:captured.update(kwargs) or {'indexed':1,'failed':0,'idle':0})
    assert cli.main()==0
    assert captured['chunk_ids']=={selected_id}
    assert captured['max_tasks']==200
    assert capsys.readouterr().out


def test_empty_or_invalid_analysis_cli_skips_indexer_construction(monkeypatch, capsys):
    import sys
    import scripts.sync_knowledge_sources as cli
    import app.knowledge.embedding as embedding
    import app.knowledge.vector_store as vector_store
    import app.knowledge.runtime as runtime

    generation='g'*64
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def commit(self): pass
    settings=SimpleNamespace(generation=generation)
    def forbidden(*args,**kwargs): raise AssertionError('empty scope must not build RAG resources')
    monkeypatch.setattr(sys,'argv',['sync_knowledge_sources.py','--type','analysis','--source-id','invalid','--index'])
    monkeypatch.setattr(cli,'SessionLocal',Session)
    monkeypatch.setattr(cli.KnowledgeSettings,'from_env',classmethod(lambda cls,**kwargs:settings))
    monkeypatch.setattr(cli,'get_tokenizer',lambda:object())
    monkeypatch.setattr(cli,'ingest_documents',lambda *args:{'documents':0,'chunks':0})
    monkeypatch.setattr(embedding,'DenseEmbedder',forbidden)
    monkeypatch.setattr(vector_store,'MilvusIndex',forbidden)
    monkeypatch.setattr(runtime,'get_runtime',forbidden)
    assert cli.main()==0
    assert "'accepted_sources': 0" in capsys.readouterr().out
