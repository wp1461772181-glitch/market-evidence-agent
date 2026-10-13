import pytest

from app.agent_workflow.request_resolution import parse_classification, extract_slots, resume_request, is_pending_reply


def test_pending_reply_requires_answer_to_expected_slot():
    pending = {"reason": "missing_action_type", "question": "上传证据到资料库", "symbols": []}
    for question in ("那你去看下资料库中苹果最新的资料说了什么", "can you check Apple saved materials"):
        assert not is_pending_reply(pending, question)
    assert is_pending_reply(pending, "import")
    assert not is_pending_reply({**pending, "reason": "missing_action_source", "selected_action": "import"}, "苹果最新资料是什么")
    assert is_pending_reply({**pending, "reason": "missing_action_source", "selected_action": "import"}, "11111111-1111-4111-8111-111111111111")
    assert is_pending_reply({**pending, "reason": "missing_action_source", "selected_action": "fetch_sec"}, "AAPL")
    assert is_pending_reply({**pending, "reason": "missing_action_source", "selected_action": "fetch_sec"}, "10-K")
    assert is_pending_reply({"reason": "missing_stock_focus", "symbols": []}, "就是AAPL")
    assert is_pending_reply({"reason": "missing_comparison_symbol", "symbols": ["AAPL"]}, "AMD")
    for reason in ("missing_stock_focus", "missing_comparison_symbol", "missing_action_symbol", "missing_action_source"):
        assert is_pending_reply({"reason": reason, "symbols": ["AAPL"]}, "PLTR")
        assert is_pending_reply({"reason": reason, "symbols": ["AAPL"]}, "BRK.B")
        assert not is_pending_reply({"reason": reason, "symbols": ["AAPL"]}, "PLTR 最新价格是什么")
    assert is_pending_reply(pending, "生成 AI 分析")
    assert not is_pending_reply(pending, "生成 AMD 财报分析")


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


def test_pronoun_followup_keeps_completed_turn_stock_focus():
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    calls=[]
    async def classify(_): return '{"intent":"saved_data","confidence":0.9}'
    async def execute(name,args):
        calls.append((name,args))
        return {"ok":True,"data":[{"symbol":args["symbol"],"close":100}],"source":"fixture"}
    async def answer(_): yield "Price."
    async def run():
        graph=build_workflow(InMemorySaver())
        config={"configurable":{"thread_id":"pronoun-stock-focus"}}
        context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,executor=execute,answerer=answer)
        first=await graph.ainvoke({"question":"AAPL price","messages":[]},config,context=context)
        second=await graph.ainvoke({"question":"那它最新价格呢","messages":[]},config,context=context)
        return first,second
    first,second=asyncio.run(run())
    assert first["symbol_focus"]==second["symbol_focus"]=="AAPL"
    assert calls==[("query_stock_prices",{"symbol":"AAPL"})]*2


def test_chinese_offered_action_label_keeps_pending_scope(monkeypatch):
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    async def classify(_): return '{"intent":"system_action","confidence":0.9}'
    async def run():
        graph=build_workflow(InMemorySaver())
        config={"configurable":{"thread_id":"chinese-action-label"}}
        context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,identity_credential="owner-session")
        first=await graph.ainvoke({"question":"请处理 AAPL 资料","messages":[]},config,context=context)
        second=await graph.ainvoke({"question":"生成 AI 分析","messages":[]},config,context=context)
        return first,second
    first,second=asyncio.run(run())
    assert first["pending_request"]["reason"]=="missing_action_type"
    assert second["pending_request"]["reason"]=="missing_action_source"
    assert second["pending_request"]["id"]==first["pending_request"]["id"]
    assert second["pending_request"]["question"]==first["pending_request"]["question"]
    assert second["pending_request"]["symbols"]==["AAPL"]
    assert second["pending_request"]["selected_action"]=="analyze"


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


@pytest.mark.parametrize("read_question", ["那你去看下资料库中苹果最新的资料说了什么", "can you check Apple saved materials"])
def test_legacy_action_pending_is_replaced_by_saved_apple_read(read_question):
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    calls=[]
    async def classify(question):
        return '{"intent":"system_action","confidence":0.9}' if "上传" in question else '{"intent":"saved_data","confidence":0.9}'
    async def execute(name,args):
        calls.append((name,args))
        return {"ok":True,"data":[{"id":"saved-aapl","excerpt":"Saved Apple source","source_url":"https://example.test/aapl"}],"source":"fixture"}
    async def answer(_): yield "Saved Apple material."
    async def run():
        graph=build_workflow(InMemorySaver())
        config={"configurable":{"thread_id":"legacy-operation-apple-read"}}
        context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,executor=execute,answerer=answer)
        first=await graph.ainvoke({"question":"上传证据到资料库","messages":[]},config,context=context)
        second=await graph.ainvoke({"question":read_question,"messages":[]},config,context=context)
        return first,second
    first,second=asyncio.run(run())
    assert first["pending_request"]["reason"]=="missing_action_type"
    assert len(first["pending_request"]["choices"])>=6
    assert second["pending_request"] is None
    assert second["entities"]["symbols"]==["AAPL"]
    assert second["intent"]=="source_qa"
    assert calls and all(args["symbol"]=="AAPL" for _,args in calls)
    assert all(name not in {"query_stock_prices","web_research"} for name,_ in calls)


def test_selected_action_keeps_id_through_missing_source(monkeypatch):
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    from app.agent_workflow import actions
    calls=[]
    monkeypatch.setattr(actions,"execute_action",lambda **kwargs: calls.append(kwargs) or {"ok":True,"status":"running"})
    async def classify(_): return '{"intent":"system_action","confidence":0.9}'
    async def run():
        graph=build_workflow(InMemorySaver())
        config={"configurable":{"thread_id":"selected-import-source"}}
        context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,identity_credential="owner-session")
        first=await graph.ainvoke({"question":"请帮我处理这份资料","messages":[]},config,context=context)
        second=await graph.ainvoke({"question":"import","messages":[]},config,context=context)
        third=await graph.ainvoke({"question":"11111111-1111-4111-8111-111111111111","messages":[]},config,context=context)
        return first,second,third
    first,second,third=asyncio.run(run())
    assert first["pending_request"]["reason"]=="missing_action_type"
    assert second["pending_request"]["reason"]=="missing_action_source"
    assert first["pending_request"]["id"]==second["pending_request"]["id"]
    assert second["pending_request"]["selected_action"]=="import"
    assert len(calls)==1 and calls[0]["action"]=="import"
    assert third["pending_request"] is None


def test_web_news_upload_stops_without_action_or_import():
    import asyncio
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.graph import build_workflow
    from app.agent_workflow.state import AgentRuntimeContext
    async def classify(_): return '{"intent":"other","confidence":0.9}'
    graph=build_workflow(InMemorySaver())
    state=asyncio.run(graph.ainvoke({"question":"把这三篇你搜到的新闻，上传到资料库里面去","messages":[]},
        {"configurable":{"thread_id":"unsupported-news-upload"}},context=AgentRuntimeContext("custom","model","synthetic",classifier=classify)))
    assert state["pending_request"] is None
    assert "不能" in state["answer"] and "网页新闻" in state["answer"]
