"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import asyncio
from app.agent_workflow.checkpoints import open_postgres_checkpointer
import os

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



def test_postgres_reopen_preserves_context_but_never_runtime_key(disposable_database):
    import asyncio,json
    from uuid import uuid4
    from app.agent_workflow.checkpoints import open_postgres_checkpointer
    from app.agent_workflow.settings import checkpoint_database_url
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext,checkpoint_thread_id
    key='synthetic-provider-key-not-for-state';chat=str(uuid4());thread=checkpoint_thread_id('owner-a',chat)
    async def exercise():
        async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
            await saver.setup();g=build_workflow(saver)
            ctx=AgentRuntimeContext('custom','test',key,owner_hash='owner-a',conversation_id=chat,classifier=lambda _:{'category':'B','answer':'公司销售产品和服务。'})
            value=await g.ainvoke({'question':'运营模式','messages':[]},{'configurable':{'thread_id':thread}},context=ctx)
            assert value['answer']=='公司销售产品和服务。'
        async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
            restored=(await build_workflow(saver).aget_state({'configurable':{'thread_id':thread}})).values
            assert restored['previous_category']=='B'
            assert key not in json.dumps(restored,ensure_ascii=False)
            other=(await build_workflow(saver).aget_state({'configurable':{'thread_id':checkpoint_thread_id('owner-b',chat)}})).values
            assert not other
    asyncio.run(exercise())


def test_invalid_second_classifier_cannot_reuse_old_answer_or_authority(disposable_database):
    import asyncio
    from uuid import uuid4
    from app.agent_workflow.checkpoints import open_postgres_checkpointer
    from app.agent_workflow.settings import checkpoint_database_url
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    async def exercise():
        async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
            await saver.setup();g=build_workflow(saver);config={'configurable':{'thread_id':str(uuid4())}}
            first=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:{'category':'B','answer':'OLD_ANSWER'})
            await g.ainvoke({'question':'旧问题','messages':[]},config,context=first)
            second=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:{'category':'INVALID','answer':'PRIVATE_RAW_PROVIDER_OUTPUT'})
            result=await g.ainvoke({'question':'新问题','messages':[]},config,context=second)
            assert result['terminal_status']=='failed'
            assert 'OLD_ANSWER' not in result['answer'] and 'PRIVATE_RAW_PROVIDER_OUTPUT' not in str(result)
            assert result['pending_request'] is None
    asyncio.run(exercise())
