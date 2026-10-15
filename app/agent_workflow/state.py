from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


class AgentWorkflowState(TypedDict, total=False):
    question: str
    original_question: str
    canonical_question: str
    resume_slots: dict[str, Any] | None
    pending_request: dict[str, Any] | None
    action_request_id: str | None
    intent: str
    entities: dict[str, Any]
    symbol_focus: str | None
    material_focus: str | None
    messages: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    task_states: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    gate_passed: bool
    gate_reason: str | None
    next_action: str | None
    seen_tool_signatures: list[str]
    tool_calls: int
    decision_steps: int
    token_usage: int
    usage_mode: str
    started_at: float
    terminal_status: str | None
    node_log: list[dict[str, Any]]
    required_sources: list[dict[str, Any]]
    pending_tool: dict[str, Any] | None
    pending_tool_queue: list[dict[str, Any]]
    web_alternative_attempted: bool
    answer: str
    final_protocol: dict[str, Any]
    turn_id: str
    audit: dict[str, Any]
    result_sets: list[dict[str, Any]]
    legacy_result_sets: list[dict[str, Any]]
    legacy_source_choices: list[dict[str, Any]]
    semantic_reference: dict[str, Any]
    semantic_catalog: list[dict[str, Any]]
    semantic_intents: list[str]
    semantic_plan: list[dict[str, Any]]
    semantic_index: int
    semantic_active: dict[str, Any] | None
    semantic_error: str | None
    semantic_hint: str | None
    semantic_conflict: bool
    semantic_positions: dict[str, Any] | None
    semantic_owner: str
    semantic_conversation: str
    semantic_active_scope: dict[str, Any]
    semantic_action: str | None
    semantic_turn: bool
    semantic_write_targets: dict[str, Any] | None
    semantic_write_slots: dict[str, Any] | None
    semantic_question_source: str | None
    semantic_action_hint: str | None      # the write the intent stage thinks is meant (a proposal, not an authority)
    semantic_write_confirmed: bool        # the user pressed "同意" on a server-made proposal
    history_allowance: int                # tokens the carried conversation adds to this turn's budget
    queue_decided: list[str]              # symbols the user already answered the research-queue card for


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
    planner: object | None = field(default=None, repr=False, compare=False)
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
