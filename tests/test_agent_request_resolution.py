import pytest

from app.agent_workflow.request_resolution import parse_classification, extract_slots, resume_request


def test_strict_classification_schema_and_single_fence():
    assert parse_classification('```json\n{"intent":"saved_data","confidence":0.8}\n```')["intent"] == "saved_data"
    for raw in ('{"intent":"saved_data","confidence":0.8,"symbol":"AAPL"}',
                '{"intent":"saved_data"}', '{"intent":"faq_qa","confidence":0.9}',
                '{"intent":"general","confidence":true}'):
        with pytest.raises(ValueError): parse_classification(raw)


def test_alias_typo_and_comparison_slots():
    assert extract_slots("苹果最近的新闻")["symbols"] == ["AAPL"]
    assert extract_slots("APPL 最新财报")["suggestion"] == "AAPL"
    assert extract_slots("查询任务状态 " + "A"*64)["symbols"] == []
    pending={"id":"pending-1","question":"比较这两家公司最新财报","symbols":[]}
    first=resume_request(pending,"AAPL")
    assert first["question"]==pending["question"] and first["symbols"]==["AAPL"]
    second=resume_request({**pending,"symbols":["AAPL"]},"AMD")
    assert second["symbols"]==["AAPL","AMD"]


def test_graph_pending_comparison_resumes_same_task_without_guessing():
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    calls=[]
    async def classify(_): return '{"intent":"compound_research"}'
    async def execute(name,args):
        calls.append(args["symbol"])
        return {"ok":True,"data":[{"id":args["symbol"],"source_url":"https://example.test/source",
                                   "excerpt":f"{args['symbol']} financial report"}],"source":"fixture"}
    async def answer(_): yield "A partial comparison."
    async def run():
        graph=build_workflow(InMemorySaver())
        config={"configurable":{"thread_id":"comparison-resume"}}
        context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,executor=execute,answerer=answer)
        first=await graph.ainvoke({"question":"比较这两家公司最新财报","messages":[]},config,context=context)
        second=await graph.ainvoke({"question":"AAPL","messages":[]},config,context=context)
        third=await graph.ainvoke({"question":"AMD","messages":[]},config,context=context)
        return first,second,third
    first,second,third=asyncio.run(run())
    assert first["terminal_status"]==second["terminal_status"]=="waiting_for_input"
    assert first["pending_request"]["id"]==second["pending_request"]["id"]
    assert calls==["AAPL","AMD"]
    assert third["entities"]["symbols"]==["AAPL","AMD"]


def test_explicit_new_request_clears_old_focus_and_asks_for_stock():
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    async def classify(_): return '{"intent":"saved_data","confidence":0.9}'
    async def execute(_name,_args):
        return {"ok":True,"data":[{"id":"source-a","excerpt":"AAPL filing overview","source_url":"https://example.test/filing"}],"source":"fixture"}
    async def answer(_messages): yield "Saved filing overview."
    async def run():
        graph=build_workflow(InMemorySaver())
        config={"configurable":{"thread_id":"new-focus-reset"}}
        context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,executor=execute,answerer=answer)
        first=await graph.ainvoke({"question":"总结 AAPL 最新财报","messages":[]},config,context=context)
        second=await graph.ainvoke({"question":"换个问题，总结最新财报","messages":[]},config,context=context)
        return first,second
    first,second=asyncio.run(run())
    assert first["symbol_focus"]=="AAPL"
    assert second["symbol_focus"] is None
    assert second["terminal_status"]=="waiting_for_input"
    assert second["pending_request"]["reason"]=="missing_stock_focus"


def test_action_missing_source_pending_reply_resumes_original_operation(monkeypatch):
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    from app.agent_workflow import actions
    source="11111111-1111-4111-8111-111111111111"
    calls=[]
    monkeypatch.setattr(actions,"execute_action",lambda **kwargs: calls.append(kwargs) or
                        {"ok":True,"status":"running","job_id":"22222222-2222-4222-8222-222222222222"})
    async def classify(_): return '{"intent":"system_action","confidence":0.99}'
    async def run():
        graph=build_workflow(InMemorySaver())
        config={"configurable":{"thread_id":"pending-action-source"}}
        context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,
                                    identity_credential="owner-session")
        first=await graph.ainvoke({"question":"生成 AI 分析","messages":[]},config,context=context)
        second=await graph.ainvoke({"question":source,"messages":[]},config,context=context)
        return first,second
    first,second=asyncio.run(run())
    assert first["terminal_status"]=="waiting_for_input"
    assert first["pending_request"]["reason"]=="missing_action_source"
    assert second["terminal_status"]=="running"
    assert len(calls)==1 and calls[0]["action"]=="analyze"
    assert calls[0]["payload"]["source_id"]==source
