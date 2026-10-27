from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


class AgentWorkflowState(TypedDict, total=False):
    question: str
    messages: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    gate_reason: str | None
    terminal_status: str | None
    node_log: list[dict[str, Any]]
    answer: str
    audit: dict[str, Any]
    pending_request: dict[str, Any] | None
    requested_pending_id: str | None
    result_sets: list[dict[str, Any]]
    previous_scope: dict[str, Any]
    previous_category: str
    turn_id: str


@dataclass(frozen=True)
class AgentRuntimeContext:
    """Ephemeral request-only values. Never copy these fields into graph state/config."""
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str | None = field(default=None, repr=False)
    owner_hash: str = ""
    conversation_id: str = ""
    classifier: object | None = field(default=None, repr=False, compare=False)
    identity_credential: str = field(default="", repr=False, compare=False)
    decider: object | None = field(default=None, repr=False, compare=False)
    executor: object | None = field(default=None, repr=False, compare=False)
    answerer: object | None = field(default=None, repr=False, compare=False)
    resolver: object | None = field(default=None, repr=False, compare=False)
    intent_recognizer: object | None = field(default=None, repr=False, compare=False)
    query_expander: object | None = field(default=None, repr=False, compare=False)
    # The research queue the client sent (None: this client sends none, so nothing asks about it).
    watchlist: tuple[str, ...] | None = field(default=None, repr=False, compare=False)
    history_citation_catalogue: tuple[dict, ...] = ()
    # The owner's profile message (agent_profile.render_block): a hint, never evidence or write authority.
    profile_block: str | None = field(default=None, repr=False, compare=False)
    # A scheduled task (agent_task_runner) with nobody watching: no write is ever granted, and a write the
    # model asks for with request_action goes to ``action_sink`` (the inbox's 待你确认), never to a card here.
    unattended: bool = False
    token_budget: int | None = None
    action_sink: list | None = field(default=None, repr=False, compare=False)
    # The bot this turn belongs to (agent_bots.turn_settings).  ``bot_block`` is its work instruction, a
    # setting and never evidence or write authority.  ``tool_scope`` None: every tool; otherwise the only
    # tool names this turn may offer or run.  ``default_company`` applies only when the question names none.
    bot_id: str | None = None
    bot_block: str | None = field(default=None, repr=False, compare=False)
    tool_scope: frozenset | None = field(default=None, compare=False)
    default_company: dict | None = field(default=None, compare=False)


def checkpoint_thread_id(owner_hash: str, conversation_id: str) -> str:
    import hashlib
    import uuid

    owner = uuid.UUID(str(owner_hash)) if _is_uuid(owner_hash) else owner_hash
    conversation = uuid.UUID(str(conversation_id))
    raw = f"{owner}:{conversation}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_uuid(value: str) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False
