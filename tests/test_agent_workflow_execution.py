import asyncio
import json

from app.agent_workflow.graph import build_workflow
from app.agent_workflow.state import AgentRuntimeContext


def run_graph(intent, question, decisions=(), results=None, seen_messages=None):
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
        if seen_messages is not None: seen_messages.extend(_messages)
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


def test_generic_chinese_report_overview_uses_only_same_source_ai_when_original_is_xbrl():
    source = "00000000-0000-4000-8000-000000000021"
    original = {"ok": True, "source": "sec_filing_inventory (saved inventory)", "data": [{
        "id": source, "symbol": "AAPL", "accession_number": "0000000000-26-000021",
        "form": "10-Q", "body_hash_valid": True, "index_status": "index_pending",
        "excerpt_coverage": "partial_excerpt", "excerpt": "<xbrli:context>2026</xbrli:context>" * 40,
        "source_url": "https://www.sec.gov/Archives/report.htm"}]}
    analysis = {"ok": True, "source": "material_analysis_versions", "data": [{
        "source_id": source, "source_type": "official_filing", "analysis_id": "analysis-21", "version_no": 2,
        "created_at": "2026-10-10T00:00:00+00:00", "model": "fixture-model",
        "summary": "The saved AI analysis describes sales and margin changes.",
        "source_url": "https://www.sec.gov/Archives/report.htm",
        "classification": "saved_ai_analysis_not_official_source_text"}]}
    messages = []
    events, calls = run_graph("saved_data", "帮我看下苹果最新财报的简介", results={
        "query_sec_filings": original, "query_material_analysis": analysis}, seen_messages=messages)
    assert [call[0] for call in calls if isinstance(call, tuple) and call[0].startswith("query_")] == [
        "query_sec_filings", "query_material_analysis"]
    assert ("query_material_analysis", {"symbol": "AAPL", "source_id": source}) in calls
    assert any("original_excerpt_insufficient" in str(message) for message in messages)
    assert any("saved_ai_analysis_not_official_source_text" in str(message) for message in messages)
    assert events[-1]["event"] == "done"

    wrong = {**analysis, "data": [{**analysis["data"][0], "source_id": "00000000-0000-4000-8000-000000000022"}]}
    events, calls = run_graph("saved_data", "帮我看下苹果最新财报的简介", results={
        "query_sec_filings": original, "query_material_analysis": wrong})
    assert "answer" not in calls and events[-1]["reason"] == "overview_original_insufficient_no_analysis"
    events, calls = run_graph("saved_data", "帮我看下苹果最新财报的简介", results={
        "query_sec_filings": original, "query_material_analysis": {"ok": True, "data": [], "error_code": "no_analysis"}})
    assert "answer" not in calls and events[-1]["reason"] == "overview_original_insufficient_no_analysis"
    events, calls = run_graph("saved_data", "帮我看下苹果最新财报的原文简介", results={
        "query_sec_filings": original, "query_material_analysis": analysis})
    assert not any(isinstance(call, tuple) and call[0] == "query_material_analysis" for call in calls)


def test_toc_item_and_xbrl_context_do_not_block_exact_source_secondary():
    source = "00000000-0000-4000-8000-000000000031"
    original = {"ok": True, "data": [{
        "id": source, "symbol": "AAPL", "accession_number": "0000000000-26-000031",
        "form": "10-Q", "body_hash_valid": True, "excerpt_coverage": "partial_excerpt",
        "excerpt": ("FORM 10-Q\nTABLE OF CONTENTS\nItem 1. Financial Statements ........ 3\n"
                    + "<xbrli:context id='revenue-2026'>2026</xbrli:context>\n" * 20),
        "source_url": "https://www.sec.gov/Archives/report.htm"}]}
    analysis = {"ok": True, "data": [{
        "source_id": source, "source_type": "official_filing", "analysis_id": "analysis-31",
        "summary": "Saved AI analysis of the filing's sales and margins.",
        "classification": "saved_ai_analysis_not_official_source_text"}]}
    messages = []
    events, calls = run_graph("saved_data", "帮我看下苹果最新财报的简介", results={
        "query_sec_filings": original, "query_material_analysis": analysis}, seen_messages=messages)
    assert ("query_material_analysis", {"symbol": "AAPL", "source_id": source}) in calls
    assert any("original_excerpt_insufficient" in str(message) for message in messages)
    assert events[-1]["event"] == "done"


def test_readable_financial_rows_keep_original_overview_without_secondary():
    source = "00000000-0000-4000-8000-000000000032"
    original = {"ok": True, "data": [{
        "id": source, "symbol": "AAPL", "accession_number": "0000000000-26-000032",
        "form": "10-Q", "body_hash_valid": True, "excerpt_coverage": "partial_excerpt",
        "excerpt": ("FORM 10-Q\nTABLE OF CONTENTS\nItem 1. Financial Statements ........ 3\n"
                    "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS\n"
                    "Three Months Ended June 27, 2026 and June 28, 2025\n"
                    "Net sales\t94,036\t85,777\nCost of sales\t48,822\t46,099\n"
                    "Gross margin\t45,214\t39,678\n" + "Notes to financial statements. " * 8),
        "source_url": "https://www.sec.gov/Archives/report.htm"}]}
    events, calls = run_graph("saved_data", "帮我看下苹果最新财报的简介", results={
        "query_sec_filings": original})
    assert not any(isinstance(call, tuple) and call[0] == "query_material_analysis" for call in calls)
    assert "answer" in calls and events[-1]["event"] == "done"


def test_accession_digits_are_not_interpreted_as_requested_period():
    from app.agent_workflow.graph import deterministic_confidence_gate
    question = "AMD 0000320193-26-000001 这份财报的营收主题是什么"
    result = {"ok": True, "source": "knowledge_chunks", "data": [{"id": "1", "symbol": "AMD",
        "excerpt": "AMD revenue grew in the filing; 营收 increased."}]}
    assert deterministic_confidence_gate(question, result, verified_symbols=["AMD"])[0]


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
    assert events[-1]["reason"] == "no_relevant_dated_article"

    row = {**row, "title": "AMD announces new products",
           "excerpt": "AMD announced new products in its company update. The company described the product launch in detail."}
    events, calls = run_graph("web_research", question, results={"web_research": {**result, "ok": True, "data": [row]}})
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
    assert "answer" in calls
    assert events[-1]["reason"] != "repeated_tool_call"


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
    assert [item["name"] for item in plan]==["query_sec_filings","search_material_analysis","web_research"]


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
        return {"usage":{"total_tokens":29000}}, {"content":'{"intent":"saved_data","confidence":0.9}'}
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
        if messages[0]["content"].startswith("Classify the user"):
            return {"usage":{"total_tokens":10}}, {"content":'{"intent":"general","confidence":0.9}'}
        return {"usage":{"total_tokens":1}}, {"tool_calls":[]}
    monkeypatch.setattr(provider,"decision_request",decide)
    async def run():
        async def injected_classifier(_question):
            return '{"intent":"general","confidence":0.9}'
        runtime=AgentRuntimeContext("deepseek","model","runtime",classifier=injected_classifier)
        return await build_workflow().ainvoke({"question":"Explain prime numbers","messages":[{"role":"user","content":"Explain prime numbers"}],
            "token_usage":30000,"usage_mode":"actual"},
            {"configurable":{"thread_id":"actual-budget"}},context=runtime)
    # The resolver resets turn accounting; use a very large provider final usage.
    state=asyncio.run(run())
    assert state["gate_reason"]=="turn_token_budget"
    assert state["token_usage"]==35001 and state["usage_mode"]=="estimated"
    assert state["terminal_status"]=="partially_succeeded"


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


def test_compound_partial_never_exposes_rejected_evidence_or_citation():
    captured = []
    async def classifier(_):
        return '{"intent":"comparison_research","confidence":0.95}'
    async def decide(_messages, _schemas):
        return {"usage":{"total_tokens":20}}, {"tool_calls":[]}
    async def execute(name, args):
        if args["symbol"] == "AAPL":
            return {"ok":True,"data":[{"title":"AAPL unsupported","excerpt":"FAILED_EVIDENCE_SENTINEL", "source_url":"https://example.com/bad"}],"source":"knowledge"}
        return {"ok":True,"data":[{"title":"AMD revenue","excerpt":"AMD revenue increased in the latest report.","source_url":"https://example.com/good"}],"source":"knowledge"}
    async def answer(messages):
        captured.extend(messages)
        yield "AMD revenue is supported; AAPL evidence is missing."
    runtime=AgentRuntimeContext("custom","model","runtime",classifier=classifier,decider=decide,executor=execute,answerer=answer)
    async def run():
        return [event async for event in build_workflow().astream(
            {"question":"比较 AAPL 和 AMD 营收", "messages":[{"role":"user","content":"比较 AAPL 和 AMD 营收"}]},
            {"configurable":{"thread_id":"partial-sentinel"}},context=runtime,stream_mode="custom")]
    events=asyncio.run(run())
    assert "FAILED_EVIDENCE_SENTINEL" not in json.dumps(captured,ensure_ascii=False)
    assert "FAILED_EVIDENCE_SENTINEL" not in json.dumps(events,ensure_ascii=False)
    done=events[-1]
    assert done["status"] == "partially_succeeded"
    assert all(item["source_url"] != "https://example.com/bad" for item in done["citations"])
    assert "AMD revenue" in json.dumps(captured,ensure_ascii=False)


def test_exact_form_accession_is_one_stock_and_direct_ai_summary():
    from app.agent_workflow.request_resolution import extract_slots
    from app.agent_workflow.graph import _required
    question="给我 AAPL 这份财报10-Q 0000320193-26-000020 的 AI 分析摘要"
    slots=extract_slots(question)
    assert slots["symbols"] == ["AAPL"]
    plan=_required({"question":question,"intent":"analysis_qa","entities":slots})
    assert plan == [{"name":"query_material_analysis","arguments":{
        "symbol":"AAPL","financial_only":True,"form":"10-Q","accession_number":"0000320193-26-000020"}}]


def test_news_query_uses_company_keywords_without_chat_instruction():
    from app.agent_workflow.graph import _expanded_web_query
    query=_expanded_web_query("帮我联网搜索一条苹果公司相关的最新新闻然后发给我",["AAPL"])
    assert "AAPL" in query and "Apple" in query and "latest" in query
    assert "帮我" not in query and "蘋果" in query


def test_rejected_citation_is_removed_before_next_verified_leaf():
    from app.agent_workflow.graph import _confidence_gate
    bad={"name":"query_materials","arguments":{"symbol":"AAPL"},"result":{
        "ok":True,"data":[{"title":"AAPL","excerpt":"FAILED_EVIDENCE_SENTINEL","source_url":"https://example.com/bad"}],"source":"knowledge"},
        "citation_start":0,"call_id":"workflow-1"}
    state={"question":"比较 AAPL 和 AMD 营收","entities":{"symbols":["AAPL","AMD"]},
           "observations":[bad],"citations":[{"ordinal":1,"source_url":"https://example.com/bad"}],
           "messages":[{"role":"user","content":"比较 AAPL 和 AMD 营收"}],
           "task_states":[{"status":"running"}],"required_sources":[]}
    update=asyncio.run(_confidence_gate(state))
    assert update["citations"] == []
    assert "FAILED_EVIDENCE_SENTINEL" not in json.dumps(update["messages"],ensure_ascii=False)
    good={"name":"query_materials","arguments":{"symbol":"AMD"},"result":{
        "ok":True,"data":[{"title":"AMD revenue","excerpt":"AMD revenue rose.","source_url":"https://example.com/good"}],"source":"knowledge"},
        "citation_start":0,"call_id":"workflow-2"}
    next_state={**state,**update,"observations":[update["observations"][-1],good],
                "citations":[{"ordinal":2,"source_url":"https://example.com/good"}],
                "task_states":[*update["task_states"],{"status":"running"}]}
    accepted=asyncio.run(_confidence_gate(next_state))
    assert accepted["gate_passed"]
    assert [item["ordinal"] for item in accepted["citations"]] == [2]


def test_complex_research_can_add_one_validated_useful_tool():
    observation={"ok":True,"data":[{"title":"AMD 2025 revenue","excerpt":"AMD 2025 revenue was reported in the saved filing.",
                                      "source_url":"https://example.com/filing"}],"source":"knowledge"}
    additional={"tool_calls":[{"id":"extra-price","type":"function","function":{
        "name":"query_stock_prices","arguments":json.dumps({"symbol":"AMD"})}}]}
    events,calls=run_graph("compound_research","AMD revenue in 2025 and stock price",[additional],
                           {"query_materials":observation})
    assert [entry[0] for entry in calls if isinstance(entry,tuple)] == ["query_materials","query_stock_prices"]
    assert calls[-1] == "answer"
    assert events[-1]["status"] == "succeeded"


def test_topic_source_binding_and_global_accession_owner_plan():
    from app.agent_workflow.graph import _required
    entities={"symbols":["AAPL","AMD"],"accession_number":"0000320193-26-000020",
              "accession_owner":"AAPL","form":"10-Q","original_required":True}
    plan=_required({"question":"比较 AAPL 和 AMD 财报营收","intent":"compound_research","entities":entities})
    apple=[item for item in plan if item["arguments"].get("symbol")=="AAPL"]
    amd=[item for item in plan if item["arguments"].get("symbol")=="AMD"]
    assert apple[0]["name"] == "query_sec_filings"
    assert apple[0]["arguments"]["accession_number"] == entities["accession_number"]
    assert all("accession_number" not in item["arguments"] for item in amd)
    scoped=_required({"question":"AAPL 10-Q 营收","intent":"source_qa",
                      "entities":{"symbols":["AAPL"],"form":"10-Q"}})
    assert [item["name"] for item in scoped] == ["query_sec_filings","query_materials"]


def test_latest_saved_library_binds_verified_sec_source_without_old_material_fallback():
    old = "00000000-0000-4000-8000-000000000031"
    latest = "00000000-0000-4000-8000-000000000032"
    calls = []
    messages = []
    async def classify(_): return '{"intent":"source_qa","confidence":0.9}'
    async def execute(name, args):
        calls.append((name, args))
        if name == "query_sec_filings":
            return {"ok":True,"data":[{"id":latest,"symbol":"AAPL","body_hash_valid":True,
                "excerpt":"Latest saved SEC filing", "source_url":"https://www.sec.gov/latest"}],"source":"saved SEC"}
        assert name == "query_materials"
        row = {"id":latest if args.get("source_id")==latest else old,
               "excerpt":"Latest saved SEC filing facts" if args.get("source_id")==latest else "Old unrelated material",
               "source_url":"https://www.sec.gov/latest" if args.get("source_id")==latest else "https://example.test/old"}
        return {"ok":True,"data":[row],"source":"saved material"}
    async def answer(items):
        messages.extend(items)
        yield "Latest saved SEC filing facts."
    async def run():
        graph=build_workflow()
        return await graph.ainvoke({"question":"苹果资料库最新资料说了什么","messages":[]},
            {"configurable":{"thread_id":"latest-saved-binding"}},
            context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,executor=execute,answerer=answer))
    state=asyncio.run(run())
    assert calls[0]==("query_sec_filings",{"symbol":"AAPL","limit":1})
    assert calls[1][0]=="query_materials" and calls[1][1]["source_id"]==latest
    assert state["observations"][0]["source_resolution_only"] is True
    assert state["observations"][1]["verified"] is True
    assert state["observations"][1]["result"]["selected_saved_source"] == {
        "source_id":latest,"source_type":"saved SEC filing"}
    assert old not in str(messages)
    assert all(item["source_id"]==latest for item in state["citations"] if item.get("source_id"))


def test_saved_ai_summary_keeps_analysis_route_with_in_memory_checkpoint():
    from langgraph.checkpoint.memory import InMemorySaver
    calls = []
    async def classify(_): return '{"intent":"analysis_qa","confidence":0.9}'
    async def execute(name, args):
        calls.append((name, args))
        return {"ok": True, "data": [{"source_id": "source-a", "symbol": "AAPL",
                "summary": "Saved AI analysis", "source_url": "https://example.test/aapl"}], "source": "fixture"}
    async def answer(_): yield "Saved AI analysis."
    async def run():
        return await build_workflow(InMemorySaver()).ainvoke(
            {"question": "查看 AAPL 已保存的 AI 分析摘要", "messages": []},
            {"configurable": {"thread_id": "saved-ai-summary"}},
            context=AgentRuntimeContext("custom", "model", "synthetic", classifier=classify,
                                        executor=execute, answerer=answer))
    state = asyncio.run(run())
    assert state["intent"] == "analysis_qa"
    assert [name for name, _ in calls] == ["query_material_analysis"]


def test_saved_original_and_analysis_keeps_compound_plan_with_in_memory_checkpoint():
    from langgraph.checkpoint.memory import InMemorySaver
    calls = []
    async def classify(_): return '{"intent":"comparison_research","confidence":0.9}'
    async def execute(name, args):
        calls.append((name, args))
        return {"ok": True, "data": [{"id": "source-a", "symbol": "AAPL",
                "excerpt": "AAPL original filing and AI analysis", "source_url": "https://example.test/aapl"}], "source": "fixture"}
    async def answer(_): yield "Original and analysis."
    async def run():
        return await build_workflow(InMemorySaver()).ainvoke(
            {"question": "查看资料库中 AAPL 原文和 AI 分析", "messages": []},
            {"configurable": {"thread_id": "saved-original-analysis"}},
            context=AgentRuntimeContext("custom", "model", "synthetic", classifier=classify,
                                        executor=execute, answerer=answer))
    state = asyncio.run(run())
    assert state["intent"] == "compound_research"
    assert [name for name, _ in calls] == ["query_materials", "search_material_analysis"]


def test_document_reference_keeps_same_source_only_for_same_goal():
    from langgraph.checkpoint.memory import InMemorySaver
    source = "00000000-0000-4000-8000-000000000044"
    calls = []
    async def classify(question):
        return '{"intent":"saved_data","confidence":0.9}' if "价格" in question else '{"intent":"source_qa","confidence":0.9}'
    async def execute(name, args):
        calls.append((name, args))
        return {"ok": True, "data": [{"id": source, "symbol": args["symbol"],
                "excerpt": "Saved AAPL material", "source_url": "https://example.test/aapl"}], "source": "fixture"}
    async def answer(_): yield "Saved material."
    async def run():
        graph = build_workflow(InMemorySaver())
        config = {"configurable": {"thread_id": "document-reference"}}
        context = AgentRuntimeContext("custom", "model", "synthetic", classifier=classify,
                                      executor=execute, answerer=answer)
        await graph.aupdate_state(config, {"symbol_focus": "AAPL", "material_focus": source})
        same = await graph.ainvoke({"question": "这份资料说了什么", "messages": []}, config, context=context)
        await graph.aupdate_state(config, {"symbol_focus": "AAPL", "material_focus": source})
        action = await graph.ainvoke({"question": "生成 MSFT AI 分析", "messages": []}, config, context=context)
        await graph.aupdate_state(config, {"symbol_focus": "AAPL", "material_focus": source})
        other = await graph.ainvoke({"question": "Apple 最新价格", "messages": []}, config, context=context)
        await graph.aupdate_state(config, {"symbol_focus": "AAPL", "material_focus": source})
        different = await graph.ainvoke({"question": "查看 MSFT 资料库里的材料", "messages": []}, config, context=context)
        return same, action, other, different
    same, action, other, different = asyncio.run(run())
    assert same["symbol_focus"] == "AAPL" and same["material_focus"] == source
    assert same["entities"]["source_id"] == source
    assert any(name == "query_materials" and args.get("source_id") == source for name, args in calls)
    assert action["material_focus"] is None
    assert other["material_focus"] is None
    assert different["symbol_focus"] == "MSFT" and different["material_focus"] is None


def test_latest_saved_library_without_verified_source_never_queries_unscoped_materials():
    calls=[]
    async def classify(_): return '{"intent":"source_qa","confidence":0.9}'
    async def execute(name,args):
        calls.append((name,args))
        return {"ok":True,"data":[{"id":"00000000-0000-4000-8000-000000000032",
             "symbol":"AAPL","body_hash_valid":False}],"source":"saved SEC"}
    async def run():
        return await build_workflow().ainvoke({"question":"苹果资料库最新资料说了什么","messages":[]},
            {"configurable":{"thread_id":"latest-invalid-source"}},
            context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,executor=execute))
    state=asyncio.run(run())
    assert [name for name,_ in calls]==["query_sec_filings"]
    assert any(task.get("error")=="no_source" for task in state["task_states"])


def test_latest_saved_library_rejects_other_source_result_even_after_scoped_call():
    latest="00000000-0000-4000-8000-000000000032"
    old="00000000-0000-4000-8000-000000000031"
    calls=[]
    async def classify(_): return '{"intent":"source_qa","confidence":0.9}'
    async def execute(name,args):
        calls.append((name,args))
        if name=="query_sec_filings":
            return {"ok":True,"data":[{"id":latest,"symbol":"AAPL","body_hash_valid":True}],"source":"saved SEC"}
        return {"ok":True,"data":[{"id":old,"excerpt":"Old unrelated material",
            "source_url":"https://example.test/old"}],"source":"saved material"}
    async def run():
        return await build_workflow().ainvoke({"question":"苹果资料库最新资料说了什么","messages":[]},
            {"configurable":{"thread_id":"latest-wrong-source"}},
            context=AgentRuntimeContext("custom","model","synthetic",classifier=classify,executor=execute))
    state=asyncio.run(run())
    assert calls[1][1]["source_id"]==latest
    assert state["observations"][1]["verified"] is False
    assert old not in str(state["citations"])
