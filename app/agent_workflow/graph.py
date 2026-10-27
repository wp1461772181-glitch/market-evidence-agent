"""Production graph for the direct A/B/C/D agent workflow.

Routing and evidence decisions live in ``direct.py``. This module keeps the existing
LangGraph/checkpoint and streaming API without retaining the former planner engine.
"""
from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from .state import AgentRuntimeContext, AgentWorkflowState, checkpoint_thread_id


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
        await publish({"event":"clarification","request_id":pending["id"],"reason":pending["reason"],"choices":pending["choices"],"display":pending.get("display"),"question":pending.get("question"),
                       **({"options":pending["options"],"select":pending.get("select")} if pending.get("options") else {})})
    try:
        get_stream_writer()({"event": "delta", "text": answer})
        get_stream_writer()({"event": "done", "status": result.get("status", "complete"), "reason": result.get("reason"),
                            "citations": result.get("citations", []), "answer": answer,
                            "pending_request": result.get("pending_request"), "trace": result.get("trace", []),
                            "final_protocol": {"route": result.get("route"),**result.get("final_protocol",{})}, "audit": result.get("audit", {})})
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
                          classifier: object | None = None, watchlist: list[str] | None = None,
                          history_citation_catalogue: list[dict] | None = None,
                          profile_block: str | None = None, unattended: bool = False,
                          token_budget: int | None = None, action_sink: list | None = None,
                          bot: dict | None = None):
    # These former routing hints/results stay accepted so older callers do not fail;
    # none may influence the direct route or tool scope.  ``bot`` (agent_bots.turn_settings) does narrow it.
    from .checkpoints import open_postgres_checkpointer
    from .settings import checkpoint_database_url
    bot = bot or {}
    context = AgentRuntimeContext(provider=provider, model=model, api_key=api_key, base_url=base_url,
        owner_hash=owner_hash, conversation_id=conversation_id, identity_credential=identity_credential,
        classifier=classifier, history_citation_catalogue=tuple(history_citation_catalogue or ()),
        profile_block=profile_block, unattended=unattended, token_budget=token_budget, action_sink=action_sink,
        bot_id=bot.get("bot_id"), bot_block=bot.get("block"), tool_scope=bot.get("tool_scope"),
        default_company=bot.get("default_company"))
    thread = checkpoint_thread_id(owner_hash, conversation_id)
    async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
        graph = build_workflow(saver)
        config = {"configurable": {"thread_id": thread}, "recursion_limit": 30}
        # Legacy pending IDs are accepted for API compatibility. The direct graph
        # does not interpret them as write consent or execute a pending operation.
        state = {"question": question, "messages": messages, "turn_id": turn_id,"requested_pending_id":pending_request_id}
        async for event in graph.astream(state, config, context=context, stream_mode="custom"):
            yield event


async def mark_terminal_status(owner_hash: str, conversation_id: str, status: str, partial_answer: str) -> None:
    from .checkpoints import open_postgres_checkpointer
    from .settings import checkpoint_database_url
    async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
        graph = build_workflow(saver)
        config = {"configurable": {"thread_id": checkpoint_thread_id(owner_hash, conversation_id)}}
        try: await graph.aupdate_state(config, {"terminal_status": status, "answer": partial_answer[:4000]})
        except Exception: pass


async def set_pending_request(owner_hash: str, conversation_id: str, card: dict) -> None:
    """Make ``card`` the conversation's pending request, as if its last turn had proposed it: the next
    press of its button is checked against it exactly like any other card (agent_tasks.mint_card)."""
    from .checkpoints import open_postgres_checkpointer
    from .settings import checkpoint_database_url
    async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
        graph = build_workflow(saver)
        config = {"configurable": {"thread_id": checkpoint_thread_id(owner_hash, conversation_id)}}
        await graph.aupdate_state(config, {"pending_request": card})


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
