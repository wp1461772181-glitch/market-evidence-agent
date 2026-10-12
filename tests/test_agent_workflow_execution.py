import asyncio
import json

from app.agent_workflow.graph import build_workflow
from app.agent_workflow.state import AgentRuntimeContext


def run_graph(intent, question, decisions=(), results=None):
    calls = []
    results = results or {}
    pending = list(decisions)

    async def classifier(_question):
        return json.dumps({"intent": intent, "symbol": "AMD", "symbols": ["AMD", "AAPL"] if "AAPL" in question else ["AMD"]})

    async def decide(_messages, _schemas):
        calls.append("decision")
        return ({"usage": {"total_tokens": 20}}, pending.pop(0))

    async def execute(name, args):
        calls.append((name, args))
        return results.get(name, {"ok": True, "data": [{"symbol": args.get("symbol"), "close": 100}], "source": "saved"})

    async def answer(_messages):
        calls.append("answer")
        yield "final "
        yield "answer"

    runtime = AgentRuntimeContext("custom", "model", "sentinel", classifier=classifier,
                                  decider=decide, executor=execute, answerer=answer)
    async def collect():
        events = []
        async for event in build_workflow().astream({"question": question, "messages": [{"role": "user", "content": question}]},
                                                     {"configurable": {"thread_id": "test"}}, context=runtime,
                                                     stream_mode="custom"):
            events.append(event)
        return events
    return asyncio.run(collect()), calls


def test_saved_comparison_fetches_both_stocks_before_final_answer():
    decisions = [{"tool_calls": []}]
    events, calls = run_graph("saved_data", "compare AMD and AAPL saved closes", decisions)
    assert [x[1]["symbol"] for x in calls if isinstance(x, tuple)] == ["AMD", "AAPL"]
    assert calls.index("answer") > calls.index(("query_stock_prices", {"symbol": "AAPL"}))
    assert [event["event"] for event in events].count("tool_result") == 2


def test_weak_initial_knowledge_never_reaches_agent_or_final_model():
    events, calls = run_graph("source_qa", "AMD revenue in 2025", results={"query_materials": {"ok": True, "data": []}})
    assert "decision" not in calls and "answer" not in calls
    assert events[-1]["event"] == "done" and events[-1]["reason"] == "zero_evidence"


def test_web_news_graph_passes_classifier_symbol_to_gate():
    question = "Search for latest AMD company news. List the source and publication date, and use only verified page content."
    row = {"url": "https://example.com/news", "title": "Company news today",
           "excerpt": "The company announced new products. This page lists a source and publication date.",
           "published_at": "2026-10-11T14:00:00+00:00", "body_provenance": "public_page_fetch",
           "search_snippet": "AMD latest news"}
    result = {"ok": True, "source": "public web search and fetched page excerpts", "data": [row]}
    events, calls = run_graph("web_research", question, results={"web_research": result})
    assert "answer" not in calls
    assert events[-1]["reason"] == "requested_entity_missing"

    row = {**row, "title": "AMD announces new products",
           "excerpt": "AMD announced new products in its company update."}
    events, calls = run_graph("web_research", question, results={"web_research": {**result, "data": [row]}})
    assert calls[-1] == "answer"
    assert events[-1]["event"] == "done"


def test_one_initial_knowledge_lookup_is_only_tool_before_answer():
    result = {"ok": True, "data": [{"title": "AMD 2025 revenue", "excerpt": "AMD 2025 revenue $1bn", "source_url": "https://example.com"}]}
    events, calls = run_graph("source_qa", "AMD revenue in 2025", [{"tool_calls": []}], {"query_materials": result})
    assert [x[0] for x in calls if isinstance(x, tuple)] == ["query_materials"]
    assert calls[-1] == "answer"
    assert events[-1]["event"] == "done"


def test_compound_repeated_normalized_tool_call_stops_without_final_model():
    result = {"ok": True, "data": [{"title": "AMD 2025 revenue", "excerpt": "AMD 2025 revenue $1bn",
                                   "source_url": "https://example.com"}]}
    repeat = {"tool_calls": [{"id": "again", "function": {"name": "query_materials",
                           "arguments": json.dumps({"symbol": "amd", "keyword": "AMD revenue in 2025"})}}]}
    events, calls = run_graph("compound_research", "AMD revenue in 2025", [repeat], {"query_materials": result})
    assert [x[0] for x in calls if isinstance(x, tuple)] == ["query_materials"]
    assert "answer" not in calls
    assert events[-1]["reason"] == "repeated_tool_call"


def test_unsupported_mutation_never_calls_tool_or_model():
    events, calls = run_graph("unsupported_action", "buy AMD")
    assert calls == []
    assert events[-1]["reason"] == "unsupported_action"


def test_stream_deadline_returns_normal_done_and_checkpoint_status(monkeypatch):
    from contextlib import asynccontextmanager
    from langgraph.checkpoint.memory import InMemorySaver
    import app.agent_workflow.graph as workflow
    import app.agent_workflow.checkpoints as checkpoints
    import app.agent_chat_provider as provider
    saver=InMemorySaver()
    @asynccontextmanager
    async def open_saver(_url): yield saver
    async def slow_decision(*_args):
        await asyncio.sleep(0.1)
        return {}, {"content": "late"}
    monkeypatch.setattr(checkpoints,"open_postgres_checkpointer",open_saver)
    monkeypatch.setattr(provider,"decision_request",slow_decision)
    monkeypatch.setattr(workflow,"MAX_SECONDS",0.01)
    async def collect():
        events=[]
        async for item in workflow.stream_workflow(question="hello",messages=[{"role":"user","content":"hello"}],
            provider="deepseek",model="demo",api_key="sentinel",base_url=None,
            owner_hash="owner",conversation_id="11111111-1111-4111-8111-111111111111"):
            events.append(item)
        return events
    events=asyncio.run(collect())
    assert events[-1]["event"]=="done" and events[-1]["reason"]=="turn_deadline"


def test_compound_explicit_web_and_analysis_require_both_sources_even_if_flags_omitted():
    from app.agent_workflow.graph import _required
    plan=_required({"question":"Compare AMD original filings, saved AI analysis, and online news",
                    "intent":"compound_research","symbol_focus":"AMD",
                    "entities":{"symbols":["AMD"],"original_required":True,
                                "analysis_required":False,"web_required":False}})
    assert [item["name"] for item in plan]==["query_materials","search_material_analysis","web_research"]


def test_saved_chinese_filing_routes_to_filing_and_ai_summary_to_derived():
    from app.agent_workflow.graph import _required
    for word in ("财报", "年报", "季报", "公告", "10-K", "10-Q", "8-K"):
        assert _required({"question": f"帮我看下 AMD 最新{word}的简介", "intent": "saved_data",
                          "entities": {"symbols": ["AMD"]}})[0]["name"] == "query_sec_filings"
    for word in ("AI分析", "已保存AI总结", "AI summary"):
        assert _required({"question": f"AMD {word}", "intent": "saved_data",
                          "entities": {"symbols": ["AMD"]}})[0]["name"] == "query_material_analysis"
    assert _required({"question": "AMD 最新价格", "intent": "saved_data", "entities": {"symbols": ["AMD"]}})[0]["name"] == "query_stock_prices"


def test_literal_url_is_extracted_for_web_read_and_compound():
    from app.agent_workflow.graph import _required
    for intent in ("web_research", "compound_research"):
        plan = _required({"question": "请读取 [报告](https://example.com/report) 并总结。", "intent": intent,
                          "entities": {"symbols": ["AMD"], "web_required": True}})
        assert [item for item in plan if item["name"] == "web_research"][0]["arguments"]["query_or_url"] == "https://example.com/report"


def test_invalid_provider_arguments_do_not_enter_memory_history():
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent_workflow.state import AgentRuntimeContext
    saver = InMemorySaver()
    async def classify(_): return '{"intent":"compound_research","symbol":"AMD","original_required":false}'
    async def decide(*_):
        return {"usage":{"total_tokens":20}}, {"tool_calls":[{"id":"x", "function":{"name":"query_stock_prices", "arguments":json.dumps({"symbol":"AMD","api_key":"FAKE_RUNTIME_KEY_SENTINEL"})}}]}
    async def run():
        graph=build_workflow(saver)
        config={"configurable":{"thread_id":"leak-test"}}
        await graph.ainvoke({"question":"compare AMD prices", "messages":[{"role":"user","content":"compare AMD prices"}]},config,
                            context=AgentRuntimeContext("custom","model","runtime",classifier=classify,decider=decide))
        return [snapshot.values for snapshot in [item async for item in graph.aget_state_history(config)]]
    assert "FAKE_RUNTIME_KEY_SENTINEL" not in repr(asyncio.run(run()))


def test_final_generation_is_skipped_after_large_actual_tool_evidence(monkeypatch):
    import app.agent_chat_provider as provider
    calls=[]
    async def classify_request(*_):
        return {"usage":{"total_tokens":29000}}, {"content":'{"intent":"saved_data","symbol":"AMD"}'}
    async def execute(_name,_args):
        return {"ok":True,"data":[{"symbol":"AMD","body":"e"*7900}],"source":"saved"}
    async def answer(_messages):
        calls.append("answer")
        yield "should not run"
    monkeypatch.setattr(provider,"decision_request",classify_request)
    async def run():
        events=[]
        runtime=AgentRuntimeContext("custom","model","runtime",executor=execute,answerer=answer)
        async for event in build_workflow().astream({"question":"AMD price", "messages":[{"role":"user","content":"AMD price"}]},
            {"configurable":{"thread_id":"budget-evidence"}},context=runtime,stream_mode="custom"):
            events.append(event)
        return events
    events=asyncio.run(run())
    assert calls==[] and events[-1]["reason"]=="turn_token_budget"


def test_final_actual_usage_over_limit_is_recorded_and_stops_normally(monkeypatch):
    import app.agent_chat_provider as provider
    async def final_stream(_provider,_model,_key,_messages,parts,_base):
        yield "A bounded answer."
        parts["_usage"]={"total_tokens":35000}
    monkeypatch.setattr(provider,"stream_answer",final_stream)
    async def decide(_provider,_model,_key,messages,_schemas,_base):
        if messages[0]["content"].startswith("Classify a market evidence"):
            return {"usage":{"total_tokens":10}}, {"content":'{"intent":"general"}'}
        return {"usage":{"total_tokens":1}}, {"tool_calls":[]}
    monkeypatch.setattr(provider,"decision_request",decide)
    async def run():
        runtime=AgentRuntimeContext("deepseek","model","runtime")
        return await build_workflow().ainvoke({"question":"hello","messages":[{"role":"user","content":"hello"}],
            "token_usage":30000,"usage_mode":"actual"},
            {"configurable":{"thread_id":"actual-budget"}},context=runtime)
    # The resolver resets turn accounting; use a very large provider final usage.
    state=asyncio.run(run())
    assert state["gate_reason"]=="turn_token_budget"
    assert state["token_usage"]==35011 and state["usage_mode"]=="actual"
    assert state["terminal_status"]=="complete"


def test_explicit_two_symbols_do_not_require_singular_focus():
    from app.agent_workflow.graph import _route_intent
    state={"question":"compare AMD and AAPL","intent":"saved_data",
           "entities":{"symbol":None,"symbols":["AMD","AAPL"]},"node_log":[]}
    routed=asyncio.run(_route_intent(state))
    assert routed["next_action"]!="clarify"
    assert [item["arguments"]["symbol"] for item in routed["required_sources"]]==["AMD","AAPL"]


def test_legacy_knowledge_observation_is_gated_but_exact_saved_analysis_is_business():
    from app.agent_workflow.graph import _execute_tools, _after_tool
    async def execute(_name,_args):
        return {"ok":True,"data":[{"symbol":"AMD","excerpt":"source"}],"_retrieval_mode":"legacy"}
    runtime=AgentRuntimeContext("custom","model","runtime",executor=execute)
    async def run(name,args,intent):
        return await _execute_tools({"question":"AMD","messages":[],"intent":intent,
            "pending_tool":{"name":name,"arguments":args},"started_at":__import__("time").monotonic(),
            "node_log":[]},type("Runtime",(),{"context":runtime})())
    knowledge=asyncio.run(run("query_materials",{"symbol":"AMD","keyword":"AMD"},"source_qa"))
    business=asyncio.run(run("query_material_analysis",{"symbol":"AMD"},"saved_data"))
    assert _after_tool(knowledge)=="confidence_gate"
    assert _after_tool(business)=="main_agent"
