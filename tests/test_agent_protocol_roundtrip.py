import asyncio
import json

from app.agent_workflow.graph import build_workflow
from app.agent_workflow.state import AgentRuntimeContext


def test_genuine_multi_tool_reply_keeps_ids_and_reasoning_pairing():
    async def classify(_): return '{"intent":"compound_research","symbol":"AMD","original_required":false}'
    calls=[{"id":f"provider-{number}","type":"function","function":{
        "name":"query_stock_prices","arguments":json.dumps({"symbol":symbol})}}
        for number,symbol in enumerate(("AMD","AAPL"),1)]
    async def decide(_messages,_schemas):
        return {"usage":{"total_tokens":20}}, {"content":None,"reasoning_content":"genuine-provider-reasoning",
                                               "tool_calls":calls}
    async def execute(_name,args):
        return {"ok":True,"data":[{"symbol":args["symbol"],"close":100}],"source":"fixture"}
    async def answer(messages):
        assistant=next(item for item in messages if item.get("tool_calls"))
        assert assistant["tool_calls"]==calls
        assert assistant["reasoning_content"]=="genuine-provider-reasoning"
        assert [item["tool_call_id"] for item in messages if item["role"]=="tool"]==["provider-1","provider-2"]
        yield "Both observations were read."
    context=AgentRuntimeContext("deepseek","deepseek-flash","synthetic",classifier=classify,
                                decider=decide,executor=execute,answerer=answer)
    state=asyncio.run(build_workflow().ainvoke({"question":"AMD AAPL prices","messages":[]},
        {"configurable":{"thread_id":"genuine-multiple"}},context=context))
    assert state["terminal_status"]=="succeeded" and state["tool_calls"]==2
