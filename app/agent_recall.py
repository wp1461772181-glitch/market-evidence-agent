"""Past conversations: an owner-only index the agent reads when the owner says 上次/之前/我们聊过.

The index holds, per conversation, the owner's own questions (row 0) and the summary segments that
``agent_context_summary`` appended (rows 1..), each with the conversation's name.  It is fed by the
chat store as rows are written and can be rebuilt from those tables with ``backfill``.

What it returns is memory, not evidence: the tool result is labelled 往事，不是证据, is never numbered
and never becomes a citation, so a figure the answer takes from it still needs this turn's read.

Retrieval is lexical (CJK bigrams and Latin words, scored in Python over the owner's rows).  The
knowledge store's hybrid retrieval is bound to knowledge documents and their index generations, and a
single owner's conversations are few, so a separate vector collection is not worth its upkeep yet.

Scope (second edition): a row belongs to the bot whose line its conversation is (``agent_conversations.
bot_id``), so it follows the conversation without a copy.  A bot searches its own line; the default bot
searches every bot's.  A deleted bot's line is archived and no longer searched.
"""
from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .agent_chat_models import (AgentBot, AgentConversation, AgentConversationRecall, AgentConversationSummary,
                                AgentConversationTitle, AgentMessage)
from .database import SessionLocal

log = logging.getLogger(__name__)

LABEL = "往事，不是证据"
NOTE = ("这些是以前对话的摘要和用户当时的提问，只用来知道聊过什么、在哪个对话。它们不是证据，不编号，不能引用；"
        "回答里的数字和事实仍要在本轮重新读取并引用本轮的 [n]。")
MAX_LIMIT = 5
QUESTIONS_MAX_CHARS = 1200
SUMMARY_SHOWN_CHARS = 400
SCAN_ROWS = 600            # the owner's newest index rows that one search scores
TITLE_MAX_CHARS = 120

# Words that only say "remember when"; they would match every conversation.
_STOP_WORDS = ("上次", "上回", "之前", "以前", "前面", "前几天", "我们", "咱们", "聊过", "聊了", "说过", "说了", "问过", "问了",
               "讨论", "提到", "记得", "什么", "哪些", "怎么", "一下", "那个", "这个", "时候", "关于", "多少")
_STOP_CHARS = "的了是吗呢吧啊和与及在把被说过对"
_SPLIT = re.compile("|".join(map(re.escape, _STOP_WORDS)) + "|[" + _STOP_CHARS + "]")
_CJK = re.compile(r"[㐀-鿿]+")
_LATIN = re.compile(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z]", re.I)
_LATIN_STOP = {"the", "we", "what", "about", "did", "last", "time", "talk", "talked", "said", "earlier", "before"}


def terms(query: str) -> list[str]:
    """Search terms of a recall question: CJK bigrams (a lone character is too common) and Latin words."""
    text = str(query or "").casefold()
    found: list[str] = []
    for run in _CJK.findall(text):
        for piece in _SPLIT.split(run):
            found.extend(piece[i:i + 2] for i in range(len(piece) - 1))
    found.extend(w for w in _LATIN.findall(_CJK.sub(" ", text)) if len(w) >= 2 and w not in _LATIN_STOP)
    return list(dict.fromkeys(found))


def _title(db, cid: uuid.UUID) -> str:
    custom = db.get(AgentConversationTitle, cid)
    if custom is not None and custom.title:
        return custom.title[:TITLE_MAX_CHARS]
    first = db.scalar(select(AgentMessage.content).where(AgentMessage.conversation_id == cid, AgentMessage.role == "user")
                      .order_by(AgentMessage.sequence.asc()).limit(1))
    return " ".join(str(first or "新建对话").split())[:TITLE_MAX_CHARS]


def _upsert(db, cid: uuid.UUID, owner: str, seq: int, title: str, text: str) -> None:
    statement = insert(AgentConversationRecall).values(conversation_id=cid, seq=seq, owner_hash=owner, title=title, text=text)
    db.execute(statement.on_conflict_do_update(
        index_elements=[AgentConversationRecall.conversation_id, AgentConversationRecall.seq],
        set_={"title": title, "text": text, "updated_at": func.now()}))


def _owner(db, cid: uuid.UUID) -> str | None:
    return db.scalar(select(AgentConversation.owner_hash).where(AgentConversation.id == cid))


def _join_questions(existing: str, question: str) -> str:
    question = " ".join(str(question or "").split())
    if len(question) < 2 or question in existing.split(" / "):
        return existing
    joined = f"{existing} / {question}" if existing else question
    return joined if len(joined) <= QUESTIONS_MAX_CHARS else existing


def index_question(conversation_id: str, question: str) -> None:
    """The owner asked something in this conversation: row 0 keeps their questions, in order."""
    cid = uuid.UUID(str(conversation_id))
    with SessionLocal() as db:
        owner = _owner(db, cid)
        if owner is None:
            return
        row = db.get(AgentConversationRecall, (cid, 0))
        text = _join_questions(row.text if row else "", question)
        if row is not None and text == row.text:
            return
        _upsert(db, cid, owner, 0, _title(db, cid), text)
        db.commit()


def index_summary(conversation_id: str, seq: int, content: str) -> None:
    """A summary segment was appended: mirror it."""
    cid = uuid.UUID(str(conversation_id))
    with SessionLocal() as db:
        owner = _owner(db, cid)
        if owner is None:
            return
        _upsert(db, cid, owner, int(seq), _title(db, cid), " ".join(str(content or "").split()))
        db.commit()


def index_title(conversation_id: str) -> None:
    """The conversation was named (or un-named): every row carries the name in use."""
    cid = uuid.UUID(str(conversation_id))
    with SessionLocal() as db:
        title = _title(db, cid)
        for row in db.scalars(select(AgentConversationRecall).where(AgentConversationRecall.conversation_id == cid)):
            row.title = title
        db.commit()


def backfill() -> int:
    """Index what was stored before the index existed (or after a failed hook).  Idempotent; returns
    the number of rows written."""
    written = 0
    with SessionLocal() as db:
        have = {(r.conversation_id, r.seq): r.text for r in db.execute(
            select(AgentConversationRecall.conversation_id, AgentConversationRecall.seq, AgentConversationRecall.text))}
        conversations = db.execute(select(AgentConversation.id, AgentConversation.owner_hash)).all()
        for cid, owner in conversations:
            title = None
            if (cid, 0) not in have:
                text = ""
                for question in db.scalars(select(AgentMessage.content).where(
                        AgentMessage.conversation_id == cid, AgentMessage.role == "user").order_by(AgentMessage.sequence.asc())):
                    text = _join_questions(text, question)
                if text:
                    title = _title(db, cid)
                    _upsert(db, cid, owner, 0, title, text)
                    written += 1
            for seq, content in db.execute(select(AgentConversationSummary.seq, AgentConversationSummary.content)
                                           .where(AgentConversationSummary.conversation_id == cid)):
                if (cid, seq) not in have:
                    title = title or _title(db, cid)
                    _upsert(db, cid, owner, seq, title, " ".join(str(content or "").split()))
                    written += 1
        db.commit()
    return written


def _rows(db, owner: str, exclude: str | None, bot_ids=None):
    statement = (select(AgentConversationRecall, AgentConversation.updated_at, AgentConversation.bot_id, AgentBot.name)
                 .join(AgentConversation, AgentConversation.id == AgentConversationRecall.conversation_id)
                 .outerjoin(AgentBot, (AgentBot.id == AgentConversation.bot_id) & (AgentBot.owner_hash == owner))
                 .where(AgentConversationRecall.owner_hash == owner, AgentConversation.owner_hash == owner,
                        AgentConversation.archived_at.is_(None)))
    if bot_ids is not None:
        wanted = []
        for value in bot_ids:
            try:
                wanted.append(uuid.UUID(str(value)))
            except ValueError:
                pass
        statement = statement.where(AgentConversation.bot_id.in_(wanted))
    if exclude:
        try:
            statement = statement.where(AgentConversationRecall.conversation_id != uuid.UUID(str(exclude)))
        except ValueError:
            pass
    return db.execute(statement.order_by(AgentConversation.updated_at.desc(), AgentConversationRecall.seq.desc())
                      .limit(SCAN_ROWS)).all()


def _item(row: AgentConversationRecall, updated_at, bot_id=None, bot_name=None) -> dict:
    cid = str(row.conversation_id)
    return {"conversation_id": cid, "title": row.title, "date": updated_at.date().isoformat() if updated_at else None,
            "summary": row.text[:SUMMARY_SHOWN_CHARS],
            "kind": "summary" if row.seq else "questions", "link": f"#agent/{cid}",
            "bot_id": str(bot_id) if bot_id else None, "bot_name": bot_name}


def _score(row: AgentConversationRecall, wanted: list[str]) -> tuple[int, int]:
    body, title = row.text.casefold(), row.title.casefold()
    matched = [t for t in wanted if t in body or t in title]
    hits = sum(min(3, body.count(t)) + 2 * min(1, title.count(t)) for t in matched)
    return len(matched), hits


def search(owner: str, query: str, limit: int = MAX_LIMIT, *, exclude: str | None = None, bot_ids=None) -> list[dict]:
    """The owner's conversations that best match the query, one line each (best-matching row).  ``bot_ids``
    None: every line; otherwise only those bots' lines."""
    wanted = terms(query)
    if not wanted:
        return recent(owner, limit=limit, exclude=exclude, bot_ids=bot_ids)
    limit = max(1, min(MAX_LIMIT, int(limit)))
    best: dict[str, tuple] = {}
    with SessionLocal() as db:
        for order, (row, updated_at, bot_id, bot_name) in enumerate(_rows(db, owner, exclude, bot_ids)):
            matched, hits = _score(row, wanted)
            if not matched:
                continue
            key = (matched, hits, -order)
            cid = str(row.conversation_id)
            if cid not in best or key > best[cid][0]:
                best[cid] = (key, _item(row, updated_at, bot_id, bot_name))
    ranked = sorted(best.values(), key=lambda value: value[0], reverse=True)[:limit]
    return [item for _key, item in ranked]


def recent(owner: str, limit: int = 20, *, exclude: str | None = None, bot_ids=None) -> list[dict]:
    """The owner's newest conversations, one line each: the newest summary segment, else the questions."""
    limit = max(1, min(50, int(limit)))
    seen: dict[str, dict] = {}
    with SessionLocal() as db:
        for row, updated_at, bot_id, bot_name in _rows(db, owner, exclude, bot_ids):
            cid = str(row.conversation_id)
            if cid not in seen:  # rows of a conversation come newest segment first, questions (seq 0) last
                if len(seen) >= limit:
                    break
                seen[cid] = _item(row, updated_at, bot_id, bot_name)
    return list(seen.values())


def safe_hook(fn, *args) -> None:
    """Index hooks never fail the write they follow; ``backfill`` repairs a missed row."""
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - the index is derived data
        log.warning("recall index update failed", exc_info=True)
