from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from app.database import Base, SessionLocal, engine
from app.event_provider import ProviderResult
from app.material_analysis import MaterialAnalysisError, list_materials, request_material_analysis, run_material_analysis_once
from app.forecast_v2_models import EvidenceEventVersionV2
from app.material_analysis_models import MaterialAnalysisJob, MaterialAnalysisVersion
from app.material_analysis_schema import locate_material_analysis_citations, validate_material_analysis
from app.models import SecFilingInventory, UploadedEvidence


@pytest.fixture(autouse=True)
def material_tables(disposable_database):
    Base.metadata.create_all(bind=engine)
    yield
    with SessionLocal() as db:
        jobs = list(db.scalars(select(MaterialAnalysisJob)))
        source_refs = {(job.source_type, job.source_id) for job in jobs}
        evidence_ids = [job.evidence_version_id for job in jobs if job.evidence_version_id]
        job_ids = [job.id for job in jobs]
        if job_ids:
            db.execute(update(MaterialAnalysisJob).where(MaterialAnalysisJob.id.in_(job_ids)).values(result_analysis_id=None))
        db.execute(delete(MaterialAnalysisVersion).where(MaterialAnalysisVersion.job_id.in_([job.id for job in jobs])))
        db.execute(delete(MaterialAnalysisJob).where(MaterialAnalysisJob.id.in_(job_ids)))
        if evidence_ids:
            db.execute(delete(EvidenceEventVersionV2).where(EvidenceEventVersionV2.id.in_(evidence_ids)))
        for source_type, source_id in source_refs:
            if source_type == "uploaded_media":
                db.execute(delete(UploadedEvidence).where(UploadedEvidence.id == source_id))
            elif source_type == "official_filing":
                db.execute(delete(SecFilingInventory).where(SecFilingInventory.id == source_id))
        db.commit()


def _payload(*, coordinate: bool = False) -> dict:
    citation = {"quote": "Revenue grew by 8%"}
    if coordinate:
        citation.update(start_char=0, end_char=18)
    return {
        "schema_version": "material-analysis-v1", "prompt_version": "material-analysis-prompt-v1",
        "summary": "本季度收入增长。", "facts": [{"id": "f1", "statement": "收入增长。", "citations": [citation]}],
        "supporting": [], "counter": [], "uncertainties": [], "key_numbers": [],
    }


class FakeProvider:
    def __init__(self, content: str | None = None, before_return=None):
        self.content = content or json.dumps(_payload(), ensure_ascii=False)
        self.before_return = before_return
        self.calls = 0

    def extract(self, **kwargs):
        self.calls += 1
        if self.before_return:
            self.before_return()
        return ProviderResult(content=self.content, response_model="deepseek-test", usage={"total_tokens": 17})


def _upload(symbol="AAPL", text_value="Revenue grew by 8% this quarter."):
    raw = text_value.encode()
    with SessionLocal() as db:
        existing = db.scalar(select(UploadedEvidence).where(
            UploadedEvidence.symbol == symbol,
            UploadedEvidence.content_sha256 == hashlib.sha256(raw).hexdigest(),
        ))
        if existing is not None:
            return existing.id
        row = UploadedEvidence(
            symbol=symbol, title="Quarterly update", source_url="https://example.test/report",
            published_at=datetime(2026, 9, 10, tzinfo=UTC), observed_at=datetime(2026, 9, 10, 1, tzinfo=UTC),
            credibility_stars=4, credibility_reason="fixture", impact_severity="medium", filename="report.txt",
            content_sha256=hashlib.sha256(raw).hexdigest(), raw_content=raw, content_text=text_value, status="unconfirmed",
        )
        db.add(row)
        db.commit()
        return row.id


def _request(source_id, *, key=None, force=False):
    with SessionLocal() as db:
        return request_material_analysis(db=db, source_type="uploaded_media", source_id=source_id,
                                         idempotency_key=key or str(uuid4()), force=force)


def test_schema_rejects_out_of_bounds_quotes_and_unknown_fact_ids_but_allows_empty_sides():
    with pytest.raises(ValueError, match="outside"):
        validate_material_analysis(_payload(coordinate=True) | {"facts": [{"id": "f1", "statement": "x", "citations": [{"quote": "x", "start_char": 100, "end_char": 101}]}]}, "short")
    invalid_link = locate_material_analysis_citations(_payload(), "Revenue grew by 8% this quarter.")
    invalid_link["supporting"] = [{"id": "s1", "statement": "x", "rationale": "r", "fact_ids": ["missing"], "citations": [{"quote": "Revenue grew by 8%", "start_char": 0, "end_char": 18}]}]
    with pytest.raises(ValueError, match="unknown fact_id"):
        validate_material_analysis(invalid_link, "Revenue grew by 8% this quarter.")
    wrong_quote = locate_material_analysis_citations(_payload(), "Revenue grew by 8% this quarter.")
    wrong_quote["facts"][0]["citations"][0]["quote"] = "Revenue fell by 8%"
    with pytest.raises(ValueError, match="does not match"):
        validate_material_analysis(wrong_quote, "Revenue grew by 8% this quarter.")
    valid = locate_material_analysis_citations(_payload(), "Revenue grew by 8% this quarter.")
    assert validate_material_analysis(valid, "Revenue grew by 8% this quarter.").supporting == []


def test_saved_analysis_adapter_accepts_owner_mapped_pdf_raw_bytes_distinct_from_parsed_text():
    from app.knowledge.analysis_sources import load_analysis_document
    from app.knowledge.models import KnowledgeUploadOwner,KnowledgeDocument,KnowledgeChunk,KnowledgeIndexTask
    from app.knowledge.repository import ingest_documents,document_integrity
    from app.knowledge.settings import KnowledgeSettings
    from app.agent_chat_store import owner_hash
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    raw=b"%PDF-1.7\x00binary fixture bytes, deliberately unlike extracted text"
    parsed="Revenue grew by 8% this quarter."
    source_id=_upload("PDFX",parsed)
    credential="C"*40
    with SessionLocal() as db,db.begin():
        upload=db.get(UploadedEvidence,source_id);upload.raw_content=raw;upload.content_sha256=hashlib.sha256(raw).hexdigest()
        db.add(KnowledgeUploadOwner(upload_id=source_id,owner_hash=owner_hash(credential)))
    result=_request(source_id,key="pdf-source-analysis")
    assert run_material_analysis_once(session_factory=SessionLocal,provider=FakeProvider(),job_id=UUID(result["job_id"]))["status"]=="succeeded"
    analysis_id=UUID(first_result_id(source_id))
    with SessionLocal() as db:
        doc=load_analysis_document(db,analysis_id)
        assert doc is not None and doc.scope=="private" and doc.owner_hash==owner_hash(credential)
        original_hash=doc.source_hash
    class Tok:
        def encode(self,text,**kwargs): return text.split()
    settings=KnowledgeSettings.from_env()
    with SessionLocal() as db,db.begin():
        ingest_documents(db,[doc],Tok(),settings.generation)
        saved=db.query(KnowledgeDocument).filter_by(source_version=str(analysis_id)).one();saved.status="active";saved.snapshot_integrity=document_integrity(saved)
        chunks=db.query(KnowledgeChunk).filter_by(document_id=saved.id).all()
        for chunk in chunks:chunk.status="ready"
        chunk_id=next(chunk.id for chunk in chunks if "原文引文：Revenue grew by 8%" in chunk.answer)
    app=FastAPI();from app.knowledge.api import router;app.include_router(router)
    with TestClient(app) as client:
        citation=client.get(f"/knowledge/citations/{chunk_id}",headers={"X-Chat-Session":credential})
        assert citation.status_code==200,citation.text
        assert citation.json()["derived"] is True and citation.json()["version_no"]==1
        assert citation.json()["original_source_type"]=="uploaded_media" and "Revenue grew by 8%" in citation.json()["original_quotes"][0]
    forced=_request(source_id,key="pdf-source-analysis-v2",force=True)
    assert run_material_analysis_once(session_factory=SessionLocal,provider=FakeProvider(),job_id=UUID(forced["job_id"]))["status"]=="succeeded"
    with SessionLocal() as db:
        newest=db.scalar(select(MaterialAnalysisVersion).where(MaterialAnalysisVersion.source_id==source_id).order_by(MaterialAnalysisVersion.version_no.desc()).limit(1))
        assert newest and newest.version_no==2
        newest_doc=load_analysis_document(db,newest.id)
        assert newest_doc is not None and load_analysis_document(db,analysis_id) is None
        latest_id=newest.id;latest_hash=newest_doc.source_hash
    from app.knowledge.source_sync import sync_source_batch
    with SessionLocal() as db:
        cycle=sync_source_batch(lambda:SessionLocal(),Tok(),settings.generation,source_type="analysis",batch_size=5)
    assert cycle["accepted"]==1 and cycle["documents"]==1
    with SessionLocal() as db:
        active=db.query(KnowledgeDocument).filter_by(source_id=f"uploaded_media:{source_id}",active=True).one()
        assert active.source_version==str(latest_id)
        assert load_analysis_document(db,analysis_id) is None
    sync_source_batch(lambda:SessionLocal(),Tok(),settings.generation,source_type="analysis",batch_size=5)
    replay=sync_source_batch(lambda:SessionLocal(),Tok(),settings.generation,source_type="analysis",batch_size=5)
    assert replay["skipped"]==1
    with SessionLocal() as db,db.begin():
        active=db.query(KnowledgeDocument).filter_by(source_id=f"uploaded_media:{source_id}",active=True).one()
        active.status="active";active.snapshot_integrity=document_integrity(active)
        current_chunks=db.query(KnowledgeChunk).filter_by(document_id=active.id).all()
        for chunk in current_chunks:chunk.status="ready"
        latest_chunk=next(chunk for chunk in current_chunks if "原文引文：Revenue grew by 8%" in chunk.answer)
        latest_chunk_id=latest_chunk.id
        citation_sidecar=[{"ordinal":1,"chunk_id":str(latest_chunk.id),"document_id":str(active.id),
            "source_hash":active.source_hash,"content_hash":latest_chunk.content_hash,"generation":latest_chunk.generation,
            "title":active.title[:180],"section":latest_chunk.section_path[:180],"source_url":active.source_url,
            "analysis_version":active.source_version,"derived":True,"source_type":active.source_type,"truncated":False}]
    with TestClient(app) as client:
        current_citation=client.get(f"/knowledge/citations/{latest_chunk_id}",headers={"X-Chat-Session":credential})
        assert current_citation.status_code==200 and current_citation.json()["version_no"]==2
    # A current publication-date edit must invalidate old analysis citations,
    # even when parsed/raw content, frozen evidence, and observations are unchanged.
    from app.agent_chat_stream import _restore_citations
    old_publication_cutoff=datetime.now(UTC)-timedelta(seconds=10)
    with SessionLocal() as db,db.begin():
        upload=db.get(UploadedEvidence,source_id)
        before_publication=upload.published_at
        upload.published_at=old_publication_cutoff+timedelta(seconds=1)
    with SessionLocal() as db:
        published_changed=load_analysis_document(db,latest_id)
        assert published_changed is not None
        assert published_changed.source_hash!=latest_hash
        assert published_changed.published_at==old_publication_cutoff+timedelta(seconds=1)
        assert published_changed.available_at>=published_changed.published_at
        assert load_analysis_document(db,latest_id,cutoff=old_publication_cutoff) is None
    assert _restore_citations(citation_sidecar,credential)[0]["source_type"]=="unavailable"
    with TestClient(app) as client:
        assert client.get(f"/knowledge/citations/{latest_chunk_id}",headers={"X-Chat-Session":credential}).status_code==404
    with SessionLocal() as db,db.begin():
        db.get(UploadedEvidence,source_id).published_at=before_publication
    with TestClient(app) as client:
        restored_citation=client.get(f"/knowledge/citations/{latest_chunk_id}",headers={"X-Chat-Session":credential})
        assert restored_citation.status_code==200 and restored_citation.json()["version_no"]==2
    with SessionLocal() as db,db.begin():
        db.delete(db.scalar(select(KnowledgeUploadOwner).where(KnowledgeUploadOwner.upload_id==source_id)))
    with SessionLocal() as db: assert load_analysis_document(db,latest_id) is None
    with TestClient(app) as client:
        assert client.get(f"/knowledge/citations/{latest_chunk_id}",headers={"X-Chat-Session":credential}).status_code==404
    with SessionLocal() as db,db.begin(): db.add(KnowledgeUploadOwner(upload_id=source_id,owner_hash=owner_hash(credential)))
    with TestClient(app) as client:
        assert client.get(f"/knowledge/citations/{latest_chunk_id}",headers={"X-Chat-Session":credential}).status_code==200
    with TestClient(app) as client:
        assert client.get(f"/knowledge/citations/{chunk_id}",headers={"X-Chat-Session":credential}).status_code==404
    with SessionLocal() as db,db.begin():
        upload=db.get(UploadedEvidence,source_id);upload.observed_at=datetime(2026,9,1,tzinfo=UTC)
    with SessionLocal() as db:
        changed=load_analysis_document(db,latest_id)
        assert changed is not None and changed.source_hash!=latest_hash
    with SessionLocal() as db,db.begin():
        version=db.get(MaterialAnalysisVersion,latest_id);version.payload={**version.payload,"summary":"Tampered saved analysis summary."}
    with SessionLocal() as db:
        payload_changed=load_analysis_document(db,latest_id)
        assert payload_changed is not None and payload_changed.source_hash!=changed.source_hash
    with TestClient(app) as client:
        stale=client.get(f"/knowledge/citations/{latest_chunk_id}",headers={"X-Chat-Session":credential})
        assert stale.status_code==404
    with SessionLocal() as db,db.begin():
        db.delete(db.scalar(select(KnowledgeUploadOwner).where(KnowledgeUploadOwner.upload_id==source_id)))
        upload=db.get(UploadedEvidence,source_id);upload.content_text="Changed parser output with original raw PDF hash."
    with SessionLocal() as db:
        assert load_analysis_document(db,analysis_id) is None
    with SessionLocal() as db,db.begin():
        version=db.get(MaterialAnalysisVersion,latest_id);version.input_fingerprint="a"*64
    with SessionLocal() as db: assert load_analysis_document(db,latest_id) is None
    with SessionLocal() as db,db.begin():
        doc_ids=select(KnowledgeDocument.id).where(KnowledgeDocument.source_id==f"uploaded_media:{source_id}")
        db.query(KnowledgeIndexTask).filter(KnowledgeIndexTask.chunk_id.in_(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id.in_(doc_ids)))).delete(synchronize_session=False)
        db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id.in_(doc_ids)).delete(synchronize_session=False)
        db.query(KnowledgeDocument).filter(KnowledgeDocument.source_id==f"uploaded_media:{source_id}").delete(synchronize_session=False)


def test_material_analysis_cache_force_new_version_failure_preserves_previous():
    source_id = _upload()
    first = _request(source_id, key="first")
    provider = FakeProvider()
    assert run_material_analysis_once(session_factory=SessionLocal, provider=provider, job_id=UUID(first["job_id"]))["status"] == "succeeded"
    assert provider.calls == 1

    cached = _request(source_id, key="cache")
    assert cached["cache_hit"] is True
    assert cached["analysis_id"] == first_result_id(source_id)

    forced = _request(source_id, key="force", force=True)
    bad = FakeProvider(content=json.dumps(_payload() | {"summary": "bad"}, ensure_ascii=False))
    bad.content = json.dumps(_payload() | {"facts": [{"id": "f1", "statement": "invalid", "citations": [{"quote": "not present"}]}]}, ensure_ascii=False)
    result = run_material_analysis_once(session_factory=SessionLocal, provider=bad, job_id=UUID(forced["job_id"]))
    assert result["status"] == "failed"
    with SessionLocal() as db:
        rows = list(db.scalars(select(MaterialAnalysisVersion).where(MaterialAnalysisVersion.source_id == source_id).order_by(MaterialAnalysisVersion.version_no)))
        failed_job = db.get(MaterialAnalysisJob, __import__("uuid").UUID(forced["job_id"]))
    assert len(rows) == 1
    assert failed_job.safe_error_code == "invalid_model_output"
    with SessionLocal() as db:
        item = next(item for item in list_materials(db=db, symbol="AAPL", source_type="uploaded_media", limit=50)["items"]
                    if item["source_id"] == str(source_id))
    assert item["analysis_status"] == "failed"
    assert item["latest_analysis_id"] == str(rows[0].id)
    replay = _request(source_id, key="force", force=True)
    assert replay["job_id"] == forced["job_id"]
    assert _request(source_id, key="after-failed-force")["analysis_id"] == str(rows[0].id)


def first_result_id(source_id):
    with SessionLocal() as db:
        return str(db.scalar(select(MaterialAnalysisVersion.id).where(MaterialAnalysisVersion.source_id == source_id)))


def test_analysis_identity_is_scoped_by_symbol_and_explicit_idempotency_conflict():
    text_value = "Revenue grew by 8% this quarter."
    first_id = _upload("AAPL", text_value)
    second_id = _upload("MSFT", text_value)
    first = _request(first_id, key="same-key")
    with pytest.raises(MaterialAnalysisError, match="idempotency_key"):
        _request(second_id, key="same-key")
    second = _request(second_id, key="different-key")
    with SessionLocal() as db:
        first_job = db.get(MaterialAnalysisJob, UUID(first["job_id"]))
        second_job = db.get(MaterialAnalysisJob, UUID(second["job_id"]))
    assert first_job.input_fingerprint != second_job.input_fingerprint


def test_concurrent_ordinary_jobs_reuse_the_first_published_analysis():
    source_id = _upload(text_value="AAPL revenue rose by 8%.")
    first = _request(source_id, key="parallel-1")
    second = _request(source_id, key="parallel-2")
    provider = FakeProvider(content=json.dumps(_payload() | {
        "facts": [{"id": "f1", "statement": "收入增长", "citations": [{"quote": "AAPL revenue rose by 8%"}]}]
    }, ensure_ascii=False))
    first_result = run_material_analysis_once(session_factory=SessionLocal, provider=provider, job_id=UUID(first["job_id"]))
    second_result = run_material_analysis_once(session_factory=SessionLocal, provider=provider, job_id=UUID(second["job_id"]))
    assert first_result["analysis_id"] == second_result["analysis_id"]
    assert second_result["cache_hit"] is True
    assert provider.calls == 1
    assert second["cache_hit"] is False


def test_expired_worker_lease_cannot_publish():
    source_id = _upload(text_value="Revenue grew by 8% this quarter. lease check")
    queued = _request(source_id, key="lease-check")

    def expire_and_reassign():
        with SessionLocal() as db:
            job = db.get(MaterialAnalysisJob, UUID(queued["job_id"]))
            job.lease_epoch += 1
            job.lease_owner = "replacement-worker"
            job.lease_expires_at = datetime.now(UTC) + datetime.resolution
            db.commit()

    provider = FakeProvider(before_return=expire_and_reassign)
    result = run_material_analysis_once(session_factory=SessionLocal, provider=provider, job_id=UUID(queued["job_id"]))
    assert result["status"] == "lease_lost"
    with SessionLocal() as db:
        versions = list(db.scalars(select(MaterialAnalysisVersion).where(MaterialAnalysisVersion.source_id == source_id)))
        job = db.get(MaterialAnalysisJob, UUID(queued["job_id"]))
    assert versions == []
    assert job.lease_owner == "replacement-worker"


def test_no_content_material_is_blocked_without_model_call():
    from app.models import SecFilingInventory

    with SessionLocal() as db:
        filing = SecFilingInventory(
            symbol="AAPL", cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1_000_000:06d}",
            form="10-Q", filed_at=datetime(2026, 9, 10, tzinfo=UTC).date(), accepted_at="2026-09-10T12:00:00Z",
            primary_document="q.htm", source_url="https://www.sec.gov/example/q.htm", source="sec-edgar",
            review_status="pending_review", human_review_note=None, reviewed_at=None,
            observed_at=datetime(2026, 9, 10, tzinfo=UTC), content_status="not_fetched", content_observed_at=None,
            content_excerpt=None, content_excerpt_sha256=None, content_truncated=False, content_error=None,
        )
        db.add(filing)
        db.commit()
        filing_id = filing.id
    with SessionLocal() as db:
        result = request_material_analysis(db=db, source_type="official_filing", source_id=filing_id,
                                           idempotency_key="no-content")
    assert result["status"] == "blocked_data"
    assert result["analysis_id"] is None


def test_worker_blocks_a_queued_job_after_its_analysis_contract_changes():
    source_id = _upload(text_value="Revenue grew by 8% this quarter. config version test")
    queued = _request(source_id, key="config-version")
    with SessionLocal() as db:
        job = db.get(MaterialAnalysisJob, UUID(queued["job_id"]))
        job.prompt_version = "material-analysis-prompt-obsolete"
        db.commit()

    provider = FakeProvider()
    result = run_material_analysis_once(session_factory=SessionLocal, provider=provider, job_id=UUID(queued["job_id"]))
    assert result["status"] == "blocked_data"
    assert provider.calls == 0
    with SessionLocal() as db:
        job = db.get(MaterialAnalysisJob, UUID(queued["job_id"]))
        versions = list(db.scalars(select(MaterialAnalysisVersion).where(MaterialAnalysisVersion.source_id == source_id)))
    assert job.safe_error_code == "config_version_changed"
    assert versions == []
