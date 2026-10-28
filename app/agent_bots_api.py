"""Owner-only API for bots and the server's model catalog (personal agent plan, second edition).

No response carries a model key or any part of one: entries say only configured / missing.  A rejected
request is answered with a short code and never echoes what was sent (see main.safe_agent_chat_validation)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, SecretStr

from . import agent_bots as bots
from . import agent_model_catalog as catalog
from .agent_chat_store import owner_hash
from .agent_chat_stream import _owner_only

router = APIRouter(prefix="/agent-chat", tags=["agent-bots"])


class FocusCompany(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    name: str | None = Field(default=None, max_length=120)


class Focus(BaseModel):
    companies: list[FocusCompany] = Field(default_factory=list, max_length=bots.MAX_COMPANIES)
    theme: str | None = Field(default=None, max_length=200)


class BotBody(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=bots.DESCRIPTION_MAX)
    focus: Focus | None = None
    # null: every tool; a list: only these (the always-on basics stay).
    tool_scope: list[str] | None = Field(default=None, max_length=80)
    # null: the catalog's default model.
    model: str | None = Field(default=None, max_length=40)
    avatar: str | None = Field(default=None, max_length=16)
    group: str | None = Field(default=None, max_length=40)
    pinned: bool | None = None
    hidden: bool | None = None
    template: str | None = Field(default=None, max_length=20)
    daily_notify_cap: int | None = None
    # Usual skills (agent_skills ids); null clears them.
    skills: list[str] | None = Field(default=None, max_length=20)


class ModelEntryBody(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    display_name: str | None = Field(default=None, max_length=80)
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = None
    key_env: str | None = Field(default=None, max_length=64)
    is_default: bool | None = None


class DefaultModelBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)


def _fields(body: BotBody) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if "focus" in fields and fields["focus"] is not None:
        fields["focus"] = body.focus.model_dump()
    return fields


# --- bots -----------------------------------------------------------------------------------------------

@router.get("/bots")
async def list_bots_endpoint(x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    items = await asyncio.to_thread(bots.list_bots, owner_hash(credential))
    return {"bots": items, "default_bot_id": next((b["id"] for b in items if b["is_default"]), None)}


@router.post("/bots/{bot_id}/read")
async def mark_bot_read_endpoint(bot_id: str, x_chat_session: str | None = Header(default=None)):
    """The owner opened this bot's line: its posted briefs are no longer unread."""
    from .agent_tasks import mark_bot_read
    credential = _owner_only(x_chat_session)
    if await asyncio.to_thread(bots.get_bot, owner_hash(credential), bot_id) is None:
        raise HTTPException(404, "bot_not_found")
    return {"read": await asyncio.to_thread(mark_bot_read, owner_hash(credential), bot_id)}


@router.get("/bot-templates")
async def bot_templates_endpoint(x_chat_session: str | None = Header(default=None)):
    _owner_only(x_chat_session)
    return {"templates": bots.templates(), "tools": bots.selectable_tools(), "always_on": sorted(bots.ALWAYS_ON)}


@router.post("/bots")
async def create_bot_endpoint(body: BotBody, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    try:
        return {"bot": await asyncio.to_thread(bots.create_bot, owner_hash(credential), _fields(body))}
    except bots.BotError as exc:
        raise HTTPException(422, str(exc))


@router.get("/bots/{bot_id}")
async def get_bot_endpoint(bot_id: str, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    bot = await asyncio.to_thread(bots.get_bot, owner_hash(credential), bot_id)
    if bot is None:
        raise HTTPException(404, "bot_not_found")
    return {"bot": bot}


@router.patch("/bots/{bot_id}")
async def update_bot_endpoint(bot_id: str, body: BotBody, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    try:
        bot = await asyncio.to_thread(bots.update_bot, owner_hash(credential), bot_id, _fields(body))
    except bots.BotError as exc:
        raise HTTPException(422, str(exc))
    if bot is None:
        raise HTTPException(404, "bot_not_found")
    return {"bot": bot}


@router.delete("/bots/{bot_id}")
async def delete_bot_endpoint(bot_id: str, x_chat_session: str | None = Header(default=None)):
    credential = _owner_only(x_chat_session)
    try:
        deleted = await asyncio.to_thread(bots.delete_bot, owner_hash(credential), bot_id)
    except bots.BotError as exc:
        raise HTTPException(422, str(exc))
    if not deleted:
        raise HTTPException(404, "bot_not_found")
    return {"deleted": True}


# --- model catalog --------------------------------------------------------------------------------------

def _catalog_error(exc: catalog.CatalogError) -> HTTPException:
    return HTTPException(404 if str(exc) == "model_not_found" else 409 if str(exc) in {"model_in_use", "model_exists"} else 422,
                         str(exc))


@router.get("/model-catalog")
async def model_catalog_endpoint(x_chat_session: str | None = Header(default=None)):
    """Every model the server can use, with configured / missing for its key.  Never the key."""
    _owner_only(x_chat_session)
    return await asyncio.to_thread(catalog.list_public)


def _save(name: str, body: ModelEntryBody, create: bool) -> dict:
    return catalog.save_entry(name, display_name=body.display_name, provider=body.provider, model=body.model,
                              base_url=body.base_url, key_env=body.key_env, is_default=body.is_default,
                              api_key=body.api_key.get_secret_value() if body.api_key else None, create=create)


@router.post("/model-catalog")
async def add_model_endpoint(body: ModelEntryBody, x_chat_session: str | None = Header(default=None)):
    _owner_only(x_chat_session)
    try:
        return {"model": await asyncio.to_thread(_save, body.name or "", body, True)}
    except catalog.CatalogError as exc:
        raise _catalog_error(exc)


@router.patch("/model-catalog/{name}")
async def update_model_endpoint(name: str, body: ModelEntryBody, x_chat_session: str | None = Header(default=None)):
    _owner_only(x_chat_session)
    try:
        return {"model": await asyncio.to_thread(_save, name, body, False)}
    except catalog.CatalogError as exc:
        raise _catalog_error(exc)


@router.delete("/model-catalog/{name}")
async def delete_model_endpoint(name: str, x_chat_session: str | None = Header(default=None)):
    _owner_only(x_chat_session)
    try:
        await asyncio.to_thread(catalog.delete_entry, name)
    except catalog.CatalogError as exc:
        raise _catalog_error(exc)
    return {"deleted": True}


@router.put("/model-catalog/default")
async def default_model_endpoint(body: DefaultModelBody, x_chat_session: str | None = Header(default=None)):
    _owner_only(x_chat_session)
    try:
        return {"model": await asyncio.to_thread(catalog.set_default, body.name)}
    except catalog.CatalogError as exc:
        raise _catalog_error(exc)


# --- a bot's own Telegram bot (UX batch 2, P4); owner only.  The default bot's is the shared one
# (/agent-chat/telegram/*, the settings page).  No response carries the token, only @username and its last four.

class BotTelegramTokenBody(BaseModel):
    bot_token: SecretStr = Field(min_length=8, max_length=256)


def _telegram_error(exc) -> HTTPException:
    code = str(exc)
    return HTTPException(404 if code == "unknown_bot" else 409 if code == "token_in_use" else 422, code)


async def _bot_telegram(call, x_chat_session, *args):
    from .agent_telegram import TelegramError
    credential = _owner_only(x_chat_session)
    try:
        return await asyncio.to_thread(call, owner_hash(credential), *args)
    except TelegramError as exc:
        raise _telegram_error(exc)


@router.get("/bots/{bot_id}/telegram")
async def bot_telegram_status(bot_id: str, x_chat_session: str | None = Header(default=None)):
    from . import agent_telegram_bots as own
    return await _bot_telegram(own.status, x_chat_session, bot_id)


@router.post("/bots/{bot_id}/telegram/token")
async def bot_telegram_token(bot_id: str, body: BotTelegramTokenBody, x_chat_session: str | None = Header(default=None)):
    from . import agent_telegram_bots as own
    return await _bot_telegram(own.save_token, x_chat_session, bot_id, body.bot_token.get_secret_value())


@router.post("/bots/{bot_id}/telegram/bind")
async def bot_telegram_bind(bot_id: str, x_chat_session: str | None = Header(default=None)):
    from . import agent_telegram_bots as own
    return await _bot_telegram(own.start_bind, x_chat_session, bot_id)


@router.post("/bots/{bot_id}/telegram/unbind")
async def bot_telegram_unbind(bot_id: str, x_chat_session: str | None = Header(default=None)):
    from . import agent_telegram_bots as own
    return await _bot_telegram(own.unbind, x_chat_session, bot_id)


@router.delete("/bots/{bot_id}/telegram")
async def bot_telegram_delete(bot_id: str, x_chat_session: str | None = Header(default=None)):
    from . import agent_telegram_bots as own
    return await _bot_telegram(own.delete_token, x_chat_session, bot_id)
