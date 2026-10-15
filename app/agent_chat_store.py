import hashlib, uuid, json
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from .config import DATABASE_URL
from .agent_chat_models import AgentConversation, AgentConversationSummary, AgentConversationTitle, AgentMessage
from . import agent_context as layers
ASYNC_URL=DATABASE_URL
engine=create_async_engine(ASYNC_URL,pool_pre_ping=True,connect_args={"connect_timeout":3})
Session=async_sessionmaker(engine,expire_on_commit=False)
def owner_hash(credential: str)->str: return hashlib.sha256(credential.encode()).hexdigest()
def _message_bytes(m):
    value={"role":m.role,"content":m.content,"tool_calls":m.tool_calls,"tool_call_id":m.tool_call_id,"status":m.status,"turn_id":str(m.turn_id)}
    return len(json.dumps(value,ensure_ascii=False,separators=(",",":")).encode("utf-8"))
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
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            return False
        return True


async def owned_messages(conversation_id: str,credential: str):
    if not await verify_conversation(conversation_id,credential): return None
    async with Session() as s:
        rows=(await s.scalars(select(AgentMessage).where(AgentMessage.conversation_id==uuid.UUID(conversation_id)).order_by(AgentMessage.sequence.desc()).limit(500))).all()
        rows=list(reversed(rows)); groups=[]
        for row in rows:
            if not groups or groups[-1][0]!=row.turn_id: groups.append((row.turn_id,[]))
            groups[-1][1].append(row)
        selected=[]; used=0
        for _,group in reversed(groups):
            if not group or group[0].role!="user" or group[-1].role!="assistant" or group[-1].status not in {"complete","failed","cancelled"}: continue
            size=sum(_message_bytes(m) for m in group)
            if used+size>100000: break
            selected[0:0]=group; used+=size
        return [{"role":m.role,"content":m.content,"tool_calls":m.tool_calls,"tool_call_id":m.tool_call_id,"status":m.status,"turn_id":str(m.turn_id)} for m in selected]

async def finish_conversation(conversation_id: str, status: str):
    async with Session() as s:
        conv=await s.get(AgentConversation,uuid.UUID(conversation_id))
        if conv:
            conv.status=status
            await s.commit()

async def list_conversations(credential: str, limit: int = 20, offset: int = 0):
    """Return only conversations owned by this credential, with a bounded title lookup: the name the
    owner gave it, else the first question."""
    async with Session() as s:
        first_question = (select(AgentMessage.content)
            .where(AgentMessage.conversation_id == AgentConversation.id, AgentMessage.role == "user")
            .order_by(AgentMessage.sequence.asc()).limit(1).scalar_subquery())
        rows = (await s.execute(
            select(AgentConversation.id, AgentConversation.created_at, AgentConversation.updated_at, first_question,
                   AgentConversationTitle.title)
            .outerjoin(AgentConversationTitle, AgentConversationTitle.conversation_id == AgentConversation.id)
            .where(AgentConversation.owner_hash == owner_hash(credential))
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
        return True
