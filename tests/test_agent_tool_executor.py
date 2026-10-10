import asyncio
from app.agent_tool_executor import execute_tool


def test_unknown_and_invalid_tool_calls_are_closed_without_execution():
    unknown = asyncio.run(execute_tool("run_sql", {}))
    invalid = asyncio.run(execute_tool("query_stock_prices", {"symbol": "../AAPL"}))
    assert unknown["error_code"] == "unknown_tool"
    assert invalid["error_code"] == "invalid_arguments"


def test_transient_tool_failure_retries_once_and_cancellation_propagates(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pydantic import BaseModel
    import app.agent_tool_executor as executor

    class Args(BaseModel):
        value: int
    tool = SimpleNamespace(args_schema=Args, ainvoke=AsyncMock(side_effect=[TimeoutError(), {"ok": True, "data": 1}]))
    monkeypatch.setitem(executor.TOOL_REGISTRY, "fake", tool)
    result = asyncio.run(executor.execute_tool("fake", {"value": 1}))
    assert result["data"] == 1 and tool.ainvoke.await_count == 2

    cancelled = SimpleNamespace(args_schema=Args, ainvoke=AsyncMock(side_effect=asyncio.CancelledError()))
    monkeypatch.setitem(executor.TOOL_REGISTRY, "fake", cancelled)
    try:
        asyncio.run(executor.execute_tool("fake", {"value": 1}))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation must propagate")
