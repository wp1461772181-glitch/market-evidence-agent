"""Fixed real-AMD corpus gate; requires an explicit saved-source fixture path."""
import asyncio, hashlib, json, os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.agent_chat_models import AgentFAQ
from app.agent_faq_seed import seed_faq
from app.models import SecFilingInventory, UploadedEvidence
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.indexing import index_pending, InjectedIndexCrash
from app.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexTask, KnowledgeUploadOwner
from app.knowledge.repository import embedding_generation, ingest_documents, reconcile
from app.knowledge.runtime import KnowledgeRuntime, research_context
from app.knowledge.settings import KnowledgeSettings, MODEL_ID, MODEL_REVISION
from app.knowledge.sources import adapt_faq, adapt_sec, adapt_uploaded
from app.knowledge.vector_store import MilvusIndex
from scripts.migrate_knowledge import migrate

pytestmark = pytest.mark.skipif(os.getenv("KNOWLEDGE_AMD_REAL") != "1", reason="explicit AMD BGE/Milvus acceptance gate")
OWNER = hashlib.sha256(b"amd-rag-owner-fixture").hexdigest()


def test_fixed_amd_acceptance_real_model_and_upsert_resume(disposable_database, monkeypatch, tmp_path):
    source_path = Path(os.environ["KNOWLEDGE_AMD_SOURCE"])
    cases_path = Path(os.environ["KNOWLEDGE_AMD_CASES"])
    source = json.loads(source_path.read_text())
    cases = json.loads(cases_path.read_text())["cases"]
    body = source["content_excerpt"]
    assert len(body) == 3804 and hashlib.sha256(body.encode()).hexdigest() == source["content_excerpt_sha256"]
    assert body.index("Item 3.02") > 1200 and body.index("World Labs") > 1200
    Base.metadata.create_all(engine, tables=[AgentFAQ.__table__, SecFilingInventory.__table__, UploadedEvidence.__table__])
    migrate(engine)
    with engine.begin() as connection:
        tables=[name for name in ("knowledge_index_tasks","knowledge_chunks","knowledge_documents","knowledge_candidates","knowledge_extraction_runs","knowledge_upload_owners","agent_faq","uploaded_evidence","sec_filing_inventory") if name in __import__('sqlalchemy').inspect(connection).get_table_names()]
        if tables: connection.exec_driver_sql("TRUNCATE TABLE " + ", ".join('"'+name+'"' for name in tables) + " CASCADE")
    now=datetime.now(UTC)
    filed=date.fromisoformat(source["filed_at"])
    observed=datetime.fromisoformat(source["observed_at"].replace("Z","+00:00"))
    content_observed=datetime.fromisoformat(source["content_observed_at"].replace("Z","+00:00"))
    sec=SecFilingInventory(id=source["id"],symbol="AMD",cik="0000002488",accession_number=source["accession_number"],form=source["form"],
        filed_at=filed,accepted_at=None,primary_document=source["content_document_name"],source_url=source["source_url"],source="saved-source-fixture",
        review_status="pending_review",reviewed_at=None,observed_at=observed,content_status="fetched",content_observed_at=content_observed,
        content_excerpt=body,content_excerpt_sha256=source["content_excerpt_sha256"],content_truncated=source["content_truncated"],
        content_source_url=source["content_source_url"],content_document_name=source["content_document_name"],content_kind=source["content_kind"])
    owner_upload_id=uuid4()
    owner_upload=UploadedEvidence(id=owner_upload_id,symbol="AMD",title="Owner scoped parsed PDF fixture",source_url="https://example.test/owner-upload",
        published_at=now,observed_at=now,credibility_stars=3,credibility_reason="owned test fixture",impact_severity="low",
        filename="owner.pdf",content_sha256=hashlib.sha256(b"raw PDF bytes").hexdigest(),raw_content=b"raw PDF bytes",
        content_text="Owner-only parsed PDF text, isolated from the public AMD filing.",status="unconfirmed")
    with Session(engine) as session, session.begin():
        session.add_all([sec,owner_upload]);session.flush()
        session.add(KnowledgeUploadOwner(upload_id=owner_upload.id,owner_hash=OWNER))
        seed_faq(session)
        faq_rows=session.query(AgentFAQ).order_by(AgentFAQ.id).limit(3).all()
        docs=[adapt_sec({c.key:getattr(sec,c.key) for c in sec.__table__.columns}),
            adapt_uploaded({"id":owner_upload.id,"symbol":owner_upload.symbol,"title":owner_upload.title,"source_url":owner_upload.source_url,
                "published_at":owner_upload.published_at,"observed_at":owner_upload.observed_at,"content_sha256":owner_upload.content_sha256,
                "content_text":owner_upload.content_text},owner_hash=OWNER,allowlist=set())]
        docs.extend(adapt_faq({"id":row.id,"question":row.question,"answer":row.answer,"category":row.category}) for row in faq_rows)
    settings=KnowledgeSettings.from_env(validate_model=True)
    generation=embedding_generation(MODEL_ID,MODEL_REVISION,"amd-fixed-real-v1-"+uuid4().hex,"amd-fixed-real-v1")
    embedder=DenseEmbedder(settings.model_path,cpu_threads=2)
    with Session(engine) as session,session.begin():
        counts=ingest_documents(session,docs,embedder.tokenizer,generation)
    index=MilvusIndex(settings.milvus_uri,generation);index.ensure_collection(generation)
    # Simulate a real process interruption after Milvus accepted the upsert.
    with pytest.raises(InjectedIndexCrash): index_pending(lambda:Session(engine),embedder,index,generation,max_tasks=100,crash_at="after_upsert")
    with Session(engine) as session,session.begin():
        task=session.query(KnowledgeIndexTask).filter_by(status="leased").one();task.lease_until=datetime.now(UTC)
    resumed=index_pending(lambda:Session(engine),embedder,index,generation,max_tasks=100,worker_id="amd-resume")
    with Session(engine) as session:
        report=reconcile(session,index,generation)
        assert report["ok"] and session.query(KnowledgeIndexTask).filter(KnowledgeIndexTask.status!="complete").count()==0
        chunk_count=session.query(KnowledgeChunk).filter_by(status="ready",generation=generation).count()
    runtime_settings=KnowledgeSettings(True,settings.model_path,settings.revision,generation,settings.milvus_uri,2,1,1,settings.threshold_path,
        settings.reranker_path,settings.reranker_revision,"hybrid")
    runtime=KnowledgeRuntime(runtime_settings,embedder,index,0.0);runtime.warm()
    import app.knowledge.runtime as runtime_module
    previous=runtime_module._runtime;runtime_module._runtime=runtime
    from app.knowledge.retrieval import retrieve_faq,retrieve_materials
    outcomes=[]
    try:
        for case in cases:
            if case.get("corpus")=="faq":
                result=asyncio.run(retrieve_faq(case["query"]))
                text_value=" ".join(item.get("answer","") for item in result.get("data",[]))
            else:
                with research_context(OWNER if case["id"]!="wrong_symbol" else hashlib.sha256(b"other-owner").hexdigest()) as ctx:
                    result=asyncio.run(retrieve_materials(case.get("symbol","AMD"),case["query"],5))
                    citation_types=[row["source_type"] for row in ctx.citations or []]
                text_value=" ".join(item.get("excerpt","") for item in result.get("data",[]))
            expected=case.get("expected",[])
            matched=[term for term in expected if term.casefold() in text_value.casefold()]
            if case["id"]=="wrong_symbol":
                passed=not result.get("data") or all(row.get("id")!=source["id"] for row in result["data"])
            elif case.get("expected_refusal"):
                unsupported=case.get("unsupported",[])
                passed=not any(term.casefold() in text_value.casefold() for term in unsupported)
            else: passed=len(matched)==len(expected)
            outcomes.append({"id":case["id"],"passed":passed,"matched":matched,"result_count":len(result.get("data") or []),
                "source_types":citation_types if case.get("corpus")!="faq" else ["research_faq"]})
        with research_context(hashlib.sha256(b"different-amd-owner").hexdigest()):
            isolated=asyncio.run(retrieve_materials("AMD","owner-only parsed PDF text",5))
        owner_ids={str(owner_upload_id)}
        assert not any(str(row.get("id")) in owner_ids for row in isolated.get("data",[]))
        outcomes.append({"id":"private_owner_isolation","passed":True,"private_source_returned":False})
        result_file=Path(os.getenv("KNOWLEDGE_AMD_REPORT",str(tmp_path/"amd-rag-report.json")))
        result_file.write_text(json.dumps({"generation":generation,"collection":index.collection_name,"source_id":source["id"],
            "source_sha256":source["content_excerpt_sha256"],"source_characters":len(body),"bootstrapped_documents":counts["documents"],
            "ready_chunks":chunk_count,"resume":resumed,"reconcile":report,"cases":outcomes,
            "refusal_limit":"Retrieval evidence absence was evaluated; no answer-generating LLM was called."},indent=2)+"\n")
        assert all(row["passed"] for row in outcomes), outcomes
    finally:
        runtime_module._runtime=previous
        runtime.close()
