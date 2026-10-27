import asyncio, hashlib, uuid, json
from sqlalchemy import select, func, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from .config import DATABASE_URL
from .agent_chat_models import (AgentConversation, AgentConversationSummary, AgentConversationTitle, AgentMessage,
                                AgentResearchQueue)
from . import agent_context as layers
from . import agent_recall as recall
ASYNC_URL=DATABASE_URL
engine=create_async_engine(ASYNC_URL,pool_pre_ping=True,connect_args={"connect_timeout":3})
Session=async_sessionmaker(engine,expire_on_commit=False)
async def _recall(hook,*args):
    # The past-conversation index is derived data: a failed update never fails the write it follows.
    await asyncio.to_thread(recall.safe_hook,hook,*args)
def owner_hash(credential: str)->str: return hashlib.sha256(credential.encode()).hexdigest()
def _message_bytes(m):
    return layers.row_bytes({"role":m.role,"content":m.content,"tool_calls":m.tool_calls,
                             "tool_call_id":m.tool_call_id,"status":m.status,"turn_id":str(m.turn_id)})
async def create_conversation(credential: str)->str:
    async with Session() as s:
        obj=AgentConversation(owner_hash=owner_hash(credential)); s.add(obj); await s.commit(); return str(obj.id)
async def verify_conversation(conversation_id: str, credential: str)->bool:
    try: cid=uuid.UUID(conversation_id)
    except ValueError:return False
    async with Session() as s:
        return await s.scalar(select(AgentConversation.id).where(AgentConversation.id==cid,AgentConversation.owner_hash==owner_hash(credential))) is not None
async def append_message(conversation_id: str, turn_id: str, role: str, content: str, *, tool_calls=None, tool_call_id=None,status="complete"):
    async with Session() as s:
        cid=uuid.UUID(conversation_id)
        seq=(await s.scalar(select(func.coalesce(func.max(AgentMessage.sequence),0)).where(AgentMessage.conversation_id==cid)))+1
        s.add(AgentMessage(conversation_id=cid,turn_id=uuid.UUID(turn_id),sequence=seq,role=role,content=content,tool_calls=tool_calls,tool_call_id=tool_call_id,status=status))
        conv=await s.get(AgentConversation,cid)
        if conv: conv.updated_at=func.now()
        await s.commit()
    if role=="user":await _recall(recall.index_question,conversation_id,content)
async def history(conversation_id: str, credential: str, limit_bytes=14000):
    if not await verify_conversation(conversation_id,credential): return None
    async with Session() as s:
        rows=(await s.scalars(select(AgentMessage).where(AgentMessage.conversation_id==uuid.UUID(conversation_id)).order_by(AgentMessage.sequence.desc()).limit(500))).all()
        rows=list(reversed(rows))
        output=[]; used=0
        # Keep complete turns only; group by turn ID and include newest full groups.
        groups=[]
        for row in rows:
            if not groups or groups[-1][0]!=row.turn_id: groups.append((row.turn_id,[]))
            groups[-1][1].append(row)
        for _,group in reversed(groups):
            if not group or group[0].role != "user" or group[-1].role != "assistant" or group[-1].status != "complete":
                continue
            size=sum(_message_bytes(m) for m in group)
            if used+size>limit_bytes: break
            output[0:0]=[{"role":m.role,"content":m.content,"tool_calls":m.tool_calls,"tool_call_id":m.tool_call_id,"status":m.status,"turn_id":str(m.turn_id)} for m in group]; used+=size
        return output
async def conversation_context(conversation_id: str, credential: str, *, l1_bytes: int = layers.L1_BYTES,
                               l2_bytes: int = layers.L2_BYTES, l3_bytes: int = layers.L3_BYTES):
    """What the next turn of this conversation carries of the turns before it, by layer (the rule is
    ``agent_context.layer_rows``; this reads the rows and the segments).  None when the conversation
    is not this credential's."""
    if not await verify_conversation(conversation_id, credential):
        return None
    cid = uuid.UUID(conversation_id)
    async with Session() as s:
        rows = (await s.scalars(select(AgentMessage).where(AgentMessage.conversation_id == cid)
                                .order_by(AgentMessage.sequence.desc()).limit(500))).all()
        segments = (await s.scalars(select(AgentConversationSummary)
                                    .where(AgentConversationSummary.conversation_id == cid)
                                    .order_by(AgentConversationSummary.seq.asc()))).all()
    stored = [{"role": m.role, "content": m.content, "tool_calls": m.tool_calls, "tool_call_id": m.tool_call_id,
               "status": m.status, "turn_id": m.turn_id, "sequence": m.sequence} for m in reversed(rows)]
    made = [{"seq": x.seq, "from_sequence": x.from_sequence, "upto_sequence": x.upto_sequence, "content": x.content}
            for x in segments]
    return layers.layer_rows(stored, made, l1_bytes=l1_bytes, l2_bytes=l2_bytes, l3_bytes=l3_bytes)


async def load_turn_citation(conversation_id: str, credential: str, turn_id: str, ordinal: int) -> dict:
    """Read one immutable citation manifest entry from an owned final answer."""
    try:
        cid, tid = uuid.UUID(conversation_id), uuid.UUID(turn_id)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            return {"ok": False, "error_code": "history_citation_invalid"}
    except (ValueError, TypeError, AttributeError):
        return {"ok": False, "error_code": "history_citation_not_found"}
    async with Session() as s:
        owner = await s.scalar(select(AgentConversation.id).where(
            AgentConversation.id == cid, AgentConversation.owner_hash == owner_hash(credential)))
        if owner is None: return {"ok": False, "error_code": "history_citation_not_found"}
        rows = (await s.scalars(select(AgentMessage).where(AgentMessage.conversation_id == cid,
            AgentMessage.turn_id == tid, AgentMessage.role == "assistant").order_by(AgentMessage.sequence))).all()
    finals = [m for m in rows if m.status == "complete" and m.content and not (
        isinstance(m.tool_calls, dict) and m.tool_calls.get("calls"))]
    if not finals: return {"ok": False, "error_code": "history_citation_not_found"}
    if len(finals) != 1: return {"ok": False, "error_code": "history_citation_invalid"}
    manifest = finals[0].tool_calls.get("citations", []) if isinstance(finals[0].tool_calls, dict) else []
    if not isinstance(manifest, list): return {"ok": False, "error_code": "history_citation_invalid"}
    candidate_matches = [c for c in manifest if isinstance(c, dict) and c.get("ordinal") == ordinal]
    if any(isinstance(c.get("ordinal"), bool) or not isinstance(c.get("ordinal"), int) for c in candidate_matches):
        return {"ok": False, "error_code": "history_citation_invalid"}
    matches = candidate_matches
    if len(matches) > 1: return {"ok": False, "error_code": "history_citation_invalid"}
    if not matches: return {"ok": False, "error_code": "history_citation_not_found"}
    if (not isinstance(matches[0].get("source_type"), str) or not isinstance(matches[0].get("title"), str)
            or not isinstance(matches[0].get("quote"), str)):
        return {"ok": False, "error_code": "history_citation_invalid"}
    return {"ok": True, "citation": dict(matches[0]), "origin": {"turn_id": str(tid), "ordinal": ordinal}}


async def append_summary(conversation_id: str, from_sequence: int, upto_sequence: int, content: str) -> bool:
    """Append the next summary segment.  False when it would not move the covered range forward
    (another task got there first) or when the segment number was taken in the meantime."""
    cid = uuid.UUID(conversation_id)
    async with Session() as s:
        last = (await s.execute(select(AgentConversationSummary.seq, AgentConversationSummary.upto_sequence)
                                .where(AgentConversationSummary.conversation_id == cid)
                                .order_by(AgentConversationSummary.seq.desc()).limit(1))).first()
        if last is not None and upto_sequence <= last.upto_sequence:
            return False
        s.add(AgentConversationSummary(conversation_id=cid, seq=(last.seq if last else 0) + 1,
                                       from_sequence=from_sequence, upto_sequence=upto_sequence, content=content))
        seq=(last.seq if last else 0) + 1
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            return False
    await _recall(recall.index_summary, conversation_id, seq, content)
    return True


def _eligible_turn_groups(rows):
    groups=[]
    for row in rows:
        if not groups or groups[-1][-1].turn_id!=row.turn_id: groups.append([])
        groups[-1].append(row)
    return [group for group in groups
            if group and group[0].role=="user" and group[-1].role=="assistant"
            and group[-1].status in {"complete","failed","cancelled"}]


def _select_message_page(groups, before_sequence=None, limit_turns=20):
    """Choose complete turns for the browser, newest first.

    The browser history is not the model context: it has no total byte budget.  A page
    is bounded by whole turns so a restored answer always carries its own tool rows;
    before_sequence is the exclusive cursor returned as next_before.
    """
    if before_sequence is not None:
        groups=[group for group in groups if group[-1].sequence < before_sequence]
    if limit_turns is None:
        selected=groups
    else:
        selected=groups[-max(1,min(100,int(limit_turns))):]
    has_more=len(groups)>len(selected)
    next_before=selected[0][0].sequence if has_more and selected else None
    return selected,has_more,next_before


async def owned_message_page(conversation_id: str, credential: str, *, before_sequence=None, limit_turns=20):
    """One page of complete turns for the browser, independent of the model context budget."""
    if not await verify_conversation(conversation_id,credential): return None
    async with Session() as s:
        rows=(await s.scalars(select(AgentMessage)
              .where(AgentMessage.conversation_id==uuid.UUID(conversation_id))
              .order_by(AgentMessage.sequence.asc()))).all()
    selected,has_more,next_before=_select_message_page(_eligible_turn_groups(rows),before_sequence,limit_turns)
    return {"messages":[{"role":m.role,"content":m.content,"tool_calls":m.tool_calls,"tool_call_id":m.tool_call_id,
                         "status":m.status,"turn_id":str(m.turn_id)} for group in selected for m in group],
            "has_more":has_more,"next_before":next_before}


async def owned_messages(conversation_id: str,credential: str):
    """All complete turns for legacy callers; the browser uses owned_message_page."""
    page=await owned_message_page(conversation_id,credential,limit_turns=None)
    return None if page is None else page["messages"]

def ensure_conversation_archive_column() -> None:
    """Add the archive column to an existing table.  A fresh database gets it from the model.

    This module's own engine is the async one; startup DDL must use the synchronous engine.
    """
    from sqlalchemy import text
    from .database import engine as sync_engine
    with sync_engine.begin() as connection:
        connection.execute(text("ALTER TABLE agent_conversations ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ"))


async def set_conversation_archived(credential: str, conversation_id: str, archived: bool) -> bool:
    """Archive (hide) or restore one of this credential's conversations.  False when it is not theirs."""
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError:
        return False
    async with Session() as s:
        owned = await s.scalar(select(AgentConversation.id).where(
            AgentConversation.id == cid, AgentConversation.owner_hash == owner_hash(credential)))
        if owned is None:
            return False
        row = await s.get(AgentConversation, cid)
        row.archived_at = func.now() if archived else None
        await s.commit()
        return True


async def finish_conversation(conversation_id: str, status: str):
    async with Session() as s:
        conv=await s.get(AgentConversation,uuid.UUID(conversation_id))
        if conv:
            conv.status=status
            await s.commit()

async def list_conversations(credential: str, limit: int = 20, offset: int = 0, include_archived: bool = False):
    """Return only conversations owned by this credential, with a bounded title lookup: the name the
    owner gave it, else the first question.  Archived conversations are hidden unless asked for."""
    async with Session() as s:
        first_question = (select(AgentMessage.content)
            .where(AgentMessage.conversation_id == AgentConversation.id, AgentMessage.role == "user")
            .order_by(AgentMessage.sequence.asc()).limit(1).scalar_subquery())
        rows = (await s.execute(
            select(AgentConversation.id, AgentConversation.created_at, AgentConversation.updated_at, first_question,
                   AgentConversationTitle.title)
            .outerjoin(AgentConversationTitle, AgentConversationTitle.conversation_id == AgentConversation.id)
            .where(AgentConversation.owner_hash == owner_hash(credential))
            .where(AgentConversation.archived_at.is_(None) if not include_archived else true())
            .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
            .offset(offset).limit(limit)
        )).all()
        output = []
        for cid, created_at, updated_at, title, custom in rows:
            output.append({"id": str(cid), "created_at": created_at.isoformat(), "updated_at": updated_at.isoformat(),
                           "title": (custom or title or "新建对话")[:120], "custom_title": bool(custom)})
        return output


async def set_conversation_title(credential: str, conversation_id: str, title: str | None) -> bool:
    """Name (or, with None, un-name) one of this owner's conversations.  False when it is not theirs."""
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError:
        return False
    async with Session() as s:
        owned = await s.scalar(select(AgentConversation.id).where(
            AgentConversation.id == cid, AgentConversation.owner_hash == owner_hash(credential)))
        if owned is None:
            return False
        row = await s.get(AgentConversationTitle, cid)
        if not title:
            if row is not None:
                await s.delete(row)
        elif row is None:
            s.add(AgentConversationTitle(conversation_id=cid, title=title))
        else:
            row.title = title
        await s.commit()
    await _recall(recall.index_title, conversation_id)
    return True


MAX_RESEARCH_QUEUE = 30  # the same bound as the front end's list


def clean_queue(symbols) -> list[str]:
    """Tickers in the form the workspace uses, each once, at most MAX_RESEARCH_QUEUE; anything else is dropped."""
    from .symbols import normalize_market_symbol
    seen: list[str] = []
    for value in symbols if isinstance(symbols, (list, tuple)) else []:
        try:
            symbol = normalize_market_symbol(value)
        except ValueError:
            continue
        if symbol not in seen:
            seen.append(symbol)
    return seen[:MAX_RESEARCH_QUEUE]


async def get_research_queue(credential: str) -> list[str] | None:
    """The owner's saved queue; None when none was ever saved (an empty list is a queue the owner emptied)."""
    async with Session() as s:
        row = await s.get(AgentResearchQueue, owner_hash(credential))
        return None if row is None else clean_queue(row.symbols)


async def put_research_queue(credential: str, symbols) -> list[str]:
    queue = clean_queue(symbols)
    async with Session() as s:
        row = await s.get(AgentResearchQueue, owner_hash(credential))
        if row is None:
            s.add(AgentResearchQueue(owner_hash=owner_hash(credential), symbols=queue))
        else:
            row.symbols = queue
        await s.commit()
    return queue

