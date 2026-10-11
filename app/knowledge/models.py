from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base

class KnowledgeDocument(Base):
    __tablename__='knowledge_documents'
    __table_args__=(UniqueConstraint('source_table','source_id','source_version','source_hash',name='uq_knowledge_document_source_version'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    source_table: Mapped[str]=mapped_column(String(64),nullable=False)
    source_id: Mapped[str]=mapped_column(String(128),nullable=False)
    source_version: Mapped[str]=mapped_column(String(128),nullable=False)
    source_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    snapshot_integrity: Mapped[str|None]=mapped_column(String(64),nullable=True)
    source_type: Mapped[str]=mapped_column(String(32),nullable=False)
    symbol: Mapped[str|None]=mapped_column(String(16),nullable=True,index=True)
    scope: Mapped[str]=mapped_column(String(32),nullable=False)
    visibility: Mapped[str]=mapped_column(String(16),nullable=False)
    owner_hash: Mapped[str|None]=mapped_column(String(64),nullable=True,index=True)
    title: Mapped[str]=mapped_column(String(512),nullable=False)
    source_url: Mapped[str|None]=mapped_column(String(2048),nullable=True)
    published_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    observed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    content_observed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    available_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True,index=True)
    raw_length: Mapped[int]=mapped_column(Integer,nullable=False)
    truncated: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    parser: Mapped[str]=mapped_column(String(64),nullable=False)
    source_text: Mapped[str]=mapped_column(Text,nullable=False)
    active: Mapped[bool]=mapped_column(Boolean,nullable=False,default=True,index=True)
    status: Mapped[str]=mapped_column(String(24),nullable=False,default='pending')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class KnowledgeChunk(Base):
    __tablename__='knowledge_chunks'
    __table_args__=(UniqueConstraint('document_id','generation','ordinal',name='uq_knowledge_chunk_document_generation_ordinal'),
      CheckConstraint("status IN ('pending','indexing','failed','ready','needs_review','superseded','withdrawn')",name='ck_knowledge_chunk_status'),
      Index('ix_knowledge_chunk_scope_symbol_type','scope','symbol','source_type'))
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True)
    document_id: Mapped[uuid.UUID]=mapped_column(Uuid,ForeignKey('knowledge_documents.id',ondelete='RESTRICT'),nullable=False,index=True)
    generation: Mapped[str]=mapped_column(String(64),nullable=False,index=True)
    ordinal: Mapped[int]=mapped_column(Integer,nullable=False)
    start_offset: Mapped[int]=mapped_column(Integer,nullable=False)
    end_offset: Mapped[int]=mapped_column(Integer,nullable=False)
    source_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    scope: Mapped[str]=mapped_column(String(32),nullable=False,index=True)
    visibility: Mapped[str]=mapped_column(String(16),nullable=False)
    owner_hash: Mapped[str|None]=mapped_column(String(64),nullable=True,index=True)
    symbol: Mapped[str|None]=mapped_column(String(16),nullable=True,index=True)
    source_type: Mapped[str]=mapped_column(String(32),nullable=False)
    available_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True,index=True)
    content_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    category: Mapped[str]=mapped_column(Text,nullable=False)
    questions: Mapped[str]=mapped_column(Text,nullable=False)
    answer: Mapped[str]=mapped_column(Text,nullable=False)
    embedding_text: Mapped[str]=mapped_column(Text,nullable=False)
    section_path: Mapped[str]=mapped_column(Text,nullable=False,default='')
    content_type: Mapped[str]=mapped_column(String(24),nullable=False,default='text')
    previous_chunk_id: Mapped[uuid.UUID|None]=mapped_column(Uuid,nullable=True)
    next_chunk_id: Mapped[uuid.UUID|None]=mapped_column(Uuid,nullable=True)
    is_key_clause: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    needs_review: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    status: Mapped[str]=mapped_column(String(24),nullable=False,default='pending',index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class KnowledgeIndexTask(Base):
    __tablename__='knowledge_index_tasks'
    __table_args__=(UniqueConstraint('chunk_id','generation',name='uq_knowledge_task_chunk_generation'),
      CheckConstraint("status IN ('pending','leased','complete','failed')",name='ck_knowledge_task_status'))
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID]=mapped_column(Uuid,ForeignKey('knowledge_chunks.id',ondelete='RESTRICT'),nullable=False,index=True)
    generation: Mapped[str]=mapped_column(String(64),nullable=False)
    status: Mapped[str]=mapped_column(String(16),nullable=False,default='pending',index=True)
    lease_epoch: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    lease_owner: Mapped[str|None]=mapped_column(String(64),nullable=True)
    lease_until: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True,index=True)
    next_attempt_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True,index=True)
    attempts: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    error_code: Mapped[str|None]=mapped_column(String(64),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now(),nullable=False)

class KnowledgeCandidate(Base):
    __tablename__='knowledge_candidates'
    __table_args__=(UniqueConstraint('dedupe_key',name='uq_knowledge_candidate_dedupe'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    dedupe_key: Mapped[str]=mapped_column(String(64),nullable=False)
    scope: Mapped[str]=mapped_column(String(32),nullable=False)
    owner_hash: Mapped[str|None]=mapped_column(String(64),nullable=True)
    conversation_id: Mapped[str]=mapped_column(String(64),nullable=False)
    turn_id: Mapped[str]=mapped_column(String(64),nullable=False)
    evidence_ref: Mapped[str]=mapped_column(String(128),nullable=False)
    evidence_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    question: Mapped[str]=mapped_column(Text,nullable=False)
    answer: Mapped[str]=mapped_column(Text,nullable=False)
    category: Mapped[str]=mapped_column(String(80),nullable=False)
    candidate_kind: Mapped[str]=mapped_column(String(32),nullable=False,default='other')
    evidence_quote: Mapped[str]=mapped_column(Text,nullable=False)
    valid_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    status: Mapped[str]=mapped_column(String(24),nullable=False,default='pending_review')
    reviewer: Mapped[str|None]=mapped_column(String(120),nullable=True)
    reviewed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class KnowledgeExtractionRun(Base):
    __tablename__='knowledge_extraction_runs'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    scope_key: Mapped[str]=mapped_column(String(64),nullable=False,default='default',server_default='default',index=True)
    cursor: Mapped[str|None]=mapped_column(String(128),nullable=True)
    batch_status: Mapped[str]=mapped_column(String(24),nullable=False,default='pending')
    processed_count: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    error_code: Mapped[str|None]=mapped_column(String(64),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
    completed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)

class KnowledgeUploadOwner(Base):
    __tablename__='knowledge_upload_owners'
    __table_args__=(UniqueConstraint('upload_id',name='uq_knowledge_upload_owner_upload'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    upload_id: Mapped[uuid.UUID]=mapped_column(Uuid,nullable=False)
    owner_hash: Mapped[str]=mapped_column(String(64),nullable=False,index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class LowConfidenceQuestion(Base):
    __tablename__='low_confidence_questions'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    question: Mapped[str]=mapped_column(Text,nullable=False)
    conversation_id: Mapped[str]=mapped_column(String(64),nullable=False,index=True)
    turn_id: Mapped[str]=mapped_column(String(64),nullable=False,index=True)
    entry_point: Mapped[str]=mapped_column(String(32),nullable=False)
    reason: Mapped[str]=mapped_column(String(80),nullable=False)
    owner_hash: Mapped[str]=mapped_column(String(64),nullable=False,index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
