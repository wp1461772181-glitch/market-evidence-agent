"""The owner's scheduled tasks, the inbox they write to, and the model they run on (plan §2.3).

Three things live here, all owner-only:

* **Tasks** (``agent_tasks``): when a task is due and what comes next.  A Mac that slept through several
  slots runs the task once when it wakes, then waits for the next slot after "now" (catch-up once).
* **Inbox** (``agent_inbox_items``): 简报 (a task's answer or an alert, each with numbered sources) and
  待你确认 (a write an unattended run suggested).  A suggestion holds the validated operation and no
  capability; ``mint_card`` turns it into an ordinary confirmation card when the owner is there to press it.
* **Background model** (``agent_background_model``): the key a task needs because a chat key lives only in
  one request's memory.  Stored with ``secret_box``, never returned by any function here, revocable.

The runner itself (an unattended turn, alerts, token caps) is ``agent_task_runner``.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .agent_chat_models import AgentBackgroundModel, AgentInboxItem, AgentResearchQueue, AgentTask
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
    "no_evidence": "已运行：没有取得带来源的证据",
    "needs_input": "已运行：需要你补充信息",
    "skipped_no_model": "未运行：缺少模型",
    "skipped_daily_cap": "未运行：已达每日 token 上限",
    "skipped_no_owner": "未运行：没有所有者登录配置",
    "skipped_no_scope": "未运行：范围里没有股票",
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
            "last_conversation_id": str(task.last_conversation_id) if task.last_conversation_id else None}


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


def create_task(owner_hash: str, fields: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with SessionLocal() as db:
        count = len(db.scalars(select(AgentTask.id).where(AgentTask.owner_hash == owner_hash)).all())
        if count >= MAX_TASKS:
            raise TaskError("too_many_tasks")
        task = AgentTask(owner_hash=owner_hash, enabled=True, usage_tokens=0, symbols=[], config={})
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


def list_tasks(owner_hash: str) -> list[dict]:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        rows = db.scalars(select(AgentTask).where(AgentTask.owner_hash == owner_hash).order_by(AgentTask.created_at)).all()
        return [serialize_task(row, now) for row in rows]


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
            "status": row.status, "created_at": _stamp(row.created_at)}


def add_item(owner_hash: str, *, section: str, kind: str, title: str, body: str = "", sources: list | None = None,
             payload: dict | None = None, task_id=None, conversation_id=None, dedupe_key: str | None = None, db=None) -> bool:
    """Store one inbox entry; False when an entry with the same ``dedupe_key`` exists (alerts run again
    and again over the same window)."""
    if section not in SECTIONS:
        raise TaskError("invalid_section")
    values = {"id": uuid.uuid4(), "owner_hash": owner_hash, "section": section, "kind": kind, "title": str(title)[:200],
              "body": str(body or ""), "sources": list(sources or []), "payload": dict(payload or {}),
              "task_id": uuid.UUID(str(task_id)) if task_id else None,
              "conversation_id": uuid.UUID(str(conversation_id)) if conversation_id else None,
              "dedupe_key": dedupe_key[:200] if dedupe_key else None, "status": "unread"}
    statement = insert(AgentInboxItem).values(**values).on_conflict_do_nothing(constraint="uq_agent_inbox_dedupe")
    if db is not None:
        return bool(db.execute(statement).rowcount)
    with SessionLocal() as session:
        added = bool(session.execute(statement).rowcount)
        session.commit()
        return added


def list_inbox(owner_hash: str, *, section: str | None = None, limit: int = 50) -> list[dict]:
    with SessionLocal() as db:
        query = select(AgentInboxItem).where(AgentInboxItem.owner_hash == owner_hash, AgentInboxItem.status != "dismissed")
        if section:
            query = query.where(AgentInboxItem.section == section)
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
