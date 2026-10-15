import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class AgentFAQ(Base):
    __tablename__ = "agent_faq"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)

class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class AgentMessage(Base):
    __tablename__ = "agent_messages"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    turn_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="complete")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentConversationTitle(Base):
    """A name the owner gave a conversation.  Without a row the first question names it; a separate
    table keeps the conversation table unchanged (it is created with the others at startup)."""
    __tablename__ = "agent_conversation_titles"
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agent_conversations.id", ondelete="CASCADE"), primary_key=True)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentConversationSummary(Base):
    """One segment of the oldest layer of a conversation's context: a summary of the turns whose
    messages lie between ``from_sequence`` and ``upto_sequence``.  Segments are only appended and
    never rewritten, so a fact passes through at most one lossy compression."""
    __tablename__ = "agent_conversation_summaries"
    __table_args__ = (UniqueConstraint("conversation_id", "seq", name="uq_agent_summary_conversation_seq"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    from_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    upto_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
