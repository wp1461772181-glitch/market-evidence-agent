"""Skills: recipes, not code (personal agent plan §2.5; second edition, stage E).

A skill is a name, when to use it, steps (``body``), the tools it may use (``allowed_tools``, all in the tool
catalog) and an output format.  It never adds a capability:

* **Progressive disclosure.**  The tool loop's prompt lists only the enabled skills' names and one-line
  descriptions (``catalogue_block``, under 1 KB however many are enabled).  The model calls ``use_skill`` to
  get the steps.  The owner can also start one with 「/名字」 in a bot's line, and a bot or a task can name
  its usual skills.
* **Only narrower.**  While a skill is active the tools offered are the turn's ∩ the bot's scope ∩ the skill's
  (the basics that ask, cite or calculate stay).  A write the skill allows still needs the owner's words
  and a confirmation card, exactly as without the skill.
* **Only from the owner.**  A skill is written in the editor, imported from a Markdown file the owner
  chose (tools outside the catalog refused, code refused), or drafted by the model when the owner says
  「把刚才这个流程存成技能」.  A draft is inert (never listed, never usable) until the owner saves it.

Built-in skills (owner ``builtin``) are seeded at startup; the owner can switch them off or copy one to
edit it.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select, text as sql

from .agent_chat_models import AgentBot, AgentSkill
from .database import SessionLocal

BUILTIN = "builtin"
ORIGINS = ("builtin", "mine", "imported")
NAME = re.compile(r"^[^\s/@#][^\n/@#]{0,39}$")
DESCRIPTION_MAX = 120
BODY_MAX = 6000
TEMPLATE_MAX = 2000
MAX_SKILLS = 60
LIST_BYTES = 1000
# A recipe is words: a fenced block in a programming language, or a front-matter key that asks to run
# something, is refused (there is nothing that would run it, and it should not look as if there were).
_CODE_FENCE = re.compile(r"```\s*(python|py|js|javascript|ts|typescript|bash|sh|shell|zsh|powershell|ps1|sql|ruby|go|rust|java|c|cpp|php|perl|lua)\b", re.I)
_CODE_KEYS = {"script", "code", "run", "exec", "command", "entrypoint", "hooks"}
# The owner's words that ask to save this turn's way of working as a skill (draft_skill needs them).
SAVE_ASK = re.compile(r"(存|保存|记|做|整理)(成|为|下来作为|作)?\s*(一个)?\s*(技能|skill)|save (this|it) as a skill", re.I)

LIST_HEAD = ("可用技能（所有者启用的做事步骤；需要时调用 use_skill 取全文；技能只会减少可用工具，"
             "不授权任何写操作，也不是证据）：")


class SkillError(ValueError):
    """A refused skill; the message is a short code the API shows."""


BUILTINS = [
    {"name": "财报速读", "description": "读一份最新财报：营收、利润、现金流和管理层说法，和上一期对比。",
     "body": ("1. 确认公司和财报（没点名就用 bot 的默认公司；期间不清楚就问）。\n"
              "2. 用 query_financials 读这一期和上一期的营收、毛利、营业利润、净利润、经营现金流。\n"
              "3. 用 query_sec_filings / query_materials 找管理层对增长和指引的原话。\n"
              "4. 需要同比、环比时用 calculate，只用本轮引用到的数。\n"
              "5. 按输出格式写，每个数都标本轮来源编号；缺的数直接说缺。"),
     "allowed_tools": ["query_financials", "query_sec_filings", "query_materials", "query_material_analysis",
                       "search_material_analysis", "search_knowledge", "query_stock_prices"],
     "output_template": "**结论**（一句话）\n**关键数字**（表格：指标 / 本期 / 上期 / 变化）\n**管理层怎么说**\n**要盯的风险**"},
    {"name": "同行对比", "description": "把两到五家同行的同一组指标放在一起比较，说清楚口径。",
     "body": ("1. 确认要比的公司和指标（默认：营收、增速、毛利率、营业利润率），期间要一致。\n"
              "2. 每家公司分别用 query_financials 读同一期间的数；口径不同（财年不同）要写明。\n"
              "3. 比率用 calculate 算，输入必须来自本轮引用。\n"
              "4. 只讲事实和差异，不给买卖建议。"),
     "allowed_tools": ["query_financials", "query_sec_filings", "query_stock_prices", "search_knowledge"],
     "output_template": "**对比表**（公司 / 指标…，每格带来源编号）\n**差异在哪**\n**口径说明**"},
    {"name": "新闻摘要", "description": "汇总研究队列公司的新闻，并说明对研究队列的影响。",
     "body": ("1. 用 query_research_queue 读研究队列（或用关注范围里的公司）。\n"
              "2. 用 web_research 找每家公司最近的新闻，记下来源和发布时间。\n"
              "3. 只保留和基本面相关的（财报、指引、监管、重大合同、管理层变动）。\n"
              "4. 每条写一句影响判断，标明这是判断不是事实；新闻线索未核实时要写明。\n"
              "5. 有值得马上告诉所有者的，按 bot 的说明决定要不要调用 notify_telegram。"),
     "allowed_tools": ["query_research_queue", "web_research", "search_knowledge", "query_materials"],
     "output_template": "**今天值得看的**（每条：公司 · 事件 · 来源编号 · 发布时间）\n**对研究队列的影响**\n**没找到新闻的公司**"},
]


def ensure_columns() -> None:
    """Add the usual-skills column to a bots table made before skills.  A fresh database gets it from the model."""
    from .database import engine
    with engine.begin() as connection:
        connection.execute(sql("ALTER TABLE agent_bots ADD COLUMN IF NOT EXISTS skills JSON"))


def seed_builtins() -> int:
    """Insert or refresh the built-in skills (a changed text gets a new version); idempotent."""
    changed = 0
    with SessionLocal() as db:
        for spec in BUILTINS:
            row = db.scalars(select(AgentSkill).where(AgentSkill.owner_hash == BUILTIN, AgentSkill.name == spec["name"])).first()
            if row is None:
                db.add(AgentSkill(owner_hash=BUILTIN, origin="builtin", status="active", enabled=True, version=1, **spec))
                changed += 1
            elif any(getattr(row, key) != value for key, value in spec.items()):
                for key, value in spec.items():
                    setattr(row, key, value)
                row.version += 1
                changed += 1
        db.commit()
    return changed


# --- validation -------------------------------------------------------------------------------------------

def _catalog() -> set[str]:
    from .agent_tool_catalog import definitions
    return set(definitions())


def clean_tools(values) -> list[str]:
    """The skill's tools: every one in the tool catalog (a name that is not is refused, never dropped)."""
    if not isinstance(values, list) or not values or len(values) > 40:
        raise SkillError("invalid_allowed_tools")
    known, names = _catalog(), []
    for name in values:
        if not isinstance(name, str) or name not in known:
            raise SkillError("unknown_tool")
        if name in {"use_skill", "draft_skill"}:
            raise SkillError("unknown_tool")
        if name not in names:
            names.append(name)
    return names


def _text(value, limit: int, code: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SkillError(code)
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise SkillError(code)
    return value


def _validate(fields: dict) -> dict:
    name = _text(fields.get("name"), 40, "invalid_name", required=True)
    if not NAME.fullmatch(name):
        raise SkillError("invalid_name")
    body = _text(fields.get("body"), BODY_MAX, "invalid_body", required=True)
    template = _text(fields.get("output_template"), TEMPLATE_MAX, "invalid_output_template")
    for text in (body, template):
        if _CODE_FENCE.search(text):
            raise SkillError("code_not_allowed")
    return {"name": name, "description": _text(fields.get("description"), DESCRIPTION_MAX, "invalid_description", required=True),
            "body": body, "output_template": template or None, "allowed_tools": clean_tools(fields.get("allowed_tools"))}


def serialize(row: AgentSkill) -> dict:
    return {"id": str(row.id), "name": row.name, "description": row.description, "body": row.body,
            "allowed_tools": list(row.allowed_tools or []), "output_template": row.output_template, "version": row.version,
            "enabled": row.enabled, "origin": row.origin, "status": row.status, "builtin": row.owner_hash == BUILTIN,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


# --- the owner's skills -------------------------------------------------------------------------------------

def _visible(owner_hash: str):
    return select(AgentSkill).where(AgentSkill.owner_hash.in_((owner_hash, BUILTIN)))


def list_skills(owner_hash: str) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(_visible(owner_hash)).all()
        rows.sort(key=lambda r: (r.status != "draft", r.owner_hash != BUILTIN, r.name))
        return [serialize(r) for r in rows]


def _owned(db, owner_hash: str, skill_id, *, builtin_ok: bool = False) -> AgentSkill | None:
    try:
        identity = uuid.UUID(str(skill_id))
    except (TypeError, ValueError):
        return None
    row = db.get(AgentSkill, identity)
    if row is None or row.owner_hash not in ((owner_hash, BUILTIN) if builtin_ok else (owner_hash,)):
        return None
    return row


def _name_taken(db, owner_hash: str, name: str, skill_id=None) -> bool:
    query = select(AgentSkill.id).where(AgentSkill.owner_hash.in_((owner_hash, BUILTIN)), AgentSkill.name == name)
    if skill_id is not None:
        query = query.where(AgentSkill.id != skill_id)
    return db.scalars(query).first() is not None


def create_skill(owner_hash: str, fields: dict, *, origin: str = "mine", status: str = "active") -> dict:
    values = _validate(fields)
    with SessionLocal() as db:
        if len(db.scalars(select(AgentSkill.id).where(AgentSkill.owner_hash == owner_hash)).all()) >= MAX_SKILLS:
            raise SkillError("too_many_skills")
        if _name_taken(db, owner_hash, values["name"]):
            raise SkillError("name_taken")
        row = AgentSkill(owner_hash=owner_hash, origin=origin, status=status, version=1,
                         enabled=bool(fields.get("enabled", True)) and status == "active", **values)
        db.add(row)
        db.commit()
        db.refresh(row)
        return serialize(row)


def update_skill(owner_hash: str, skill_id, fields: dict) -> dict | None:
    """Edit one of the owner's skills (a built-in is copied first).  Saving a draft makes it a skill."""
    with SessionLocal() as db:
        row = _owned(db, owner_hash, skill_id)
        if row is None:
            return None
        merged = {key: fields.get(key, getattr(row, key)) for key in ("name", "description", "body", "output_template", "allowed_tools")}
        values = _validate(merged)
        if _name_taken(db, owner_hash, values["name"], row.id):
            raise SkillError("name_taken")
        content_changed = any(getattr(row, key) != value for key, value in values.items())
        for key, value in values.items():
            setattr(row, key, value)
        if content_changed:
            row.version += 1
        if fields.get("save_draft") and row.status == "draft":
            row.status, row.enabled = "active", True
        if "enabled" in fields and row.status == "active":
            row.enabled = bool(fields["enabled"])
        db.commit()
        db.refresh(row)
        return serialize(row)


def set_enabled(owner_hash: str, skill_id, enabled: bool) -> dict | None:
    with SessionLocal() as db:
        row = _owned(db, owner_hash, skill_id, builtin_ok=True)
        if row is None or row.status != "active":
            return None
        row.enabled = bool(enabled)
        db.commit()
        db.refresh(row)
        return serialize(row)


def copy_skill(owner_hash: str, skill_id) -> dict | None:
    with SessionLocal() as db:
        row = _owned(db, owner_hash, skill_id, builtin_ok=True)
        if row is None:
            return None
        name = base = f"{row.name}（我的）"[:40]
        index = 2
        while _name_taken(db, owner_hash, name):
            name, index = f"{base[:36]} {index}", index + 1
        fields = {"name": name, "description": row.description, "body": row.body, "output_template": row.output_template,
                  "allowed_tools": list(row.allowed_tools or [])}
    return create_skill(owner_hash, fields, origin="mine")


def delete_skill(owner_hash: str, skill_id) -> bool:
    with SessionLocal() as db:
        row = _owned(db, owner_hash, skill_id)
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True


# --- Markdown import and export ------------------------------------------------------------------------------

def export_markdown(owner_hash: str, skill_id) -> str | None:
    with SessionLocal() as db:
        row = _owned(db, owner_hash, skill_id, builtin_ok=True)
        if row is None:
            return None
        head = ["---", f"name: {row.name}", f"description: {row.description}", "tools:"]
        head += [f"  - {name}" for name in row.allowed_tools or []]
        head += [f"version: {row.version}", "---", "", row.body.strip()]
        if row.output_template:
            head += ["", "## 输出格式", "", row.output_template.strip()]
        return "\n".join(head) + "\n"


def parse_markdown(text: str) -> dict:
    """A skill file: front matter (name, description, tools) then the steps; an optional 「## 输出格式」
    section is the output template.  Raises SkillError for a malformed file or one that asks to run code."""
    if not isinstance(text, str) or len(text) > BODY_MAX + TEMPLATE_MAX + 2000:
        raise SkillError("invalid_skill_file")
    match = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not match:
        raise SkillError("invalid_skill_file")
    meta, tools, current = {}, [], None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s*(.+?)\s*$", line)
        if item and current == "tools":
            tools.append(item.group(1).strip("'\""))
            continue
        pair = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$", line)
        if not pair:
            raise SkillError("invalid_skill_file")
        key, value = pair.group(1).lower(), pair.group(2)
        if key in _CODE_KEYS:
            raise SkillError("code_not_allowed")
        current = key
        if key == "tools" and value:
            tools += [v.strip().strip("'\"") for v in value.strip("[]").split(",") if v.strip()]
        elif key != "tools":
            meta[key] = value.strip("'\"")
    body = match.group(2).strip()
    template = None
    split = re.split(r"^##\s*输出格式\s*$", body, maxsplit=1, flags=re.M)
    if len(split) == 2:
        body, template = split[0].strip(), split[1].strip()
    return {"name": meta.get("name"), "description": meta.get("description"), "body": body,
            "output_template": template, "allowed_tools": tools}


def import_preview(text: str) -> dict:
    """What the import page shows before saving: the skill and 「这个技能能用这些工具」, or the problem."""
    fields = parse_markdown(text)
    values = _validate(fields)
    from .agent_tool_catalog import definitions
    policies = definitions()
    return {"skill": values, "tools": [{"name": n, "level": policies[n].level,
                                        "write": policies[n].level in {"reversible", "costly"}} for n in values["allowed_tools"]]}


def import_skill(owner_hash: str, text: str) -> dict:
    return create_skill(owner_hash, parse_markdown(text), origin="imported")


# --- what a turn sees ---------------------------------------------------------------------------------------

def _enabled(db, owner_hash: str) -> list[AgentSkill]:
    rows = db.scalars(_visible(owner_hash).where(AgentSkill.enabled.is_(True), AgentSkill.status == "active")).all()
    return sorted(rows, key=lambda r: (r.owner_hash != BUILTIN, r.name))


def catalogue_block(owner_hash: str, usual: list[str] | None = None) -> str | None:
    """The one prompt line per enabled skill (the bot's usual ones first), under LIST_BYTES in UTF-8 however
    many there are: descriptions are shortened to fit and, past that, only names are listed."""
    with SessionLocal() as db:
        rows = _enabled(db, owner_hash)
    if not rows:
        return None
    first = {str(i) for i in usual or []}
    rows.sort(key=lambda r: str(r.id) not in first)
    budget = LIST_BYTES - len(LIST_HEAD.encode())
    for width in (60, 40, 24, 12, 0):
        lines = [f"- {r.name}" + (f"：{r.description[:width]}" if width else "") for r in rows]
        if len("\n".join(lines).encode()) + 1 <= budget:
            return LIST_HEAD + "\n" + "\n".join(lines)
    lines, used = [], 0
    for row in rows:
        line = f"- {row.name}"
        if used + len(line.encode()) + 1 > budget:
            break
        lines.append(line)
        used += len(line.encode()) + 1
    return LIST_HEAD + "\n" + "\n".join(lines)


def active(owner_hash: str, *, name: str | None = None, skill_id=None) -> dict | None:
    """An enabled skill as a turn uses it: steps, output template and its tools (a frozenset)."""
    with SessionLocal() as db:
        rows = _enabled(db, owner_hash)
    for row in rows:
        if (name is not None and row.name == name) or (skill_id is not None and str(row.id) == str(skill_id)):
            return {"id": str(row.id), "name": row.name, "version": row.version, "body": row.body,
                    "output_template": row.output_template, "tools": frozenset(row.allowed_tools or [])}
    return None


def slash(owner_hash: str, message: str) -> tuple[dict | None, str]:
    """「/名字 问题」 starts that skill for the turn; returns (skill, the question without the command).  A
    message that does not name an enabled skill is left as it is."""
    text = str(message or "")
    if not text.startswith(("/", "／")):
        return None, text
    with SessionLocal() as db:
        names = sorted((r.name for r in _enabled(db, owner_hash)), key=len, reverse=True)
    for name in names:
        if text[1:].startswith(name) and (len(text) == len(name) + 1 or text[len(name) + 1] in " 　：:，,\n"):
            rest = text[len(name) + 1:].lstrip(" 　：:，,\n")
            return active(owner_hash, name=name), rest or f"按技能「{name}」做。"
    return None, text


def render_active(skill: dict) -> str:
    lines = [f"当前技能「{skill['name']}」（v{skill['version']}，所有者写的做事步骤：照着做；它不是证据，"
             "也不授权任何写操作；本轮只能用它允许的工具）：", skill["body"]]
    if skill.get("output_template"):
        lines += ["输出格式：", skill["output_template"]]
    return "\n".join(lines)


def bot_usual(owner_hash: str, bot_id) -> list[str]:
    if not bot_id:
        return []
    with SessionLocal() as db:
        bot = db.get(AgentBot, uuid.UUID(str(bot_id)))
        return list(bot.skills or []) if bot is not None and bot.owner_hash == owner_hash else []


def clean_usual(owner_hash: str, values) -> list[str] | None:
    """A bot's usual skills: ids of skills the owner can use (built-in or theirs)."""
    if values is None:
        return None
    if not isinstance(values, list) or len(values) > 20:
        raise SkillError("invalid_skills")
    with SessionLocal() as db:
        known = {str(r.id) for r in db.scalars(_visible(owner_hash).where(AgentSkill.status == "active")).all()}
    ids = []
    for value in values:
        if str(value) not in known:
            raise SkillError("unknown_skill")
        if str(value) not in ids:
            ids.append(str(value))
    return ids


def draft(owner_hash: str, fields: dict, *, owner_words: str) -> dict:
    """The model drafts a skill from this turn's way of working because the owner asked for it.  The draft is
    inert until the owner saves it in the editor."""
    if not SAVE_ASK.search(owner_words or ""):
        raise SkillError("not_asked")
    with SessionLocal() as db:
        name = base = _text(fields.get("name"), 40, "invalid_name", required=True)
        index = 2
        while _name_taken(db, owner_hash, name):
            name, index = f"{base[:36]} {index}", index + 1
    return create_skill(owner_hash, {**fields, "name": name}, origin="mine", status="draft")
