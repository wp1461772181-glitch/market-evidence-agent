"""C1–C14 migration: retained independent unit contracts. Legacy planner/node-injection
checks were replaced by production direct-loop, consent and provenance tests; see
docs/reports/agent-routing-test-migration.md. Originals are in Git main 81f62be.
"""
from __future__ import annotations
import pytest

@pytest.mark.parametrize("name,args",[
 ("query_stock_prices",{"symbol":"not a symbol"}),
 ("query_latest_forecast",{"symbol":""}),
 ("query_materials",{"symbol":"AMD","keyword":""}),
 ("query_sec_filings",{"symbol":"AMD","filed_year":1800}),
 ("query_research_faq",{"keyword":""}),
 ("query_material_analysis",{"symbol":"AMD","unexpected":"value"}),
 ("search_material_analysis",{"symbol":"AMD","keyword":"q","limit":99}),
 ("web_research",{"query_or_url":"x","days_back":0}),
])
def test_all_catalog_schemas_reject_invalid_arguments_before_handler(name,args):
    from app.agent_tools import TOOL_REGISTRY
    with pytest.raises(Exception): TOOL_REGISTRY[name].args_schema.model_validate(args)


def test_catalog_exposes_no_delete_or_secret_configuration_tools():
    from app.agent_tools import TOOL_REGISTRY
    forbidden={"delete_database","clear_history","get_api_key","set_model_key","change_login_code"}
    assert not forbidden.intersection(TOOL_REGISTRY)


def test_legacy_watchlist_field_is_ignored_by_request_model():
    from app.agent_chat_stream import StreamRequest
    request=StreamRequest.model_validate({"provider":"custom","base_url":"https://example.com/v1",
        "model":"test-model","api_key":"not-a-real-key","message":"hello","watchlist":["malformed old value"]})
    assert request.message=="hello" and not hasattr(request,"watchlist")
