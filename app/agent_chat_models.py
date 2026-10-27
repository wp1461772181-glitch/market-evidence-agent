import uuid
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
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
    # Archiving is a logical delete: the conversation and its messages stay, it just stops being
    # listed.  NULL means not archived.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class AgentResearchQueue(Base):
    """The research queue (the stocks the owner follows) as the site owner keeps it, one row per owner
    credential hash, so that clearing the browser does not clear it.  A guest's queue stays in their
    browser.  A separate table keeps the existing ones unchanged (it is created with them at startup)."""
    __tablename__ = "agent_research_queue"
    owner_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)



class AgentProfileItem(Base):
    """One line of the owner's profile: a lasting preference, something about the owner, or a standing
    instruction.  Every row quotes the owner's own words (``quote``); text from pages, filings or tool
    results never becomes a row.  Rows are archived rather than deleted when a newer one replaces them;
    only the owner deleting a row on the memory page removes it."""
    __tablename__ = "agent_profile_items"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # A short slug (currency_unit, answer_language): a newer item of the same kind and topic replaces
    # the older one.  NULL: the item replaces nothing.
    topic: Mapped[str | None] = mapped_column(String(40), nullable=True)
    text: Mapped[str] = mapped_column(String(200), nullable=False)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    quote: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentConversationRecall(Base):
    """The owner's index of past conversations, read by ``recall_conversations`` and the memory page.
    Row ``seq`` 0 holds the owner's own questions; rows 1.. mirror the summary segments.  Every row
    carries the conversation's name.  It is memory, not evidence: nothing here is ever numbered or cited.
    Derived from the conversation tables, so ``agent_recall.backfill`` can rebuild it."""
    __tablename__ = "agent_conversation_recall"
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agent_conversations.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentTask(Base):
    """A scheduled job of the owner (agent_tasks).  ``kind`` brief: the agent answers ``instruction``
    unattended (read tools only) and the answer goes to the inbox; kind alerts: no model, the runner checks
    new filings, news leads and price lines (``config``) for the scope.  Schedule math is in agent_tasks."""
    __tablename__ = "agent_tasks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="brief")
    schedule_kind: Mapped[str] = mapped_column(String(12), nullable=False)      # daily | weekdays | hourly
    schedule_time: Mapped[str] = mapped_column(String(5), nullable=False)       # HH:MM (hourly: only MM counts)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)     # Phase 5
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="research_queue")  # research_queue | symbols
    symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    channel: Mapped[str] = mapped_column(String(12), nullable=False, default="inbox")         # inbox | telegram
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    run_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    usage_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    usage_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentInboxItem(Base):
    """One entry of the owner's inbox.  Section brief (简报): a task's answer or an alert, each with its
    numbered ``sources``; section confirm (待你确认): a write an unattended run suggested.  A suggestion holds
    the validated operation and no capability: the owner mints the card from the inbox and presses it."""
    __tablename__ = "agent_inbox_items"
    __table_args__ = (UniqueConstraint("owner_hash", "dedupe_key", name="uq_agent_inbox_dedupe"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    section: Mapped[str] = mapped_column(String(10), nullable=False)              # brief | confirm
    kind: Mapped[str] = mapped_column(String(16), nullable=False)                 # task_brief | filing | news | price | action
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="unread")  # unread | read | done | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class AgentBackgroundModel(Base):
    """The model the owner's scheduled tasks use.  A chat key lives only in one request's memory, so
    tasks need their own: encrypted with secret_box, never returned, revocable (``ciphertext`` NULL)."""
    __tablename__ = "agent_background_model"
    owner_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentTelegramBind(Base):
    """The owner's Telegram bridge binding.  The bot token is encrypted with secret_box, never
    returned by any endpoint.  The one-time bind code lives here (hash of a random short code)
    and is cleared after confirmation.  ``chat_id`` and ``user_id`` are the bound Telegram
    account; the bridge ignores messages from any other sender."""
    __tablename__ = "agent_telegram_bind"
    owner_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    bot_token_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bind_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
