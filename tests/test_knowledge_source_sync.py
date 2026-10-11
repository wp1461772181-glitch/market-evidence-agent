from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.agent_chat_models import AgentFAQ
from app.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexTask, KnowledgeUploadOwner
from app.models import UploadedEvidence
from scripts.migrate_knowledge import migrate


class Tok:
    def encode(self, text, **kwargs): return text.split()


def test_bounded_material_sync_is_idempotent_and_keeps_private_owner_scope(disposable_database):
    migrate(engine)
    Base.metadata.create_all(engine, tables=[AgentFAQ.__table__, UploadedEvidence.__table__])
    now = datetime.now(UTC)
    mapped_id, unknown_id = uuid4(), uuid4()
    with Session(engine) as session, session.begin():
        session.add_all([
            AgentFAQ(id=991001, question="sync faq", answer="A saved FAQ.", category="product"),
            AgentFAQ(id=991002, question="sync faq two", answer="A second saved FAQ.", category="product"),
            AgentFAQ(id=991003, question="sync faq three", answer="A third saved FAQ.", category="product"),
            UploadedEvidence(id=mapped_id, symbol="AMD", title="Owner report", content_text="Private parsed PDF text.",
                raw_content=b"raw PDF bytes", content_sha256="a" * 64, source_url="https://example.test/private", published_at=now, observed_at=now,
                credibility_stars=3, credibility_reason="fixture", filename="private.pdf"),
            UploadedEvidence(id=unknown_id, symbol="AMD", title="Unmapped report", content_text="Must not publish.",
                raw_content=b"other bytes", content_sha256="b" * 64, source_url="https://example.test/unknown", published_at=now, observed_at=now,
                credibility_stars=3, credibility_reason="fixture", filename="unknown.pdf"),
            KnowledgeUploadOwner(upload_id=mapped_id, owner_hash="a" * 64),
        ])
    from app.knowledge.source_sync import sync_source_batch
    generation = "s" * 64
    for _ in range(3): sync_source_batch(lambda: Session(engine), Tok(), generation, source_type="faq", batch_size=10)
    sync_source_batch(lambda: Session(engine), Tok(), generation, source_type="upload", batch_size=10)
    with Session(engine) as session:
        faq_docs = session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.source_table == "agent_faq",
            KnowledgeDocument.source_id.in_(["991001","991002","991003"]))).all()
        upload_docs = session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.source_table == "uploaded_evidence",
            KnowledgeDocument.source_id.in_([str(mapped_id),str(unknown_id)])).order_by(KnowledgeDocument.source_id)).all()
        assert len(faq_docs) == 3
        assert len(upload_docs) == 1 and upload_docs[0].scope == "private" and upload_docs[0].owner_hash == "a" * 64
        assert upload_docs[0].source_text == "Private parsed PDF text."
        assert session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.source_id == str(unknown_id))) is None
    # The source page can be replayed after a cursor cycle without duplicate documents/tasks.
    sync_source_batch(lambda: Session(engine), Tok(), generation, source_type="faq", batch_size=10)
    sync_source_batch(lambda: Session(engine), Tok(), generation, source_type="upload", batch_size=10)
    with Session(engine) as session,session.begin():
        row=session.get(UploadedEvidence,mapped_id);row.title="Owner report updated";row.content_text="Updated private parsed PDF text."
        row.raw_content=b"updated raw PDF bytes";row.content_sha256="c"*64
    # One cycle reaches the end; the next bounded page sees the changed source snapshot.
    sync_source_batch(lambda: Session(engine), Tok(), generation, source_type="upload", batch_size=10)
    sync_source_batch(lambda: Session(engine), Tok(), generation, source_type="upload", batch_size=10)
    with Session(engine) as session:
        upload_versions=session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.source_id == str(mapped_id)).order_by(KnowledgeDocument.created_at)).all()
        assert len(upload_versions)==2 and upload_versions[0].active is False and upload_versions[1].active is True
        assert upload_versions[1].source_text=="Updated private parsed PDF text."
        assert session.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.source_id == str(unknown_id))) is None
        own_doc_ids=[row.id for row in upload_versions+faq_docs]
        own_chunk_ids=list(session.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id.in_(own_doc_ids))))
        assert session.query(KnowledgeIndexTask).filter(KnowledgeIndexTask.chunk_id.in_(own_chunk_ids)).count() == len(own_chunk_ids)
        session.query(KnowledgeIndexTask).filter(KnowledgeIndexTask.chunk_id.in_(own_chunk_ids)).delete(synchronize_session=False)
        session.query(KnowledgeChunk).filter(KnowledgeChunk.id.in_(own_chunk_ids)).delete(synchronize_session=False)
        session.query(KnowledgeDocument).filter(KnowledgeDocument.id.in_(own_doc_ids)).delete(synchronize_session=False)
        session.query(KnowledgeUploadOwner).delete(); session.query(UploadedEvidence).delete()
        session.query(AgentFAQ).filter(AgentFAQ.id.in_([991001, 991002, 991003])).delete(synchronize_session=False)
        session.query(__import__('app.knowledge.models', fromlist=['KnowledgeExtractionRun']).KnowledgeExtractionRun).delete()
        session.commit()
