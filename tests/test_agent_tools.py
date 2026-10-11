from app.agent_tools import TOOL_REGISTRY, tool_schemas, normalize_agent_symbol
import asyncio


def test_fixed_read_only_registry_and_provider_schemas():
    assert set(TOOL_REGISTRY) == {"query_stock_prices", "query_latest_forecast", "query_materials", "query_sec_filings", "query_research_faq"}
    assert {x["function"]["name"] for x in tool_schemas()} == set(TOOL_REGISTRY)
    assert normalize_agent_symbol(" brk-b ") == "BRK.B"
    assert normalize_agent_symbol("amd") == "AMD"


def test_tool_schemas_are_strict_and_limited():
    price = TOOL_REGISTRY["query_stock_prices"].args_schema
    assert price.model_validate({"symbol": "amd"}).symbol == "AMD"
    try:
        price.model_validate({"symbol": "AMD", "sql": "DROP TABLE"})
    except Exception:
        pass
    else:
        raise AssertionError("unknown fields must be rejected")
    assert price.model_validate({"symbol": "BRK-B"}).symbol == "BRK.B"
    assert price.model_json_schema()["properties"]["limit"]["maximum"] == 30


def test_sec_without_form_omits_nullable_parameter(monkeypatch):
    import app.agent_tools as tools
    seen=[]
    async def query(sql,params): seen.append((sql,params)); return []
    monkeypatch.setattr(tools,"_query",query)
    asyncio.run(tools.query_sec_filings.ainvoke({"symbol":"AAPL","form":None}))
    assert ":form" not in seen[0][0]
    assert "form" not in seen[0][1]


def test_material_search_delegates_natural_language_to_dense_retrieval(monkeypatch):
    import app.agent_tools as tools
    seen=[]
    async def retrieve(symbol,query,limit):
        seen.append((symbol,query,limit))
        return {"ok":True,"data":[],"source":"knowledge_documents","as_of":None}
    monkeypatch.setattr(tools,"retrieve_materials",retrieve)
    result=asyncio.run(tools.query_materials.ainvoke({"symbol":"AAPL","keyword":"查FY2025收入"}))
    assert seen==[("AAPL","查FY2025收入",5)]
    assert result=={"ok":True,"data":[],"source":"knowledge_documents","as_of":None}
    schema=tools.query_materials.args_schema.model_json_schema()
    assert set(schema["properties"])=={"symbol","keyword","limit"}


def test_forecast_jsonb_null_falls_through_to_baseline(monkeypatch):
    import app.agent_tools as tools
    async def query(sql,params):
        assert "forecast_versions_v2" in sql and "forecast_snapshots" not in sql
        return [{"id":"v","version_no":1,"decision_probabilities":"null","joint_probabilities":None,"baseline_probabilities":{"bearish":.2,"neutral":.3,"bullish":.5},"research_brief":None,"created_at":None}]
    monkeypatch.setattr(tools,"_query",query)
    result=asyncio.run(tools.query_latest_forecast.ainvoke({"symbol":"AAPL"}))
    assert result["data"][0]["probabilities"]=={"bearish":.2,"neutral":.3,"bullish":.5}
