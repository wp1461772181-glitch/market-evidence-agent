"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import asyncio

def test_accession_digits_are_not_interpreted_as_requested_period():
    from app.agent_workflow.graph import deterministic_confidence_gate
    question = "AMD 0000320193-26-000001 这份财报的营收主题是什么"
    result = {"ok": True, "source": "knowledge_chunks", "data": [{"id": "1", "symbol": "AMD",
        "excerpt": "AMD revenue grew in the filing; 营收 increased."}]}
    assert deterministic_confidence_gate(question, result, verified_symbols=["AMD"])[0]


def test_stream_deadline_returns_normal_done_and_checkpoint_status(monkeypatch):
    from contextlib import asynccontextmanager
    from langgraph.checkpoint.memory import InMemorySaver
    import app.agent_workflow.graph as workflow
    import app.agent_workflow.checkpoints as checkpoints
    import app.agent_workflow.direct as direct
    saver=InMemorySaver()
    @asynccontextmanager
    async def open_saver(_url): yield saver
    async def slow_decision(*_args):
        await asyncio.sleep(0.1)
        return {}, {"content": "late"}
    monkeypatch.setattr(checkpoints,"open_postgres_checkpointer",open_saver)
    monkeypatch.setattr(direct,"decision_request",slow_decision)
    monkeypatch.setattr(direct,"MAX_SECONDS",0.01)
    async def collect():
        events=[]
        async for item in workflow.stream_workflow(question="hello",messages=[{"role":"user","content":"hello"}],
            provider="deepseek",model="demo",api_key="sentinel",base_url=None,
            owner_hash="owner",conversation_id="11111111-1111-4111-8111-111111111111"):
            events.append(item)
        return events
    events=asyncio.run(collect())
    assert events[-1]["event"]=="done" and events[-1]["reason"]=="turn_deadline"
