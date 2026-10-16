"""Additive tables: consent capabilities and captured public web sources."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import DateTime, JSON, String, Text, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped,mapped_column
from ..database import Base

class AgentConsent(Base):
    __tablename__='agent_consents'
    id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    owner_hash:Mapped[str]=mapped_column(String(64),index=True)
    conversation_id:Mapped[str]=mapped_column(String(64),index=True)
    capability_hash:Mapped[str]=mapped_column(String(64),unique=True)
    payload_digest:Mapped[str]=mapped_column(String(64))
    operations:Mapped[list]=mapped_column(JSON)
    display:Mapped[dict]=mapped_column(JSON)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    status:Mapped[str]=mapped_column(String(24),default='proposed')
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class AgentWebSource(Base):
    __tablename__='agent_web_sources'
    __table_args__=(UniqueConstraint('owner_hash','url_hash',name='uq_agent_web_url'),UniqueConstraint('owner_hash','content_hash',name='uq_agent_web_body'))
    id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    owner_hash:Mapped[str]=mapped_column(String(64),index=True)
    symbol:Mapped[str|None]=mapped_column(String(16),nullable=True,index=True)
    company:Mapped[str|None]=mapped_column(String(160),nullable=True)
    source_url:Mapped[str]=mapped_column(String(2048))
    url_hash:Mapped[str]=mapped_column(String(64))
    content_hash:Mapped[str]=mapped_column(String(64))
    title:Mapped[str]=mapped_column(String(512))
    excerpt:Mapped[str]=mapped_column(Text)
    source_type:Mapped[str]=mapped_column(String(24),default='web_news')
    published_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    fetched_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    fetch_method:Mapped[str]=mapped_column(String(64))
