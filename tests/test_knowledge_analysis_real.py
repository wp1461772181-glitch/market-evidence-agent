"""Opt-in BGE/Milvus acceptance for saved analysis on disposable PostgreSQL."""
import asyncio
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.event_provider import ProviderResult
from app.forecast_v2_models import EvidenceEventVersionV2
from app.material_analysis import request_material_analysis, run_material_analysis_once
from app.material_analysis_models import MaterialAnalysisJob, MaterialAnalysisVersion
from app.models import SecFilingInventory
from app.knowledge.analysis_sources import load_analysis_document
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.indexing import index_pending
from app.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexTask
from app.knowledge.repository import embedding_generation, ingest_documents
from app.knowledge.retrieval import retrieve_analysis, retrieve_materials
from app.knowledge.runtime import KnowledgeRuntime, research_context
import app.knowledge.runtime as runtime_module
from app.knowledge.settings import KnowledgeSettings, MODEL_ID, MODEL_REVISION
from app.knowledge.vector_store import MilvusIndex
from scripts.migrate_knowledge import migrate

pytestmark = pytest.mark.skipif(os.getenv("KNOWLEDGE_ANALYSIS_REAL") != "1", reason="explicit saved-analysis BGE/Milvus gate")


class SavedAnalysisProvider:
    """Fake only for creation of a saved analysis; retrieval embeddings stay real."""
    def extract(self, **kwargs):
        return ProviderResult(content=json.dumps({
            "schema_version":"material-analysis-v1","prompt_version":"material-analysis-prompt-v1",
            "summary":"AMD reported quarterly revenue of $6.8 billion, up 8% year over year.",
            "facts":[{"id":"f1","statement":"Quarterly revenue was $6.8 billion.","citations":[{"quote":"quarterly revenue of $6.8 billion"}]}],
            "supporting":[],"counter":[],"uncertainties":[],
            "key_numbers":[{"name":"Revenue","value_text":"$6.8 billion","period":"quarter","citations":[{"quote":"quarterly revenue of $6.8 billion"}]}],
        },ensure_ascii=False),response_model="acceptance-fake-provider",usage={})


def test_saved_analysis_real_bge_milvus_search_and_citation_readback(disposable_database,monkeypatch):
    from app.knowledge.api import router

    Base.metadata.create_all(engine)
    migrate(engine)
    now=datetime.now(UTC); source_id=uuid4(); sec=SecFilingInventory(
        id=source_id,symbol="AMD",cik="0000002488",accession_number=f"0000002488-{now:%y}-{uuid4().hex[:6]}",
        form="10-Q",filed_at=date.today()-timedelta(days=1),accepted_at=now.isoformat(),primary_document="amd-quarterly.htm",
        source_url="https://www.sec.gov/Archives/edgar/data/2488/fixture.htm",source="real-analysis-acceptance",
        review_status="pending_review",reviewed_at=None,observed_at=now-timedelta(minutes=2),content_status="fetched",
        content_observed_at=now-timedelta(minutes=1),content_excerpt="AMD reported quarterly revenue of $6.8 billion, up 8% year over year. Net income was $0.9 billion.",
        content_excerpt_sha256="",content_truncated=False,content_source_url=None,content_document_name="AMD Quarterly Results",content_kind="text")
    sec.content_excerpt_sha256=hashlib.sha256(sec.content_excerpt.encode()).hexdigest()
    request=None; analysis_id=None; evidence_id=None; doc_id=None; runtime=None; index=None; previous_runtime=runtime_module._runtime
    with Session(engine) as session,session.begin():session.add(sec)
    try:
        with Session(engine) as session:
            request=request_material_analysis(db=session,source_type="official_filing",source_id=source_id,idempotency_key=f"real-analysis-{uuid4()}",model_override="saved-analysis-fake")
        outcome=run_material_analysis_once(session_factory=lambda:Session(engine),provider=SavedAnalysisProvider(),job_id=__import__('uuid').UUID(request["job_id"]))
        assert outcome["status"]=="succeeded"
        analysis_id=__import__('uuid').UUID(outcome["analysis_id"])
        with Session(engine) as session:
            doc=load_analysis_document(session,analysis_id)
            assert doc and doc.source_type=="ai_analysis" and "not official" not in doc.title.casefold()
            job=session.scalar(select(MaterialAnalysisJob).where(MaterialAnalysisJob.id==__import__('uuid').UUID(request["job_id"])))
            evidence_id=job.evidence_version_id
        base=KnowledgeSettings.from_env(validate_model=True)
        generation=embedding_generation(MODEL_ID,MODEL_REVISION,"saved-analysis-real-v1-"+uuid4().hex,"saved-analysis-real-v1")
        settings=replace(base,generation=generation)
        monkeypatch.setattr(KnowledgeSettings,"from_env",classmethod(lambda cls,**kwargs: settings))
        embedder=DenseEmbedder(settings.model_path,cpu_threads=2)
        index=MilvusIndex(settings.milvus_uri,generation);index.ensure_collection(generation)
        class Tokenizer:
            def encode(self,text,**kwargs):return embedder.tokenizer.encode(text,**kwargs)
        with Session(engine) as session,session.begin():
            counts=ingest_documents(session,[doc],Tokenizer(),generation)
            assert counts["documents"]==1 and counts["chunks"]>0
            saved=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.source_version==str(analysis_id)))
            doc_id=saved.id
            chunk_ids=set(session.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id==doc_id)).all())
        indexed=index_pending(lambda:Session(engine),embedder,index,generation,max_tasks=200,chunk_ids=chunk_ids)
        assert indexed["indexed"]==len(chunk_ids),indexed
        with Session(engine) as session:
            assert ingest_documents(session,[doc],Tokenizer(),generation)["skipped"]==1
        runtime=KnowledgeRuntime(settings,embedder,index,0.0);runtime.warm();runtime_module._runtime=runtime
        with research_context("A"*64) as ctx:
            result=asyncio.run(retrieve_analysis("AMD","AMD quarterly revenue $6.8 billion up 8%",5))
            assert result["ok"] and result["data"]
            assert any("$6.8 billion" in row.get("excerpt","") for row in result["data"])
            assert ctx.citations and ctx.citations[0]["source_type"]=="ai_analysis"
            citation_id=ctx.citations[0]["chunk_id"]
            from app.agent_chat_stream import _restore_citations
            restored=_restore_citations(ctx.citations,"A"*64)
            assert restored and restored[0]["source_type"]=="ai_analysis"
            citation_sidecar=list(ctx.citations)
        with research_context("A"*64):
            assert not asyncio.run(retrieve_analysis("AAPL","AMD quarterly revenue $6.8 billion",5)).get("data")
        with research_context("A"*64,now-timedelta(days=1)):
            assert not asyncio.run(retrieve_analysis("AMD","AMD quarterly revenue $6.8 billion",5)).get("data")
        with research_context("A"*64):
            original=asyncio.run(retrieve_materials("AMD","AMD quarterly revenue $6.8 billion",5))
            assert not original.get("data")
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            response=client.get(f"/knowledge/citations/{citation_id}",headers={"X-Chat-Session":"A"*64})
            assert response.status_code==200,response.text
            details=response.json()
            assert details["derived"] and details["analysis_version"]==str(analysis_id) and details["version_no"]==1
            assert details["model"]=="acceptance-fake-provider" and any("quarterly revenue of $6.8 billion" in quote for quote in details["original_quotes"])
        with Session(engine) as session,session.begin():
            version=session.get(MaterialAnalysisVersion,analysis_id);version.payload={**version.payload,"summary":"Tampered current analysis payload."}
        invalidated=_restore_citations(citation_sidecar,"A"*64)
        assert invalidated and invalidated[0]["source_type"]=="unavailable"
        with TestClient(app) as client:
            assert client.get(f"/knowledge/citations/{citation_id}",headers={"X-Chat-Session":"A"*64}).status_code==404
    finally:
        runtime_module._runtime=previous_runtime
        if runtime:
            runtime.close()
        elif index:
            index.close()
        if doc_id:
            with Session(engine) as session,session.begin():
                session.query(KnowledgeIndexTask).filter(KnowledgeIndexTask.chunk_id.in_(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id==doc_id))).delete(synchronize_session=False)
                session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id==doc_id))
                session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id==doc_id))
        if analysis_id:
            with Session(engine) as session,session.begin():
                session.execute(update(MaterialAnalysisJob).where(MaterialAnalysisJob.result_analysis_id==analysis_id).values(result_analysis_id=None))
                session.execute(delete(MaterialAnalysisVersion).where(MaterialAnalysisVersion.id==analysis_id))
                session.execute(delete(MaterialAnalysisJob).where(MaterialAnalysisJob.source_id==source_id))
                if evidence_id:session.execute(delete(EvidenceEventVersionV2).where(EvidenceEventVersionV2.id==evidence_id))
        with Session(engine) as session,session.begin():session.execute(delete(SecFilingInventory).where(SecFilingInventory.id==source_id))
        if index and runtime is None:index.close()
