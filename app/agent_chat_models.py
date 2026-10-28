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
    # The bot whose line this is (agent_bots).  NULL: a guest's conversation, or an owner's from before bots;
    # the owner's are moved under the default bot when it is created.
    bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
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
    # The layer (second edition): NULL is global, read by every bot; a bot id is that bot's own memory, read
    # by it and by the default bot.  Added to older tables by ``agent_profile.ensure_bot_column``.
    bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
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
    # The bot the task belongs to (second edition, stage C): its settings and memory run the task and its
    # results are posted in that bot's line.  NULL only on a table from before bots, until the default bot
    # adopts it (agent_bots.ensure_default_bot).  Added to older tables by ``agent_tasks.ensure_bot_columns``.
    bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentTaskRun(Base):
    """One run of a task (the bot page lists a bot's latest 20).  ``posted``: the run's conclusion went into
    the bot's line; a quiet run (nothing new) or a failed one posts nothing."""
    __tablename__ = "agent_task_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    task_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    posted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


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
    # The bot whose task made the entry (stage C).  A brief is posted in that bot's line and this row is its
    # unread mark; a 待你确认 entry is labelled with the bot.  NULL: from before bots, the default bot's.
    bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
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


class AgentBot(Base):
    """One of the owner's bots (personal agent plan, second edition): a long-lived post with one ongoing
    conversation.  ``description`` is its work instruction.  ``focus`` narrows attention, never data:
    ``{"companies": [{"symbol", "name"}], "theme": str|None}``; exactly one company makes it the default
    company of unnamed questions.  ``tool_scope`` NULL means every tool; a list can only remove tools.
    ``model`` names a model catalog entry (agent_model_catalog); NULL uses the catalog's default."""
    __tablename__ = "agent_bots"
    __table_args__ = (UniqueConstraint("owner_hash", "name", name="uq_agent_bots_owner_name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    focus: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tool_scope: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    avatar: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    group_name: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    template: Mapped[str] = mapped_column(String(20), nullable=False, default="blank")
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    daily_notify_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentModelEntry(Base):
    """A model catalog entry the owner added on the settings page (agent_model_catalog).  Entries from the
    deployment's config file are not rows.  The key is either here, encrypted with secret_box
    (``key_source`` vault), or read from the named environment variable (``key_source`` env)."""
    __tablename__ = "agent_model_catalog"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    key_source: Mapped[str] = mapped_column(String(8), nullable=False)          # vault | env
    key_env: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    migrated_from: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
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
