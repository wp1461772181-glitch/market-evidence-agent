import asyncio

import pytest


@pytest.mark.parametrize(('tool_name','budget'), [('query_materials',30.0),('query_research_faq',30.0),('query_stock_prices',5.0)])
def test_tool_executor_applies_knowledge_timeout_only(monkeypatch,tool_name,budget):
    import app.agent_tool_executor as executor
    from types import SimpleNamespace
    from pydantic import BaseModel
    class Args(BaseModel):
        value: str = 'ok'
    observed=[]
    async def invoke(_):
        observed.append(asyncio.get_running_loop().time())
        return {'ok':True,'data':'ok'}
    monkeypatch.setitem(executor.TOOL_REGISTRY,tool_name,SimpleNamespace(args_schema=Args,ainvoke=invoke))
    monkeypatch.delenv('KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS',raising=False)
    original=asyncio.timeout
    def capture(value):
        assert value==budget
        return original(0.1)
    monkeypatch.setattr(asyncio,'timeout',capture)
    result=asyncio.run(executor.execute_tool(tool_name,{}))
    assert result['ok'] is True and observed
