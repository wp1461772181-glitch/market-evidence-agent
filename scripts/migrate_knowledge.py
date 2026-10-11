import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import engine
from importlib import import_module
models = import_module('app.knowledge.models')
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
TABLES=[models.KnowledgeDocument.__table__,models.KnowledgeChunk.__table__,models.KnowledgeIndexTask.__table__,
        models.KnowledgeCandidate.__table__,models.KnowledgeExtractionRun.__table__,models.KnowledgeUploadOwner.__table__,
        models.LowConfidenceQuestion.__table__]
def migrate(bind=engine):
    models.Base.metadata.create_all(bind=bind,tables=TABLES,checkfirst=True)
    # Additive compatibility for databases created before block-navigation metadata.
    columns={column['name'] for column in inspect(bind).get_columns('knowledge_chunks')} if inspect(bind).has_table('knowledge_chunks') else set()
    additions={'previous_chunk_id':'UUID NULL','next_chunk_id':'UUID NULL','is_key_clause':'BOOLEAN NOT NULL DEFAULT FALSE'}
    with bind.begin() as connection:
        document_columns={column['name'] for column in inspect(bind).get_columns('knowledge_documents')} if inspect(bind).has_table('knowledge_documents') else set()
        if 'snapshot_integrity' not in document_columns:connection.execute(text('ALTER TABLE knowledge_documents ADD COLUMN snapshot_integrity VARCHAR(64) NULL'))
        for name,definition in additions.items():
            if name not in columns:connection.execute(text(f'ALTER TABLE knowledge_chunks ADD COLUMN {name} {definition}'))
        task_columns={column['name'] for column in inspect(bind).get_columns('knowledge_index_tasks')} if inspect(bind).has_table('knowledge_index_tasks') else set()
        if 'next_attempt_at' not in task_columns:connection.execute(text('ALTER TABLE knowledge_index_tasks ADD COLUMN next_attempt_at TIMESTAMPTZ NULL'))
        run_columns={column['name'] for column in inspect(bind).get_columns('knowledge_extraction_runs')} if inspect(bind).has_table('knowledge_extraction_runs') else set()
        if 'scope_key' not in run_columns:connection.execute(text("ALTER TABLE knowledge_extraction_runs ADD COLUMN scope_key VARCHAR(64) NOT NULL DEFAULT 'default'"))
    # Explicit additive migration also repairs historical nullable FAQ times.
    # It uses their original document creation time and is safe to rerun.
    from app.knowledge.repository import repair_faq_availability
    with Session(bind) as session, session.begin():
        repair_faq_availability(session)
if __name__=='__main__': migrate()
