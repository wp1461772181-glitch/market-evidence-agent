"""Production graph for the direct A/B/C/D agent workflow.

Routing and evidence decisions live in ``direct.py``. This module keeps the existing
LangGraph/checkpoint and streaming API without retaining the former planner engine.
"""
from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from .state import AgentRuntimeContext, AgentWorkflowState, checkpoint_thread_id

CLASSIFIER_SYSTEM = "Classify the latest user message into A evidence, B ordinary in-scope explanation, C out of scope, or D system action. Return a route_request function call."
OUT_OF_SCOPE_SCRIPT = "这个请求不在 Market Evidence Agent 的功能范围内。我可以协助查证市场资料、公开新闻和已保存证据。"
WRITE_CANCELLED = "已取消，没有执行任何操作。"
MAX_SECONDS = 120
HISTORY_TOKEN_ALLOWANCE = 32000


def _bytes(value: Any) -> int:
    import json
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


from .evidence import deterministic_confidence_gate


async def _direct_agent_turn(state: AgentWorkflowState, runtime: Runtime[AgentRuntimeContext]) -> dict:
    from .direct import run_direct_turn
    async def publish(event):
        event = dict(event)
        kind = event.pop("event")
        try: get_stream_writer()({"event": kind, **event})
        except RuntimeError: pass
    result = await run_direct_turn(state.get("question", ""), runtime.context, emit=publish,
                                   messages=state.get("messages", []), state=state)
    answer = result["answer"]
    if result.get("pending_request"):
        pending=result["pending_request"]
        await publish({"event":"clarification","request_id":pending["id"],"reason":pending["reason"],"choices":pending["choices"],"display":pending.get("display"),"question":pending.get("question")})
    try:
        get_stream_writer()({"event": "delta", "text": answer})
        get_stream_writer()({"event": "done", "status": result.get("status", "complete"), "reason": result.get("reason"),
                            "citations": result.get("citations", []), "answer": answer,
                            "pending_request": result.get("pending_request"), "final_protocol": {"route": result.get("route"),**result.get("final_protocol",{})}, "audit": result.get("audit", {})})
    except RuntimeError: pass
    return {"answer": answer, "citations": result.get("citations", []),
            "audit": result.get("audit", {}), "terminal_status": result.get("status"),
            "pending_request":result.get("pending_request"),"result_sets":result.get("result_sets",[]),"previous_category":result.get("previous_category"),"previous_scope":result.get("previous_scope",{}),
            "gate_reason": result.get("reason"), "node_log": [{"node": "direct_agent_turn", "route": result.get("route")}]}


def build_workflow(checkpointer=None):
    builder = StateGraph(AgentWorkflowState, context_schema=AgentRuntimeContext)
    builder.add_node("direct_agent_turn", _direct_agent_turn)
    builder.add_edge(START, "direct_agent_turn")
    builder.add_edge("direct_agent_turn", END)
    return builder.compile(checkpointer=checkpointer)


async def stream_workflow(*, question: str, messages: list[dict], provider: str, model: str,
                          api_key: str, base_url: str | None, owner_hash: str,
                          conversation_id: str, identity_credential: str = "", turn_id: str = "",
                          pending_request_id: str | None = None, legacy_result_sets: list[dict] | None = None,
                          legacy_source_choices: list[dict] | None = None,
                          classifier: object | None = None, watchlist: list[str] | None = None):
    # These former routing hints/results stay accepted so older callers do not fail;
    # none may influence the direct route or tool scope.
    from .checkpoints import open_postgres_checkpointer
    from .settings import checkpoint_database_url
    context = AgentRuntimeContext(provider=provider, model=model, api_key=api_key, base_url=base_url,
        owner_hash=owner_hash, conversation_id=conversation_id, identity_credential=identity_credential,
        classifier=classifier)
    thread = checkpoint_thread_id(owner_hash, conversation_id)
    async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
        graph = build_workflow(saver)
        config = {"configurable": {"thread_id": thread}, "recursion_limit": 30}
        # Legacy pending IDs are accepted for API compatibility. The direct graph
        # does not interpret them as write consent or execute a pending operation.
        state = {"question": question, "messages": messages, "turn_id": turn_id,"requested_pending_id":pending_request_id}
        try:
            async with asyncio.timeout(MAX_SECONDS+10):
                async for event in graph.astream(state, config, context=context, stream_mode="custom"):
                    yield event
        except TimeoutError:
            text = "本轮研究达到时限，已停止继续调用工具。"
            yield {"event": "delta", "text": text}
            yield {"event": "done", "status": "failed", "reason": "turn_deadline", "citations": [],
                   "answer": text, "final_protocol": {}, "audit": {"stop_reason": "turn_deadline"}}


async def mark_terminal_status(owner_hash: str, conversation_id: str, status: str, partial_answer: str) -> None:
    from .checkpoints import open_postgres_checkpointer
    from .settings import checkpoint_database_url
    async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
        graph = build_workflow(saver)
        config = {"configurable": {"thread_id": checkpoint_thread_id(owner_hash, conversation_id)}}
        try: await graph.aupdate_state(config, {"terminal_status": status, "answer": partial_answer[:4000]})
        except Exception: pass


async def run_workflow(**kwargs) -> dict:
    from .checkpoints import open_postgres_checkpointer
    from .settings import checkpoint_database_url
    context = AgentRuntimeContext(provider=kwargs["provider"], model=kwargs["model"], api_key=kwargs["api_key"],
        base_url=kwargs.get("base_url"), owner_hash=kwargs["owner_hash"], conversation_id=kwargs["conversation_id"],
        identity_credential=kwargs.get("identity_credential", ""))
    async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
        graph = build_workflow(saver)
        return await graph.ainvoke({"question": kwargs["question"], "messages": kwargs["messages"]},
            {"configurable": {"thread_id": checkpoint_thread_id(kwargs["owner_hash"], kwargs["conversation_id"])},
             "recursion_limit": 30}, context=context)

def _turn_limit(state: AgentWorkflowState) -> int:
    """The token budget of this turn: the work budget, plus what the conversation carried into the
    turn costs (up to the history budget), so a long history does not eat the budget for the work."""
    return MAX_TOKENS + max(0, min(int(state.get("history_allowance") or 0), HISTORY_TOKEN_ALLOWANCE))


def _budget_reason(state: AgentWorkflowState, reserve: int = 1800) -> str | None:
    if time.monotonic() - state.get("started_at", time.monotonic()) >= MAX_SECONDS:
        return "turn_deadline"
    if state.get("token_usage", 0) + (_bytes(state.get("messages", [])) + 3) // 4 + reserve > _turn_limit(state):
        return "turn_token_budget"
    if _bytes(state.get("messages", [])) > CONTEXT_GUARD_BYTES:
        return "chat_context_too_large"
    return None


def _dialog_budget(budget_kb: int) -> int:
    return max(9000, 9000 * budget_kb // 64)


def _newest_lines(text: str, limit: int) -> str:
    """The newest lines of a numbered list that fit ``limit`` characters (the oldest are dropped
    first, whole lines; one line longer than the limit keeps its end)."""
    lines = [line for line in text.split("\n") if line.strip()]
    while len(lines) > 1 and len("\n".join(lines)) > limit:
        lines.pop(0)
    return "\n".join(lines)[-limit:]


def _dialog_view(messages: list[dict]) -> list[dict]:
    """The conversation the resolver reads: each user message and the start of each answer, newest
    last, as much of it as the budget holds (the oldest turns are the first to go).  The resolver
    completes the user's words from it, so the beginning of a long conversation must not drop out
    while a budget is left."""
    picked, used = [], 0
    for message in reversed(messages):
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = str(message.get("content") or "")[:DIALOG_USER_CAP if role == "user" else DIALOG_ASSISTANT_CAP]
        if picked and used + len(text) > DIALOG_BUDGET_CHARS:
            break
        picked.append({"role": role, "content": text})
        used += len(text)
    return list(reversed(picked))


import time,os
from .. import agent_context as _layers
MAX_TOKENS=32000
HISTORY_TOKEN_ALLOWANCE=_layers.HISTORY_TOKEN_ALLOWANCE
DIALOG_BUDGET_CHARS=_layers.HISTORY_BUDGET

CONTEXT_GUARD_BYTES=_layers.CONTEXT_GUARD_BYTES
DIALOG_USER_CAP=4000
DIALOG_ASSISTANT_CAP=900
