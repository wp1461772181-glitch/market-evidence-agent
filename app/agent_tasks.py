"""The owner's scheduled tasks, the inbox they write to, and the model they run on (plan §2.3).

Three things live here, all owner-only:

* **Tasks** (``agent_tasks``): when a task is due and what comes next.  A Mac that slept through several
  slots runs the task once when it wakes, then waits for the next slot after "now" (catch-up once).
* **Inbox** (``agent_inbox_items``): 简报 (a task's answer or an alert, each with numbered sources) and
  待你确认 (a write an unattended run suggested).  A suggestion holds the validated operation and no
  capability; ``mint_card`` turns it into an ordinary confirmation card when the owner is there to press it.
* **Background model** (``agent_background_model``): the key a task needs because a chat key lives only in
  one request's memory.  Stored with ``secret_box``, never returned by any function here, revocable.

Second edition, stage C: a task belongs to a bot (``bot_id``).  Its results are posted in that bot's
line as a turn of their own (``post_to_line``), so the owner follows up right below; the 简报 inbox row
stays as the post's unread mark.  A post waits while the owner's own turn in that line is unfinished, so
it never splits it.  Each run is logged (``agent_task_runs``) for the bot page.

The runner itself (an unattended turn, alerts, token caps) is ``agent_task_runner``.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text as sql
from sqlalchemy.dialects.postgresql import insert

from .agent_chat_models import (AgentBackgroundModel, AgentBot, AgentConversation, AgentInboxItem, AgentMessage,
                                AgentResearchQueue, AgentTask, AgentTaskRun)
from .agent_recall import TASK_POST_HEAD
from .database import SessionLocal
from .secret_box import CredentialEncryptionError, decrypt_secret, encrypt_secret

KINDS = ("brief", "alerts")
SCHEDULES = ("daily", "weekdays", "hourly")
SCOPES = ("research_queue", "symbols")
CHANNELS = ("inbox", "telegram")
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_RUN_TOKENS = 60_000
DEFAULT_DAILY_TOKENS = 200_000
RUN_TOKENS_RANGE = (5_000, 150_000)
DAILY_TOKENS_MAX = 2_000_000
MAX_TASKS = 20
INSTRUCTION_MAX_CHARS = 2000
MAX_SYMBOLS = 30
MAX_PRICE_LINES = 30

# What the task list shows for the outcome of the last run.
STATUS_LABELS = {
    "running": "运行中",
    "ok": "已运行",
    "quiet": "已运行：没有新东西",
    "no_evidence": "已运行：没有取得带来源的证据",
    "needs_input": "已运行：需要你补充信息",
    "skipped_no_model": "未运行：缺少模型",
    "skipped_daily_cap": "未运行：已达每日 token 上限",
    "skipped_no_owner": "未运行：没有所有者登录配置",
    "skipped_no_scope": "未运行：范围里没有股票",
    "skipped_no_bot": "未运行：bot 已不存在",
    "failed": "运行失败",
}
SECTIONS = ("brief", "confirm")
INBOX_STATUSES = ("unread", "read", "done", "dismissed")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")


class TaskError(ValueError):
    """A short code the API may show; never a stored value."""


# --- schedule -------------------------------------------------------------------------------------

def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TaskError("invalid_timezone") from exc


def next_run(schedule_kind: str, schedule_time: str, timezone: str, after: datetime) -> datetime:
    """The first slot strictly after ``after`` (UTC).  daily/weekdays: HH:MM local time (weekdays skips
    Saturday and Sunday); hourly: minute MM of every hour."""
    match = _TIME.fullmatch(schedule_time or "")
    if not match or schedule_kind not in SCHEDULES:
        raise TaskError("invalid_schedule")
    if after.tzinfo is None:
        raise TaskError("naive_datetime")
    hour, minute = int(match.group(1)), int(match.group(2))
    zone = _zone(timezone)
    after = after.astimezone(UTC)
    local = after.astimezone(zone)
    if schedule_kind == "hourly":
        slot = local.replace(minute=minute, second=0, microsecond=0).astimezone(UTC)
        while slot <= after:
            slot += timedelta(hours=1)
        return slot
    for offset in range(0, 9):
        day = local.date() + timedelta(days=offset)
        if schedule_kind == "weekdays" and day.weekday() >= 5:
            continue
        # A local time that a DST change skips resolves through UTC, like the wall clock does.
        slot = datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)
        if slot > after:
            return slot
    raise TaskError("invalid_schedule")  # unreachable: some weekday lies within nine days


def is_due(task: AgentTask, now: datetime) -> bool:
    return bool(task.enabled and task.next_run_at is not None and task.next_run_at <= now)


def missed_slots(task: AgentTask, now: datetime, limit: int = 1000) -> int:
    """How many slots passed between the one that was due and now (1 = on time).  Only reported: however
    many were missed, a due task runs once."""
    if not is_due(task, now):
        return 0
    count, slot = 1, task.next_run_at
    while count < limit:
        slot = next_run(task.schedule_kind, task.schedule_time, task.timezone, slot)
        if slot > now:
            break
        count += 1
    return count


def advance(task: AgentTask, now: datetime) -> None:
    """Called when a due task starts: missed slots collapse into this run (catch-up once)."""
    task.next_run_at = next_run(task.schedule_kind, task.schedule_time, task.timezone, now)


def local_day(task: AgentTask, now: datetime) -> date:
    return now.astimezone(_zone(task.timezone)).date()


def tokens_used_today(task: AgentTask, now: datetime) -> int:
    return task.usage_tokens if task.usage_day == local_day(task, now) else 0


def add_usage(task: AgentTask, now: datetime, tokens: int) -> None:
    today = local_day(task, now)
    if task.usage_day != today:
        task.usage_day, task.usage_tokens = today, 0
    task.usage_tokens += max(0, int(tokens or 0))


# --- tasks ----------------------------------------------------------------------------------------

def _stamp(value):
    return value.astimezone(UTC).isoformat() if value else None


def serialize_task(task: AgentTask, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    return {"id": str(task.id), "name": task.name, "kind": task.kind, "schedule_kind": task.schedule_kind,
            "schedule_time": task.schedule_time, "timezone": task.timezone, "instruction": task.instruction,
            "scope": task.scope, "symbols": list(task.symbols or []), "channel": task.channel,
            "config": task.config or {}, "enabled": task.enabled, "run_token_cap": task.run_token_cap,
            "daily_token_cap": task.daily_token_cap, "tokens_today": tokens_used_today(task, now),
            "last_run_at": _stamp(task.last_run_at), "next_run_at": _stamp(task.next_run_at),
            "last_status": task.last_status, "last_status_label": STATUS_LABELS.get(task.last_status or "", None),
            "last_conversation_id": str(task.last_conversation_id) if task.last_conversation_id else None,
            "bot_id": str(task.bot_id) if task.bot_id else None,
            "skill_id": str(task.skill_id) if task.skill_id else None}


def _symbols(values) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_SYMBOLS:
        raise TaskError("invalid_symbols")
    cleaned = []
    for value in values:
        symbol = str(value or "").strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise TaskError("invalid_symbols")
        cleaned.append(symbol)
    return list(dict.fromkeys(cleaned))


def _price_lines(values) -> list[dict]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_PRICE_LINES:
        raise TaskError("invalid_price_lines")
    lines = []
    for value in values:
        if not isinstance(value, dict):
            raise TaskError("invalid_price_lines")
        symbol = _symbols([value.get("symbol")])[0]
        line = {"symbol": symbol}
        for side in ("above", "below"):
            if value.get(side) is not None:
                try:
                    price = float(value[side])
                except (TypeError, ValueError) as exc:
                    raise TaskError("invalid_price_lines") from exc
                if not 0 < price < 1e7:
                    raise TaskError("invalid_price_lines")
                line[side] = price
        if len(line) == 1:
            raise TaskError("invalid_price_lines")
        lines.append(line)
    return lines


def _config(kind: str, value) -> dict:
    value = value or {}
    if not isinstance(value, dict):
        raise TaskError("invalid_config")
    if kind != "alerts":
        return {}
    return {"filings": bool(value.get("filings", True)), "news": bool(value.get("news", True)),
            "price_lines": _price_lines(value.get("price_lines"))}


def _apply(task: AgentTask, fields: dict, now: datetime) -> None:
    kind = fields.get("kind", task.kind or "brief")
    if kind not in KINDS:
        raise TaskError("invalid_kind")
    name = " ".join(str(fields.get("name", task.name) or "").split())
    if not name or len(name) > 80:
        raise TaskError("invalid_name")
    schedule_kind = fields.get("schedule_kind", task.schedule_kind)
    schedule_time = fields.get("schedule_time", task.schedule_time)
    timezone = fields.get("timezone", task.timezone) or DEFAULT_TIMEZONE
    _zone(timezone)
    if schedule_kind not in SCHEDULES or not _TIME.fullmatch(str(schedule_time or "")):
        raise TaskError("invalid_schedule")
    instruction = fields.get("instruction", task.instruction)
    instruction = str(instruction).strip() if instruction is not None else None
    if kind == "brief" and (not instruction or len(instruction) > INSTRUCTION_MAX_CHARS):
        raise TaskError("invalid_instruction")
    scope = fields.get("scope", task.scope or "research_queue")
    if scope not in SCOPES:
        raise TaskError("invalid_scope")
    symbols = _symbols(fields.get("symbols", task.symbols))
    if scope == "symbols" and not symbols:
        raise TaskError("invalid_symbols")
    channel = fields.get("channel", task.channel or "inbox")
    if channel not in CHANNELS:
        raise TaskError("invalid_channel")
    if kind == "brief":
        # Whether a brief notifies is its bot's call (notify_telegram, per the instruction); only alerts,
        # which run no model, keep the 也发 Telegram switch.
        channel = "inbox"
    run_cap = int(fields.get("run_token_cap", task.run_token_cap or DEFAULT_RUN_TOKENS))
    daily_cap = int(fields.get("daily_token_cap", task.daily_token_cap or DEFAULT_DAILY_TOKENS))
    if not RUN_TOKENS_RANGE[0] <= run_cap <= RUN_TOKENS_RANGE[1] or not run_cap <= daily_cap <= DAILY_TOKENS_MAX:
        raise TaskError("invalid_token_caps")
    config = _config(kind, fields.get("config", task.config))
    rescheduled = (schedule_kind, schedule_time, timezone) != (task.schedule_kind, task.schedule_time, task.timezone)
    task.kind, task.name, task.schedule_kind, task.schedule_time, task.timezone = kind, name, schedule_kind, schedule_time, timezone
    task.instruction = instruction if kind == "brief" else None
    task.scope, task.symbols, task.channel, task.config = scope, symbols, channel, config
    task.run_token_cap, task.daily_token_cap = run_cap, daily_cap
    if "enabled" in fields:
        task.enabled = bool(fields["enabled"])
    if rescheduled or task.next_run_at is None:
        task.next_run_at = next_run(schedule_kind, schedule_time, timezone, now)


def ensure_bot_columns() -> None:
    """Add the bot column to task and inbox tables made before bots.  A fresh database gets it from the model."""
    from .database import engine
    with engine.begin() as connection:
        for table in ("agent_tasks", "agent_inbox_items"):
            connection.execute(sql(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS bot_id UUID"))
            connection.execute(sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_bot_id ON {table} (bot_id)"))


BRIEF_TELEGRAM_NOTE = ("（迁移说明：这个任务以前每次运行都推送到 Telegram。现在由 bot 判断：有值得告诉我的新结论时，"
                       "用 notify_telegram 发给我。）")


def migrate_brief_channels() -> int:
    """Stage D: a brief task that pushed every run to Telegram gets that said in its instruction (the bot now
    decides, with notify_telegram) and keeps only the conversation channel.  Idempotent."""
    with SessionLocal() as db:
        rows = db.scalars(select(AgentTask).where(AgentTask.kind == "brief", AgentTask.channel == "telegram")).all()
        for task in rows:
            instruction = (task.instruction or "").strip()
            if BRIEF_TELEGRAM_NOTE not in instruction:
                instruction = (instruction + "\n" + BRIEF_TELEGRAM_NOTE).strip()[:INSTRUCTION_MAX_CHARS + len(BRIEF_TELEGRAM_NOTE)]
            task.instruction, task.channel = instruction, "inbox"
        db.commit()
        return len(rows)


def _task_skill(owner_hash: str, skill_id) -> uuid.UUID | None:
    """The skill a task runs with (stage E): one the owner can use, or none ("" clears it)."""
    if skill_id in (None, ""):
        return None
    from .agent_skills import SkillError, clean_usual
    try:
        return uuid.UUID(clean_usual(owner_hash, [skill_id])[0])
    except SkillError as exc:
        raise TaskError("unknown_skill") from exc


def _task_bot(db, owner_hash: str, bot_id) -> uuid.UUID:
    """The bot a task is attached to: one of this owner's bots, or the default bot when none is named."""
    if bot_id in (None, ""):
        from .agent_bots import ensure_default_bot
        return ensure_default_bot(owner_hash).id
    try:
        identity = uuid.UUID(str(bot_id))
    except (TypeError, ValueError) as exc:
        raise TaskError("unknown_bot") from exc
    bot = db.get(AgentBot, identity)
    if bot is None or bot.owner_hash != owner_hash:
        raise TaskError("unknown_bot")
    return bot.id


def create_task(owner_hash: str, fields: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with SessionLocal() as db:
        count = len(db.scalars(select(AgentTask.id).where(AgentTask.owner_hash == owner_hash)).all())
        if count >= MAX_TASKS:
            raise TaskError("too_many_tasks")
        task = AgentTask(owner_hash=owner_hash, enabled=True, usage_tokens=0, symbols=[], config={})
        task.bot_id = _task_bot(db, owner_hash, fields.get("bot_id"))
        task.skill_id = _task_skill(owner_hash, fields.get("skill_id"))
        _apply(task, fields, now)
        db.add(task)
        db.commit()
        db.refresh(task)
        return serialize_task(task, now)


def _owned_task(db, owner_hash: str, task_id) -> AgentTask | None:
    try:
        identity = uuid.UUID(str(task_id))
    except (TypeError, ValueError):
        return None
    task = db.get(AgentTask, identity)
    return task if task is not None and task.owner_hash == owner_hash else None


def update_task(owner_hash: str, task_id, fields: dict, *, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(UTC)
    with SessionLocal() as db:
        task = _owned_task(db, owner_hash, task_id)
        if task is None:
            return None
        if "bot_id" in fields:
            task.bot_id = _task_bot(db, owner_hash, fields["bot_id"])
        if "skill_id" in fields:
            task.skill_id = _task_skill(owner_hash, fields["skill_id"])
        _apply(task, fields, now)
        db.commit()
        db.refresh(task)
        return serialize_task(task, now)


def delete_task(owner_hash: str, task_id) -> bool:
    with SessionLocal() as db:
        task = _owned_task(db, owner_hash, task_id)
        if task is None:
            return False
        db.delete(task)
        db.commit()
        return True


def list_tasks(owner_hash: str, bot_id=None) -> list[dict]:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        query = select(AgentTask).where(AgentTask.owner_hash == owner_hash)
        if bot_id is not None:
            query = query.where(AgentTask.bot_id == _uuid(bot_id))
        rows = db.scalars(query.order_by(AgentTask.created_at)).all()
        return [serialize_task(row, now) for row in rows]


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def log_run(db, task: AgentTask, *, status: str, tokens: int = 0, posted: int = 0, conversation_id=None) -> None:
    db.add(AgentTaskRun(owner_hash=task.owner_hash, task_id=task.id, bot_id=task.bot_id, task_name=task.name,
                        status=status, posted=int(posted), tokens=max(0, int(tokens or 0)),
                        conversation_id=uuid.UUID(str(conversation_id)) if conversation_id else None))


def list_runs(owner_hash: str, bot_id=None, *, limit: int = 20) -> list[dict]:
    """A bot's latest runs (all its tasks), newest first, for the bot page."""
    with SessionLocal() as db:
        query = select(AgentTaskRun).where(AgentTaskRun.owner_hash == owner_hash)
        if bot_id is not None:
            query = query.where(AgentTaskRun.bot_id == _uuid(bot_id))
        rows = db.scalars(query.order_by(AgentTaskRun.created_at.desc()).limit(max(1, min(100, limit)))).all()
        return [{"id": str(r.id), "task_id": str(r.task_id), "task_name": r.task_name, "status": r.status,
                 "status_label": STATUS_LABELS.get(r.status), "posted": r.posted, "tokens": r.tokens,
                 "conversation_id": str(r.conversation_id) if r.conversation_id else None,
                 "created_at": _stamp(r.created_at)} for r in rows]


def scope_symbols(db, task: AgentTask) -> list[str]:
    if task.scope == "symbols":
        return list(task.symbols or [])
    queue = db.get(AgentResearchQueue, task.owner_hash)
    return list(queue.symbols or []) if queue is not None else []


# --- background model -----------------------------------------------------------------------------

def background_summary(owner_hash: str) -> dict:
    """What the settings page shows.  Never the key, nor any part of it."""
    with SessionLocal() as db:
        row = db.get(AgentBackgroundModel, owner_hash)
        if row is None:
            return {"configured": False, "has_key": False}
        return {"configured": True, "provider": row.provider, "model": row.model, "base_url": row.base_url,
                "has_key": bool(row.ciphertext), "revoked_at": _stamp(row.revoked_at), "updated_at": _stamp(row.updated_at)}


def save_background_model(owner_hash: str, *, provider: str, model: str, base_url: str | None, api_key: str | None) -> dict:
    from .agent_chat_api import PROVIDER_NAMES, _validated_base_url
    if provider not in PROVIDER_NAMES:
        raise TaskError("invalid_provider")
    if not _MODEL.fullmatch(model or ""):
        raise TaskError("invalid_model")
    try:
        base_url = _validated_base_url(base_url) if base_url else None
    except ValueError as exc:
        raise TaskError("invalid_endpoint") from exc
    if provider == "custom" and not base_url:
        raise TaskError("invalid_endpoint")
    if api_key is not None and (not 8 <= len(api_key) <= 2048 or api_key.strip() != api_key):
        raise TaskError("invalid_key")
    with SessionLocal() as db:
        row = db.get(AgentBackgroundModel, owner_hash, with_for_update=True)
        same = row is not None and (row.provider, row.model, row.base_url) == (provider, model, base_url)
        if api_key:
            try:
                ciphertext = encrypt_secret(api_key)
            except CredentialEncryptionError as exc:
                raise TaskError("credential_key_missing") from exc
        elif same and row.ciphertext:
            ciphertext = row.ciphertext  # changing nothing but keeping the saved key
        else:
            raise TaskError("key_required")
        if row is None:
            row = AgentBackgroundModel(owner_hash=owner_hash, provider=provider, model=model)
            db.add(row)
        row.provider, row.model, row.base_url, row.ciphertext, row.revoked_at = provider, model, base_url, ciphertext, None
        db.commit()
    return background_summary(owner_hash)


def revoke_background_key(owner_hash: str) -> dict:
    """The key is erased (not kept disabled); provider and model stay so the page can say what was used."""
    with SessionLocal() as db:
        row = db.get(AgentBackgroundModel, owner_hash, with_for_update=True)
        if row is not None and row.ciphertext:
            row.ciphertext, row.revoked_at = None, datetime.now(UTC)
            # The model catalog's copy of this key (agent_model_catalog.migrate_legacy) goes with it.
            from .agent_chat_models import AgentModelEntry
            db.query(AgentModelEntry).filter(AgentModelEntry.migrated_from == f"background:{owner_hash[:16]}").update(
                {"ciphertext": None}, synchronize_session=False)
            db.commit()
    return background_summary(owner_hash)


def load_background_model(owner_hash: str) -> dict | None:
    """The runner's model with its key in memory, or None when none is saved, it was revoked, or it no
    longer decrypts (a changed deployment secret).  Callers keep the key out of every log and row."""
    with SessionLocal() as db:
        row = db.get(AgentBackgroundModel, owner_hash)
        if row is None or not row.ciphertext:
            return None
        try:
            key = decrypt_secret(row.ciphertext)
        except CredentialEncryptionError:
            return None
        return {"provider": row.provider, "model": row.model, "base_url": row.base_url, "api_key": key}


# --- inbox ----------------------------------------------------------------------------------------

def serialize_item(row: AgentInboxItem) -> dict:
    payload = row.payload or {}
    shown = {k: payload[k] for k in ("display", "task_name", "channel_pending", "missed_slots") if k in payload}
    return {"id": str(row.id), "section": row.section, "kind": row.kind, "title": row.title, "body": row.body,
            "sources": list(row.sources or []), "payload": shown, "task_id": str(row.task_id) if row.task_id else None,
            "conversation_id": str(row.conversation_id) if row.conversation_id else None,
            "bot_id": str(row.bot_id) if row.bot_id else None,
            "posted": bool(payload.get("posted_turn_id")), "status": row.status, "created_at": _stamp(row.created_at)}


def add_item(owner_hash: str, *, section: str, kind: str, title: str, body: str = "", sources: list | None = None,
             payload: dict | None = None, task_id=None, conversation_id=None, dedupe_key: str | None = None, db=None,
             bot_id=None, post: dict | None = None, now: datetime | None = None, telegram: bool = False) -> bool:
    """Store one inbox entry; False when an entry with the same ``dedupe_key`` exists (alerts run again
    and again over the same window).  ``post`` (a brief of a bot's task: ``{"head", "citations"}``) also
    posts it in the bot's line, now or, while the owner's turn there is unfinished, at the next flush.
    ``telegram`` (an alerts task with 也发 Telegram) also queues it for the bound chat through the same send
    path as notify_telegram, within the bot's daily cap; a full cap or no bound chat only skips the message."""
    if section not in SECTIONS:
        raise TaskError("invalid_section")
    payload = dict(payload or {})
    if post is not None and bot_id:
        payload.update(post_pending=True, post_head=str(post.get("head") or "")[:200],
                       post_citations=list(post.get("citations") or []))
    values = {"id": uuid.uuid4(), "owner_hash": owner_hash, "section": section, "kind": kind, "title": str(title)[:200],
              "body": str(body or ""), "sources": list(sources or []), "payload": payload,
              "task_id": uuid.UUID(str(task_id)) if task_id else None,
              "conversation_id": uuid.UUID(str(conversation_id)) if conversation_id else None,
              "bot_id": uuid.UUID(str(bot_id)) if bot_id else None,
              "dedupe_key": dedupe_key[:200] if dedupe_key else None, "status": "unread"}
    statement = (insert(AgentInboxItem).values(**values).on_conflict_do_nothing(constraint="uq_agent_inbox_dedupe")
                 .returning(AgentInboxItem.id))

    def store(session) -> bool:
        identity = session.execute(statement).scalar()
        if identity is None:
            return False
        if payload.get("post_pending"):
            post_to_line(session, session.get(AgentInboxItem, identity), now or datetime.now(UTC))
        if telegram and bot_id:
            from . import agent_telegram as telegram_out
            bot = session.get(AgentBot, uuid.UUID(str(bot_id)))
            cited = [s.get("ordinal") for s in sources or [] if isinstance(s, dict) and type(s.get("ordinal")) is int]
            text = telegram_out.render(bot.name if bot else "", f"{title}\n{body}", list(sources or []), cited)
            try:
                with session.begin_nested():
                    telegram_out.enqueue(session, owner_hash, bot_id, text, source="alert", now=now, inbox_item_id=identity)
            except telegram_out.NotifyError:
                pass
        return True
    if db is not None:
        return store(db)
    with SessionLocal() as session:
        added = store(session)
        session.commit()
        return added


# --- the bot's line ---------------------------------------------------------------------------------

POST_HEAD = TASK_POST_HEAD  # recall keeps these rows out of the owner's questions
POST_NOTE = "（系统自动贴入，不是所有者说的话）"
# A turn of the owner that has not finished (its last row is not a final answer) holds the posts back,
# unless it is this old: then it was cut off and will never finish.
BUSY_FOR = timedelta(minutes=15)


def post_head(task_name: str, when: datetime, timezone: str) -> str:
    local = when.astimezone(_zone(timezone))
    return f"{POST_HEAD}「{task_name}」{local:%Y-%m-%d %H:%M}{POST_NOTE}"


def _final(row: AgentMessage) -> bool:
    calls = isinstance(row.tool_calls, dict) and row.tool_calls.get("calls")
    return row.role == "assistant" and row.status in {"complete", "failed", "cancelled"} and not calls


def line_busy(db, conversation_id, now: datetime) -> bool:
    last = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
                      .order_by(AgentMessage.sequence.desc()).limit(1)).first()
    if last is None or _final(last):
        return False
    created = last.created_at if last.created_at.tzinfo else last.created_at.replace(tzinfo=UTC)
    return now - created < BUSY_FOR


def post_to_line(db, item: AgentInboxItem | None, now: datetime) -> bool:
    """Append the brief as a turn of its own at the end of its bot's line: a row that says it is a task's
    result (not the owner's words), then the brief with its citations, so the next turn there carries it
    and can cite it again.  False (it stays pending) while the line is busy or the bot has no line."""
    payload = dict(item.payload or {}) if item is not None else {}
    if not payload.get("post_pending") or item.bot_id is None:
        return False
    bot = db.get(AgentBot, item.bot_id)
    if bot is None or bot.owner_hash != item.owner_hash or bot.conversation_id is None:
        return False
    line = db.query(AgentConversation).filter(AgentConversation.id == bot.conversation_id).with_for_update().first()
    if line is None or line.archived_at is not None or line_busy(db, line.id, now):
        return False
    sequence = db.scalar(select(func.coalesce(func.max(AgentMessage.sequence), 0))
                         .where(AgentMessage.conversation_id == line.id))
    turn = uuid.uuid4()
    marker = {"task_id": str(item.task_id) if item.task_id else None, "task_name": payload.get("task_name"),
              "kind": item.kind, "item_id": str(item.id),
              "run_conversation_id": str(item.conversation_id) if item.conversation_id else None}
    protocol = {"task_post": marker, "audit": {}}
    if payload.get("post_citations"):
        protocol["citations"] = payload["post_citations"]
    db.add(AgentMessage(conversation_id=line.id, turn_id=turn, sequence=sequence + 1, role="user",
                        content=payload.get("post_head") or POST_HEAD, status="complete"))
    db.add(AgentMessage(conversation_id=line.id, turn_id=turn, sequence=sequence + 2, role="assistant",
                        content=item.body, tool_calls=protocol, status="complete"))
    line.updated_at = func.now()
    for key in ("post_pending", "post_head", "post_citations"):
        payload.pop(key, None)
    payload["posted_turn_id"] = str(turn)
    item.payload = payload
    db.flush()
    return True


def flush_posts(owner_hash: str | None = None, bot_id=None, *, now: datetime | None = None) -> int:
    """Post the briefs that waited for a busy line (the runner's tick, and the end of each owner turn)."""
    now = now or datetime.now(UTC)
    posted = 0
    with SessionLocal() as db:
        query = select(AgentInboxItem).where(AgentInboxItem.section == "brief", AgentInboxItem.bot_id.is_not(None),
                                             AgentInboxItem.status != "dismissed")
        if owner_hash:
            query = query.where(AgentInboxItem.owner_hash == owner_hash)
        if bot_id is not None:
            query = query.where(AgentInboxItem.bot_id == _uuid(bot_id))
        rows = db.scalars(query.order_by(AgentInboxItem.created_at).limit(500)).all()
        for row in rows:
            if (row.payload or {}).get("post_pending") and post_to_line(db, row, now):
                posted += 1
        db.commit()
    return posted


def last_post(db, task_id) -> AgentInboxItem | None:
    """The newest brief of this task that went into its bot's line (what the next run compares with)."""
    rows = db.scalars(select(AgentInboxItem).where(AgentInboxItem.task_id == task_id, AgentInboxItem.section == "brief")
                      .order_by(AgentInboxItem.created_at.desc()).limit(20)).all()
    return next((row for row in rows if (row.payload or {}).get("posted_turn_id") or (row.payload or {}).get("post_pending")), None)


def bot_counts(owner_hash: str) -> dict:
    """{bot_id: {"unread": briefs not yet seen, "pending": 待你确认 entries}} for the sidebar."""
    counts: dict = {}
    with SessionLocal() as db:
        rows = db.execute(select(AgentInboxItem.bot_id, AgentInboxItem.section, func.count())
                          .where(AgentInboxItem.owner_hash == owner_hash, AgentInboxItem.status == "unread")
                          .group_by(AgentInboxItem.bot_id, AgentInboxItem.section)).all()
    for bot_id, section, count in rows:
        entry = counts.setdefault(str(bot_id) if bot_id else None, {"unread": 0, "pending": 0})
        entry["unread" if section == "brief" else "pending"] += int(count)
    return counts


def mark_bot_read(owner_hash: str, bot_id) -> int:
    """Opening a bot's line marks its posted briefs read.  待你确认 entries stay until they are handled."""
    identity = _uuid(bot_id)
    if identity is None:
        return 0
    with SessionLocal() as db:
        changed = db.query(AgentInboxItem).filter(AgentInboxItem.owner_hash == owner_hash, AgentInboxItem.bot_id == identity,
                                                  AgentInboxItem.section == "brief", AgentInboxItem.status == "unread").update(
            {"status": "read"}, synchronize_session=False)
        db.commit()
        return int(changed)


def list_inbox(owner_hash: str, *, section: str | None = None, limit: int = 50, bot_id=None) -> list[dict]:
    with SessionLocal() as db:
        query = select(AgentInboxItem).where(AgentInboxItem.owner_hash == owner_hash, AgentInboxItem.status != "dismissed")
        if section:
            query = query.where(AgentInboxItem.section == section)
        if bot_id is not None:
            query = query.where(AgentInboxItem.bot_id == _uuid(bot_id))
        rows = db.scalars(query.order_by(AgentInboxItem.created_at.desc()).limit(max(1, min(200, limit)))).all()
        return [serialize_item(row) for row in rows]


def _owned_item(db, owner_hash: str, item_id) -> AgentInboxItem | None:
    try:
        identity = uuid.UUID(str(item_id))
    except (TypeError, ValueError):
        return None
    row = db.get(AgentInboxItem, identity)
    return row if row is not None and row.owner_hash == owner_hash else None


def set_item_status(owner_hash: str, item_id, status: str) -> dict | None:
    if status not in INBOX_STATUSES:
        raise TaskError("invalid_status")
    with SessionLocal() as db:
        row = _owned_item(db, owner_hash, item_id)
        if row is None:
            return None
        row.status = status
        db.commit()
        return serialize_item(row)


CARD_REQUEST = "从收件箱查看建议："
CARD_NOTE = "这是定时任务在无人值守时提出的建议，不是你的要求。请核对确认卡；点「同意」才会执行。"


async def mint_card(credential: str, item_id) -> dict:
    """Turn a 待你确认 suggestion into a confirmation card in the run's conversation, now that the owner is
    here: the card gets the usual one-time capability (ten minutes), the conversation's state holds it as the
    pending request, and the stored assistant message shows it.  Pressing it is an ordinary card press."""
    from .agent_chat_store import append_message, owner_hash, verify_conversation
    from .agent_workflow import consent
    from .agent_workflow.grants import label_suggestion_card
    from .agent_workflow.graph import set_pending_request
    owner = owner_hash(credential)
    with SessionLocal() as db:
        row = _owned_item(db, owner, item_id)
        if row is None or row.section != "confirm" or row.status == "dismissed":
            raise TaskError("inbox_item_not_found")
        payload = dict(row.payload or {})
        conversation = str(row.conversation_id) if row.conversation_id else None
    operation, display = payload.get("operation"), payload.get("display")
    if not isinstance(operation, dict) or not isinstance(display, dict) or not conversation:
        raise TaskError("inbox_item_invalid")
    if not await verify_conversation(conversation, credential):
        raise TaskError("inbox_item_not_found")
    card = await asyncio.to_thread(consent.propose, credential, conversation, [operation], display,
                                   question=str(payload.get("question") or ""))
    label_suggestion_card(card, operation)
    await set_pending_request(owner, conversation, card)
    # A turn of its own, opened by the owner's click in the inbox (the browser shows turns that start with
    # the owner's row), ending in the card exactly as a chat turn that proposed it would.
    turn = str(uuid.uuid4())
    clarification = {"request_id": card["id"], "reason": card["reason"], "choices": card["choices"],
                     "display": card.get("display"), "question": card.get("question")}
    await append_message(conversation, turn, "user", CARD_REQUEST + str(display.get("title") or "")[:80])
    await append_message(conversation, turn, "assistant", CARD_NOTE, tool_calls={"clarification": clarification})
    set_item_status(owner, item_id, "read")
    return {"conversation_id": conversation, "request_id": card["id"], "expires_at": card.get("expires_at")}
