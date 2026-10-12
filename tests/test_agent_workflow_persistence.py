import asyncio
import os

import psycopg

from app.agent_workflow.checkpoints import open_postgres_checkpointer, postgres_saver_url
from app.agent_workflow.graph import build_workflow
from app.agent_workflow.state import AgentRuntimeContext, checkpoint_thread_id


def test_postgres_checkpoint_reopens_state_and_never_persists_model_secret(disposable_database):
    database_url=os.environ["DATABASE_URL"]
    thread=checkpoint_thread_id("owner-hash-a", "11111111-1111-4111-8111-111111111111")
    sentinel="model-key-sentinel-never-store"

    async def write_and_reopen():
        runtime=AgentRuntimeContext("custom","model",sentinel,owner_hash="owner-hash-a",
                                    conversation_id="11111111-1111-4111-8111-111111111111",
                                    classifier=lambda _question: None)
        async def classify(_question):return '{"intent":"saved_data","symbol":"AAPL"}'
        runtime=AgentRuntimeContext("custom","model",sentinel,owner_hash="owner-hash-a",
                                    conversation_id="11111111-1111-4111-8111-111111111111",classifier=classify,
                                    executor=_fixture_executor,answerer=_fixture_answer)
        async with open_postgres_checkpointer(database_url) as first:
            await first.setup()
            graph=build_workflow(first)
            await graph.ainvoke({"question":"AAPL saved prices","messages":[{"role":"user","content":"AAPL saved prices"}],
                                 "observations":[],"citations":[],"tool_calls":0,"decision_steps":0,
                                 "token_usage":0,"seen_tool_signatures":[],"node_log":[]},
                                {"configurable":{"thread_id":thread}},context=runtime)
        async with open_postgres_checkpointer(database_url) as second:
            restored=await build_workflow(second).aget_state({"configurable":{"thread_id":thread}})
            return restored.values

    values=asyncio.run(write_and_reopen())
    assert values["symbol_focus"]=="AAPL"
    assert values["intent"]=="saved_data"
    assert sentinel not in repr(values)
    assert sentinel not in thread
    assert values["tool_calls"] == 1
    assert values["observations"][0]["name"] == "query_stock_prices"
    assert values["terminal_status"] == "succeeded"
    assert values["audit"]["tool_calls"] == 1

    connection=psycopg.connect(postgres_saver_url(database_url))
    try:
        tables=connection.execute("select tablename from pg_tables where schemaname='public' and tablename like '%checkpoint%'").fetchall()
        assert tables
        assert all(sentinel.encode() not in repr(row).encode() for (table,) in tables
                   for row in connection.execute(f'SELECT * FROM "{table}"').fetchall())
    finally:
        connection.close()


def test_checkpoint_threads_are_isolated_by_verified_owner(disposable_database):
    from uuid import uuid4
    from app.agent_workflow.graph import mark_terminal_status
    database_url=os.environ["DATABASE_URL"]
    conversation=str(uuid4())
    first=checkpoint_thread_id("owner-a",conversation)
    second=checkpoint_thread_id("owner-b",conversation)
    async def exercise():
        async with open_postgres_checkpointer(database_url) as saver:
            await saver.setup()
        await mark_terminal_status("owner-a",conversation,"cancelled","partial")
        async with open_postgres_checkpointer(database_url) as saver:
            graph=build_workflow(saver)
            own=await graph.aget_state({"configurable":{"thread_id":first}})
            other=await graph.aget_state({"configurable":{"thread_id":second}})
            return own.values, other.values
    own,other=asyncio.run(exercise())
    assert own["terminal_status"]=="cancelled" and own["answer"]=="partial"
    assert not other


def test_workflow_health_reports_migrated_saver_and_key_configuration(disposable_database, monkeypatch):
    from app.main import agent_workflow_health
    from app.agent_workflow.checkpoints import checkpoint_ready
    database_url=os.environ["DATABASE_URL"]
    async def migrate():
        async with open_postgres_checkpointer(database_url) as saver:
            await saver.setup()
    asyncio.run(migrate())
    monkeypatch.delenv("TAVILY_API_KEY",raising=False)
    assert checkpoint_ready(database_url)
    assert agent_workflow_health()["checkpoint_ready"] is True
    assert agent_workflow_health()["tavily_configured"] is False


def test_malformed_provider_payload_absent_from_all_postgres_checkpoint_rows(disposable_database):
    import json
    database_url=os.environ["DATABASE_URL"]
    sentinel="FAKE_RUNTIME_KEY_SENTINEL"
    async def classify(_):return '{"intent":"compound_research","symbols":["AMD"],"original_required":false}'
    async def decide(*_):
        return {"usage":{"total_tokens":20}}, {"tool_calls":[{"id":"provider-id", "function":{"name":"query_stock_prices", "arguments":json.dumps({"symbol":"AMD","api_key":sentinel})}}]}
    async def exercise():
        async with open_postgres_checkpointer(database_url) as saver:
            await saver.setup()
            graph=build_workflow(saver)
            config={"configurable":{"thread_id":checkpoint_thread_id("owner-a","22222222-2222-4222-8222-222222222222")}}
            state=await graph.ainvoke({"question":"AMD saved prices", "messages":[{"role":"user","content":"AMD saved prices"}]},config,
                context=AgentRuntimeContext("custom","model","runtime",classifier=classify,decider=decide))
            history=[snapshot.values async for snapshot in graph.aget_state_history(config)]
            return state,history
    state,history=asyncio.run(exercise())
    assert state["gate_reason"]=="invalid_or_disallowed_tool"
    assert sentinel not in repr(history)
    with psycopg.connect(postgres_saver_url(database_url)) as connection:
        tables=[row[0] for row in connection.execute("select tablename from pg_tables where schemaname='public' and tablename like '%checkpoint%'")]
        for table in tables:
            assert sentinel.encode() not in repr(connection.execute(f'SELECT * FROM "{table}"').fetchall()).encode()


def test_second_turn_resets_old_protocol_and_route_on_invalid_classifier(disposable_database):
    database_url=os.environ["DATABASE_URL"]
    thread=checkpoint_thread_id("owner-a","33333333-3333-4333-8333-333333333333")
    async def classify_ok(_):return '{"intent":"saved_data","symbol":"AMD"}'
    async def classify_bad(_):return 'not-json'
    async def exercise():
        async with open_postgres_checkpointer(database_url) as saver:
            await saver.setup()
            graph=build_workflow(saver)
            config={"configurable":{"thread_id":thread}}
            await graph.ainvoke({"question":"AMD price","messages":[{"role":"user","content":"AMD price"}]},config,
                context=AgentRuntimeContext("custom","model","runtime",classifier=classify_ok,executor=_fixture_executor,answerer=_fixture_answer))
            await graph.aupdate_state(config,{"final_protocol":{"reasoning_content":["OLD_THINKING_SENTINEL"]},
                                              "required_sources":[{"name":"query_stock_prices","arguments":{"symbol":"AMD"}}]},as_node="record_log")
            return await graph.ainvoke({"question":"other question","messages":[{"role":"user","content":"other question"}]},config,
                context=AgentRuntimeContext("custom","model","runtime",classifier=classify_bad,answerer=_fixture_answer))
    state=asyncio.run(exercise())
    assert state["intent"]=="clarify" and state["required_sources"]==[]
    assert state["final_protocol"]=={} and "OLD_THINKING_SENTINEL" not in repr(state)


async def _fixture_executor(name, args):
    return {"ok": True, "data": [{"symbol": args.get("symbol"), "close": 100}], "source": "saved"}


async def _fixture_answer(_messages):
    yield "Saved AAPL price."
