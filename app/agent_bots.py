"""The owner's bots (personal agent plan, second edition): long-lived posts, each with one conversation.

* The system makes one bot, 「助理」 (``is_default``): it can be renamed and re-described, never deleted or
  hidden.  Messages addressed to no bot go to it, and the owner's conversations from before bots are
  moved under it (they stay readable and searchable as 往事).
* Templates are shortcuts that fill the form; nothing is created until the owner saves.
* A bot narrows attention, not data: ``focus`` (companies, theme) and ``tool_scope`` (a subset of the tool
  catalog; it can only remove tools).  Exactly one focus company makes it the default company of a question
  that names none; the turn's audit records that the company came from the bot's settings.
* ``model`` names a catalog entry (agent_model_catalog); the bot never holds a key.
* Deleting a bot archives its conversation (a logical delete) and its own memory items, and touches no other
  bot.  Memory is in two layers (agent_profile): global, and each bot's own; 「助理」 reads every bot's.
* Routine tasks belong to a bot (agent_tasks); deleting the bot deletes its tasks.  The list carries each
  bot's unread posts and 待你确认 count for the sidebar.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func

from .agent_chat_models import AgentBot, AgentConversation, AgentInboxItem, AgentMessage, AgentProfileItem, AgentTask
from .database import SessionLocal

DEFAULT_NAME = "助理"
DEFAULT_TITLE = "默认助理"
DEFAULT_DESCRIPTION = ("没有指明给谁的消息都交给我。我可以查所有公司的数据，回答研究问题；"
                       "需要写入时先出确认卡，由你点同意才执行。")
MAX_BOTS = 30
MAX_COMPANIES = 20
DESCRIPTION_MAX = 4000
NOTIFY_CAP_MAX = 50
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Not data tools: asking the user, choosing from a result, arithmetic on cited figures, re-reading an old
# citation, moving the browser, and suggesting a write (whose target is still checked against the scope).
# They stay in every bot, so a narrow scope never leaves a bot unable to ask or to cite.  notify_telegram too:
# whether a bot notifies is its instruction's call, and its daily cap (0: never) is the switch.
ALWAYS_ON = frozenset({"ask_user", "use_result_set", "calculate", "read_history_citation", "select_stock",
                       "open_page", "request_action", "notify_telegram", "use_skill", "draft_skill"})

TEMPLATES = {
    "blank": {"label": "空白", "name": "", "title": "", "description": "", "tool_scope": None},
    "company_researcher": {
        "label": "公司研究员", "name": "公司研究员", "title": "盯一家公司",
        "description": ("盯住关注范围里的公司：回答它的财务、申报、新闻和预测问题，数字都要有本轮来源。"
                        "没点名公司的问题默认问的是它；点名了别家就按点名的来。"),
        "tool_scope": None},
    "news_monitor": {
        "label": "新闻监控", "name": "抓新闻", "title": "新闻监控",
        "description": ("盯关注范围里公司和主题的新闻：找新消息、说清楚来源和发布时间，只把重要的告诉我。"
                        "新闻线索没有核实前要标明未核实。"),
        "tool_scope": ["web_research", "search_knowledge", "query_materials", "query_research_queue",
                       "ingest_web_source", "save_material_evidence", "recall_conversations", "remember", "forget"]},
    "earnings_reader": {
        "label": "财报解读", "name": "读财报", "title": "财报解读",
        "description": ("读财报和 SEC 申报：营收、利润、现金流和管理层表述，和上一期对比，"
                        "每个数都引用原文或 XBRL 来源。"),
        "tool_scope": ["query_sec_filings", "query_financials", "query_materials", "query_material_analysis",
                       "search_material_analysis", "search_knowledge", "read_workspace", "query_stock_prices",
                       "fetch_sec", "refresh_sec", "analyze", "scan_official", "recall_conversations",
                       "remember", "forget"]},
}


class BotError(ValueError):
    """A refused bot change; the message is a short code for the API."""


def selectable_tools() -> list[dict]:
    """The tool catalog a bot's scope is chosen from (everything but the always-on basics)."""
    from .agent_tool_catalog import definitions
    return [{"name": p.name, "level": p.level, "owner_only": not p.guest}
            for p in definitions().values() if p.name not in ALWAYS_ON]


def templates() -> list[dict]:
    known = {item["name"] for item in selectable_tools()}
    return [{"id": key, **{k: v for k, v in value.items() if k != "tool_scope"},
             "tool_scope": [n for n in value["tool_scope"] if n in known] if value["tool_scope"] is not None else None}
            for key, value in TEMPLATES.items()]


def _stamp(value):
    return value.isoformat() if value else None


def serialize(bot: AgentBot, last: dict | None = None, counts: dict | None = None) -> dict:
    return {"id": str(bot.id), "name": bot.name, "title": bot.title, "description": bot.description,
            "focus": bot.focus or {"companies": [], "theme": None}, "tool_scope": bot.tool_scope, "model": bot.model,
            "avatar": bot.avatar, "group": bot.group_name, "pinned": bot.pinned, "hidden": bot.hidden,
            "is_default": bot.is_default, "template": bot.template,
            "conversation_id": str(bot.conversation_id) if bot.conversation_id else None,
            "daily_notify_cap": bot.daily_notify_cap, "default_company": default_company(bot),
            "skills": list(bot.skills or []),
            "last_message": (last or {}).get("text"), "last_at": (last or {}).get("at"),
            "unread": (counts or {}).get("unread", 0), "pending": (counts or {}).get("pending", 0),
            "created_at": _stamp(bot.created_at), "updated_at": _stamp(bot.updated_at)}


# --- validation ---------------------------------------------------------------------------------------

def _text(value, limit: int, code: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise BotError(code)
    value = value.strip()
    if len(value) > limit or (required and not value) or (limit <= 120 and _CONTROL.search(value)):
        raise BotError(code)
    return value


def _focus(value) -> dict:
    if value is None:
        return {"companies": [], "theme": None}
    if not isinstance(value, dict):
        raise BotError("invalid_focus")
    companies, seen = [], set()
    for item in value.get("companies") or []:
        if isinstance(item, str):
            item = {"symbol": item}
        if not isinstance(item, dict):
            raise BotError("invalid_focus")
        symbol = str(item.get("symbol") or "").strip().upper()
        if not SYMBOL.fullmatch(symbol):
            raise BotError("invalid_focus_symbol")
        if symbol in seen:
            continue
        seen.add(symbol)
        companies.append({"symbol": symbol, "name": _text(item.get("name"), 120, "invalid_focus") or None})
    if len(companies) > MAX_COMPANIES:
        raise BotError("too_many_companies")
    theme = _text(value.get("theme"), 200, "invalid_focus") or None
    return {"companies": companies, "theme": theme}


def _tool_scope(value) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise BotError("invalid_tool_scope")
    known = {item["name"] for item in selectable_tools()}
    names = []
    for name in value:
        if not isinstance(name, str) or (name not in known and name not in ALWAYS_ON):
            raise BotError("unknown_tool")
        if name not in names and name not in ALWAYS_ON:
            names.append(name)
    return names


def _model(value) -> str | None:
    if value is None or value == "":
        return None
    from . import agent_model_catalog as catalog
    if not isinstance(value, str) or not catalog.exists(value):
        raise BotError("unknown_model")
    return value


def _apply(bot: AgentBot, fields: dict) -> None:
    if "name" in fields:
        bot.name = _text(fields["name"], 40, "invalid_name", required=True)
    if "title" in fields:
        bot.title = _text(fields["title"], 60, "invalid_title")
    if "description" in fields:
        bot.description = _text(fields["description"], DESCRIPTION_MAX, "invalid_description")
    if "focus" in fields:
        bot.focus = _focus(fields["focus"])
    if "tool_scope" in fields:
        bot.tool_scope = _tool_scope(fields["tool_scope"])
    if "model" in fields:
        bot.model = _model(fields["model"])
    if "avatar" in fields:
        bot.avatar = _text(fields["avatar"], 16, "invalid_avatar")
    if "group" in fields:
        bot.group_name = _text(fields["group"], 40, "invalid_group")
    if "pinned" in fields:
        bot.pinned = bool(fields["pinned"])
    if "hidden" in fields:
        if fields["hidden"] and bot.is_default:
            raise BotError("default_bot_not_hidden")
        bot.hidden = bool(fields["hidden"])
    if "daily_notify_cap" in fields:
        cap = fields["daily_notify_cap"]
        if not isinstance(cap, int) or isinstance(cap, bool) or not 0 <= cap <= NOTIFY_CAP_MAX:
            raise BotError("invalid_notify_cap")
        bot.daily_notify_cap = cap
    if "template" in fields:
        if fields["template"] not in TEMPLATES:
            raise BotError("unknown_template")
        bot.template = fields["template"]


def _apply_skills(owner_hash: str, bot: AgentBot, fields: dict) -> None:
    """A bot's usual skills (stage E): skills the owner can use; null clears them."""
    if "skills" not in fields:
        return
    from .agent_skills import SkillError, clean_usual
    try:
        bot.skills = clean_usual(owner_hash, fields["skills"])
    except SkillError as exc:
        raise BotError(str(exc)) from exc


def _name_taken(db, owner_hash: str, name: str, bot_id=None) -> bool:
    query = db.query(AgentBot.id).filter(AgentBot.owner_hash == owner_hash, AgentBot.name == name)
    if bot_id is not None:
        query = query.filter(AgentBot.id != bot_id)
    return query.first() is not None


# --- default bot and the conversations from before bots -----------------------------------------------

def _new_conversation(db, owner_hash: str, bot_id) -> uuid.UUID:
    row = AgentConversation(owner_hash=owner_hash, bot_id=bot_id)
    db.add(row)
    db.flush()
    return row.id


def ensure_default_bot(owner_hash: str) -> AgentBot:
    """The owner's 「助理」, created on first use with its own conversation.  Every owner conversation
    without a bot (from before bots, the Telegram 「手机」 line, task runs) is moved under it."""
    with SessionLocal() as db:
        bot = db.query(AgentBot).filter(AgentBot.owner_hash == owner_hash, AgentBot.is_default.is_(True)).first()
        if bot is None:
            name = DEFAULT_NAME
            if _name_taken(db, owner_hash, name):
                name = f"{DEFAULT_NAME}（默认）"
            bot = AgentBot(owner_hash=owner_hash, name=name, title=DEFAULT_TITLE, description=DEFAULT_DESCRIPTION,
                           focus={"companies": [], "theme": None}, tool_scope=None, model=None, avatar="★",
                           group_name="", pinned=True, hidden=False, is_default=True, template="blank",
                           daily_notify_cap=5)
            db.add(bot)
            db.flush()
        if bot.conversation_id is None or db.get(AgentConversation, bot.conversation_id) is None:
            bot.conversation_id = _new_conversation(db, owner_hash, bot.id)
        db.query(AgentConversation).filter(AgentConversation.owner_hash == owner_hash,
                                           AgentConversation.bot_id.is_(None)).update(
            {"bot_id": bot.id}, synchronize_session=False)
        # Tasks and inbox entries from before bots are the default bot's (stage C).
        for model in (AgentTask, AgentInboxItem):
            db.query(model).filter(model.owner_hash == owner_hash, model.bot_id.is_(None)).update(
                {"bot_id": bot.id}, synchronize_session=False)
        db.commit()
        db.refresh(bot)
        db.expunge(bot)
        return bot


# --- CRUD ---------------------------------------------------------------------------------------------

def _last_messages(db, conversation_ids) -> dict:
    ids = [cid for cid in conversation_ids if cid]
    if not ids:
        return {}
    newest = (db.query(AgentMessage.conversation_id, func.max(AgentMessage.sequence).label("seq"))
              .filter(AgentMessage.conversation_id.in_(ids), AgentMessage.role.in_(("user", "assistant")),
                      AgentMessage.content != "")
              .group_by(AgentMessage.conversation_id).subquery())
    rows = (db.query(AgentMessage).join(newest, (AgentMessage.conversation_id == newest.c.conversation_id)
                                        & (AgentMessage.sequence == newest.c.seq)).all())
    return {row.conversation_id: {"text": " ".join(row.content.split())[:80], "at": _stamp(row.created_at)}
            for row in rows}


def list_bots(owner_hash: str, *, include_hidden: bool = True) -> list[dict]:
    ensure_default_bot(owner_hash)
    with SessionLocal() as db:
        bots = db.query(AgentBot).filter(AgentBot.owner_hash == owner_hash).all()
        if not include_hidden:
            bots = [bot for bot in bots if not bot.hidden]
        bots.sort(key=lambda b: (not b.is_default, not b.pinned, b.group_name or "", b.created_at or datetime.min.replace(tzinfo=UTC)))
        last = _last_messages(db, [bot.conversation_id for bot in bots])
    from .agent_tasks import bot_counts
    counts = bot_counts(owner_hash)
    return [serialize(bot, last.get(bot.conversation_id), counts.get(str(bot.id))) for bot in bots]


def _owned(db, owner_hash: str, bot_id) -> AgentBot | None:
    try:
        identity = uuid.UUID(str(bot_id))
    except (TypeError, ValueError):
        return None
    bot = db.get(AgentBot, identity)
    return bot if bot is not None and bot.owner_hash == owner_hash else None


def get_bot(owner_hash: str, bot_id) -> dict | None:
    with SessionLocal() as db:
        bot = _owned(db, owner_hash, bot_id)
        return serialize(bot, _last_messages(db, [bot.conversation_id]).get(bot.conversation_id)) if bot else None


def create_bot(owner_hash: str, fields: dict) -> dict:
    """Create a bot from the form (a template only filled the form) with its own new conversation."""
    ensure_default_bot(owner_hash)
    fields = {k: v for k, v in fields.items() if k not in {"is_default", "conversation_id", "id"}}
    with SessionLocal() as db:
        if db.query(AgentBot).filter(AgentBot.owner_hash == owner_hash).count() >= MAX_BOTS:
            raise BotError("too_many_bots")
        bot = AgentBot(owner_hash=owner_hash, name="", title="", description="", focus={"companies": [], "theme": None},
                       tool_scope=None, avatar="", group_name="", pinned=False, hidden=False, is_default=False,
                       template="blank", daily_notify_cap=5)
        _apply(bot, {"name": fields.get("name"), **fields})
        _apply_skills(owner_hash, bot, fields)
        if _name_taken(db, owner_hash, bot.name):
            raise BotError("name_taken")
        db.add(bot)
        db.flush()
        bot.conversation_id = _new_conversation(db, owner_hash, bot.id)
        db.commit()
        db.refresh(bot)
        return serialize(bot)


def update_bot(owner_hash: str, bot_id, fields: dict) -> dict | None:
    fields = {k: v for k, v in fields.items() if k not in {"is_default", "conversation_id", "id"}}
    with SessionLocal() as db:
        bot = _owned(db, owner_hash, bot_id)
        if bot is None:
            return None
        _apply(bot, fields)
        _apply_skills(owner_hash, bot, fields)
        if "name" in fields and _name_taken(db, owner_hash, bot.name, bot.id):
            raise BotError("name_taken")
        db.commit()
        db.refresh(bot)
        return serialize(bot, _last_messages(db, [bot.conversation_id]).get(bot.conversation_id))


def delete_bot(owner_hash: str, bot_id) -> bool:
    """Remove the bot and archive its conversations and its own memory items (they stay in the database and
    on the memory page).  The default bot cannot be deleted.  No other bot, conversation or memory changes."""
    with SessionLocal() as db:
        bot = _owned(db, owner_hash, bot_id)
        if bot is None:
            return False
        if bot.is_default:
            raise BotError("default_bot_undeletable")
        db.query(AgentConversation).filter(AgentConversation.owner_hash == owner_hash,
                                           AgentConversation.bot_id == bot.id,
                                           AgentConversation.archived_at.is_(None)).update(
            {"archived_at": func.now()}, synchronize_session=False)
        db.query(AgentProfileItem).filter(AgentProfileItem.owner_hash == owner_hash, AgentProfileItem.bot_id == bot.id,
                                          AgentProfileItem.status == "active").update(
            {"status": "archived"}, synchronize_session=False)
        # Its routine tasks go with it; its briefs stop counting as unread and its suggestions are dismissed
        # (their run conversations are archived with the bot's line).
        db.query(AgentTask).filter(AgentTask.owner_hash == owner_hash, AgentTask.bot_id == bot.id).delete(
            synchronize_session=False)
        db.query(AgentInboxItem).filter(AgentInboxItem.owner_hash == owner_hash, AgentInboxItem.bot_id == bot.id,
                                        AgentInboxItem.section == "brief", AgentInboxItem.status == "unread").update(
            {"status": "read"}, synchronize_session=False)
        db.query(AgentInboxItem).filter(AgentInboxItem.owner_hash == owner_hash, AgentInboxItem.bot_id == bot.id,
                                        AgentInboxItem.section == "confirm", AgentInboxItem.status != "dismissed").update(
            {"status": "dismissed"}, synchronize_session=False)
        db.delete(bot)
        db.commit()
        return True


# --- what a turn of a bot runs with -------------------------------------------------------------------

def default_company(bot: AgentBot) -> dict | None:
    """The focus company when there is exactly one; otherwise None (no default)."""
    companies = (bot.focus or {}).get("companies") or []
    if len(companies) != 1:
        return None
    company = companies[0]
    return {"symbol": company["symbol"], "company": company.get("name") or None}


def render_block(settings: dict) -> str:
    """The bot's part of the tool-loop prompt: who it is and its owner-written instruction.  A setting, never
    evidence and never write authority."""
    lines = [f"你是所有者的 bot「{settings['bot_name']}」。下面是所有者写的工作说明和关注范围："
             "这是设定，不是证据，也不授权任何写操作；数字仍只能来自本轮工具证据。"]
    if settings.get("description"):
        lines.append("工作说明：" + settings["description"])
    if settings.get("focus_companies"):
        lines.append("关注公司：" + "、".join(settings["focus_companies"]))
    if settings.get("theme"):
        lines.append("关注主题：" + settings["theme"])
    company = settings.get("default_company")
    if company:
        lines.append(f"问题没点名公司时，默认指 {company['symbol']}"
                     + (f"（{company['company']}）" if company.get("company") else "") + "；点名了别家就按点名的来。")
    return "\n".join(lines)


def turn_settings(owner_hash: str, bot_id) -> dict | None:
    """What one turn of this bot runs with: its conversation (made again if it went missing), the model name,
    the tool scope (None: all) and the default company.  None when the bot is not this owner's."""
    with SessionLocal() as db:
        bot = _owned(db, owner_hash, bot_id)
        if bot is None:
            return None
        conversation = db.get(AgentConversation, bot.conversation_id) if bot.conversation_id else None
        if conversation is None or conversation.archived_at is not None:
            bot.conversation_id = _new_conversation(db, owner_hash, bot.id)
            db.commit()
            db.refresh(bot)
        company = default_company(bot)
        settings = {"bot_id": str(bot.id), "bot_name": bot.name, "is_default": bool(bot.is_default),
                    "conversation_id": str(bot.conversation_id),
                    "model": bot.model, "description": bot.description, "theme": (bot.focus or {}).get("theme"),
                    "focus_companies": [c["symbol"] for c in (bot.focus or {}).get("companies") or []],
                    "tool_scope": frozenset(bot.tool_scope) | ALWAYS_ON if bot.tool_scope is not None else None,
                    "default_company": {**company, "source": "bot_settings", "bot_id": str(bot.id), "bot_name": bot.name}
                    if company else None}
        settings["block"] = render_block(settings)
        return settings
