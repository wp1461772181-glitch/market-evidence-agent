"""Owner-only API for skills (agent_skills; personal agent plan §2.5, second edition stage E).

A rejected request is answered with a short code and never echoes what was sent."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import agent_skills as skills
from .agent_chat_store import owner_hash
from .agent_chat_stream import _owner_only

router = APIRouter(prefix="/agent-chat", tags=["agent-skills"])


class SkillBody(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=skills.DESCRIPTION_MAX)
    body: str | None = Field(default=None, max_length=skills.BODY_MAX)
    allowed_tools: list[str] | None = Field(default=None, max_length=40)
    output_template: str | None = Field(default=None, max_length=skills.TEMPLATE_MAX)
    enabled: bool | None = None
    # A draft becomes a skill only when the owner saves it here.
    save_draft: bool | None = None


class EnabledBody(BaseModel):
    enabled: bool


class ImportBody(BaseModel):
    markdown: str = Field(min_length=1, max_length=skills.BODY_MAX + skills.TEMPLATE_MAX + 2000)


def _error(exc: skills.SkillError) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("/skills")
async def list_skills_endpoint(x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    from .agent_tool_catalog import definitions
    tools = [{"name": p.name, "level": p.level} for p in definitions().values() if p.name not in {"use_skill", "draft_skill"}]
    return {"skills": await asyncio.to_thread(skills.list_skills, owner_hash(credential)), "tools": tools}


@router.post("/skills")
async def create_skill_endpoint(body: SkillBody, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    try:
        return {"skill": await asyncio.to_thread(skills.create_skill, owner_hash(credential), body.model_dump(exclude_none=True))}
    except skills.SkillError as exc:
        raise _error(exc)


@router.patch("/skills/{skill_id}")
async def update_skill_endpoint(skill_id: str, body: SkillBody, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    try:
        skill = await asyncio.to_thread(skills.update_skill, owner_hash(credential), skill_id, body.model_dump(exclude_none=True))
    except skills.SkillError as exc:
        raise _error(exc)
    if skill is None:
        raise HTTPException(404, "skill_not_found")
    return {"skill": skill}


@router.post("/skills/{skill_id}/enabled")
async def skill_enabled_endpoint(skill_id: str, body: EnabledBody, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    skill = await asyncio.to_thread(skills.set_enabled, owner_hash(credential), skill_id, body.enabled)
    if skill is None:
        raise HTTPException(404, "skill_not_found")
    return {"skill": skill}


@router.post("/skills/{skill_id}/copy")
async def copy_skill_endpoint(skill_id: str, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    try:
        skill = await asyncio.to_thread(skills.copy_skill, owner_hash(credential), skill_id)
    except skills.SkillError as exc:
        raise _error(exc)
    if skill is None:
        raise HTTPException(404, "skill_not_found")
    return {"skill": skill}


@router.delete("/skills/{skill_id}")
async def delete_skill_endpoint(skill_id: str, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    if not await asyncio.to_thread(skills.delete_skill, owner_hash(credential), skill_id):
        raise HTTPException(404, "skill_not_found")
    return {"deleted": True}


@router.get("/skills/{skill_id}/export")
async def export_skill_endpoint(skill_id: str, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    text = await asyncio.to_thread(skills.export_markdown, owner_hash(credential), skill_id)
    if text is None:
        raise HTTPException(404, "skill_not_found")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.post("/skills/import/preview")
async def import_preview_endpoint(body: ImportBody, x_chat_session: str | None = Header(default=None)):
    """What the file is and 「这个技能能用这些工具」, before anything is saved."""
    _owner_only(x_chat_session)
    try:
        return await asyncio.to_thread(skills.import_preview, body.markdown)
    except skills.SkillError as exc:
        raise _error(exc)


@router.post("/skills/import")
async def import_skill_endpoint(body: ImportBody, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    try:
        return {"skill": await asyncio.to_thread(skills.import_skill, owner_hash(credential), body.markdown)}
    except skills.SkillError as exc:
        raise _error(exc)
