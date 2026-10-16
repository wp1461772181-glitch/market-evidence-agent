"""Independent acceptance: every saved material must survive complete sync cycles."""
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeExtractionRun, KnowledgeIndexTask
from app.models import SecFilingInventory
from scripts.migrate_knowledge import migrate


class Tokenizer:
    def encode(self, text, **kwargs):
        return text.split()


def test_sec_sync_cycles_cover_both_sides_of_the_initial_source_and_updates(disposable_database):
    from app.knowledge.source_sync import sync_source_batch

    migrate(engine)
    Base.metadata.create_all(engine, tables=[SecFilingInventory.__table__])
    ids = [UUID("00000000-0000-4000-8000-000000000001"),
           UUID("9af2ef08-e7e6-4e2d-bf47-68d1a163957c"),
           UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")]
    generation = "p" * 64
    now = datetime.now(UTC) - timedelta(minutes=1)
    try:
        with Session(engine) as session, session.begin():
            state = session.scalar(select(KnowledgeExtractionRun).where(
                KnowledgeExtractionRun.scope_key == "material_sync.sec"))
            if state:
                state.cursor = None
            for ordinal, identity in enumerate(ids):
                body = f"Item 3.02 Material acceptance source {ordinal}. Value {ordinal + 1}."
                session.add(SecFilingInventory(id=identity, symbol="AMD", cik="0099999999",
                    accession_number=f"9999999999-26-00000{ordinal}", form="8-K", filed_at=now.date(),
                    primary_document=f"acceptance-{ordinal}.htm", source_url=f"https://example.test/acceptance-{ordinal}",
                    source="declared acceptance fixture", review_status="pending_review", observed_at=now,
                    content_status="fetched", content_observed_at=now, content_excerpt=body,
                    content_excerpt_sha256=hashlib.sha256(body.encode()).hexdigest()))
        # Passing the end of the keyset and starting again must cover IDs below
        # the first bootstrapped source, not keep replaying only its suffix.
        for _ in range(40):
            sync_source_batch(lambda: Session(engine), Tokenizer(), generation, source_type="sec", batch_size=1)
        with Session(engine) as session:
            found = set(session.scalars(select(KnowledgeDocument.source_id).where(
                KnowledgeDocument.source_table == "sec_filing_inventory", KnowledgeDocument.source_id.in_([str(i) for i in ids]))))
            assert found == {str(i) for i in ids}, "a complete source cycle must cover every saved eligible source"
            row = session.get(SecFilingInventory, ids[0])
            row.content_excerpt = "Item 3.02 Updated material. Value 17."
            row.content_excerpt_sha256 = hashlib.sha256(row.content_excerpt.encode()).hexdigest()
            session.commit()
        for _ in range(40):
            sync_source_batch(lambda: Session(engine), Tokenizer(), generation, source_type="sec", batch_size=1)
        with Session(engine) as session:
            current = session.scalars(select(KnowledgeDocument).where(
                KnowledgeDocument.source_id == str(ids[0]), KnowledgeDocument.active.is_(True))).all()
            assert len(current) == 1 and "Value 17" in current[0].source_text
    finally:
        # Test-only records; never operate on a developer database.
        assert engine.url.database.startswith("test_")
        with Session(engine) as session, session.begin():
            doc_ids = list(session.scalars(select(KnowledgeDocument.id).where(
                KnowledgeDocument.source_table == "sec_filing_inventory", KnowledgeDocument.source_id.in_([str(i) for i in ids]))))
            chunk_ids = list(session.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id.in_(doc_ids))))
            session.execute(delete(KnowledgeIndexTask).where(KnowledgeIndexTask.chunk_id.in_(chunk_ids)))
            session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id.in_(chunk_ids)))
            session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(doc_ids)))
            session.execute(delete(SecFilingInventory).where(SecFilingInventory.id.in_(ids)))
            session.execute(delete(KnowledgeExtractionRun).where(KnowledgeExtractionRun.scope_key == "material_sync.sec"))


def test_source_sync_waits_when_runtime_is_unavailable(monkeypatch):
    import time
    from app.knowledge import runtime, source_sync

    calls = []
    monkeypatch.setattr(runtime, "get_runtime", lambda: calls.append(True))
    source_sync.start_source_sync(lambda: Session(engine), interval_seconds=5)
    try:
        time.sleep(0.05)
    finally:
        source_sync.stop_source_sync()
        source_sync._thread.join(timeout=1)
    assert len(calls) <= 2, "an unavailable model must wait, not spin on a background CPU loop"


def test_indexing_disabled_still_advances_all_material_sources(monkeypatch):
    from types import SimpleNamespace
    from app.knowledge import source_sync

    seen=[]
    monkeypatch.setenv("RAG_INDEXING_ENABLED", "false")
    monkeypatch.setattr(source_sync, "sync_source_batch", lambda _factory, _tokenizer, _generation, *, source_type, batch_size:
        seen.append((source_type, batch_size)) or {"scanned": 5})
    runtime=SimpleNamespace(settings=SimpleNamespace(generation="g"*64),
        executor=SimpleNamespace(run_background_sync=lambda *_: (_ for _ in ()).throw(AssertionError("indexing must be disabled"))),
        embedder=object(),index=object())
    source_sync.sync_sources_once(lambda:None,runtime,object())
    assert seen == [("faq",5),("sec",5),("upload",5),("analysis",5),("web",5)]


def test_recovery_cannot_start_a_new_runtime_after_shutdown(monkeypatch):
    import threading
    from app.knowledge import runtime

    entered = threading.Event()
    resume = threading.Event()
    starts = []
    def unavailable():
        entered.set()
        resume.wait(timeout=1)
        return None
    monkeypatch.setattr(runtime, "get_runtime", unavailable)
    monkeypatch.setattr(runtime, "start_runtime", lambda: starts.append(True))
    monkeypatch.setattr(runtime, "_runtime", None)
    monkeypatch.setattr(runtime, "_recovery_thread", None)
    runtime.start_runtime_background(retry_seconds=1)
    try:
        assert entered.wait(timeout=1)
        runtime.stop_runtime()
        resume.set()
        runtime._recovery_thread.join(timeout=1)
        assert not starts, "shutdown must prevent a recovery already waiting to initialize"
    finally:
        resume.set()
        runtime.stop_runtime()


def test_recovery_initializer_serializes_settings_window_with_shutdown(monkeypatch):
    """A stop racing after the recovery check must wait for init, then close it."""
    import threading
    from app.knowledge import runtime

    entered = threading.Event()
    resume = threading.Event()
    stop_returned = threading.Event()
    starts = []

    class FakeRuntime:
        ready = True
        def close(self):
            self.ready = False
            starts.append('closed')

    def from_env():
        entered.set()
        assert resume.wait(timeout=2)
        return object()

    def pending_start():
        runtime.KnowledgeSettings.from_env()
        with runtime._runtime_lock:
            runtime._runtime = FakeRuntime()
            starts.append('started')

    monkeypatch.setattr(runtime.KnowledgeSettings, 'from_env', from_env)
    monkeypatch.setattr(runtime, 'start_runtime', pending_start)
    monkeypatch.setattr(runtime, 'get_runtime', lambda: None)
    monkeypatch.setattr(runtime, '_runtime', None)
    monkeypatch.setattr(runtime, '_recovery_thread', None)
    runtime._recovery_stop.clear()
    runtime.start_runtime_background(retry_seconds=1)
    try:
        assert entered.wait(timeout=1), 'recovery should reach settings load after its stop check'
        stopper = threading.Thread(target=lambda: (runtime.stop_runtime(), stop_returned.set()))
        stopper.start()
        # The initializer owns the lifecycle lock while settings are loaded.
        assert not stop_returned.wait(timeout=0.05)
        resume.set()
        stopper.join(timeout=2)
        runtime._recovery_thread.join(timeout=2)
        assert stop_returned.is_set()
        assert starts == ['started', 'closed']
        assert runtime._runtime is None
    finally:
        resume.set()
        runtime.stop_runtime()
