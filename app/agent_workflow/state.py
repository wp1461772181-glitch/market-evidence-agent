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
