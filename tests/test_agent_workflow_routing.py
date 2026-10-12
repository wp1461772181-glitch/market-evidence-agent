import pytest

from app.agent_workflow.routing import classify_intent, route_for_intent, resolve_focus


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ('{"intent":"saved_data","symbol":"aapl"}', {"intent": "saved_data", "symbol": "AAPL", "symbols": ["AAPL"], "material_id": None, "web_required": False, "original_required": False, "analysis_required": False}),
        ('{"intent":"compound_research","symbol":"MSFT"}', {"intent": "compound_research", "symbol": "MSFT", "symbols": ["MSFT"], "material_id": None, "web_required": False, "original_required": True, "analysis_required": False}),
    ],
)
def test_classifier_normalizes_only_valid_allowlisted_json(label, expected):
    assert classify_intent(label) == expected


@pytest.mark.parametrize("payload", ["not json", '{"intent":"arbitrary"}', '[]'])
def test_classifier_fails_closed_on_unknown_or_malformed_labels(payload):
    assert classify_intent(payload)["intent"] == "clarify"


def test_saved_data_route_skips_knowledge_gate():
    assert route_for_intent("saved_data") == ("query_stock_prices", False)


def test_explicit_symbol_switch_clears_incompatible_material_focus():
    assert resolve_focus("AAPL", "brief-1", {"symbol": "MSFT"}) == ("MSFT", None)


def test_runtime_secret_is_not_rendered_and_checkpoint_thread_is_owner_scoped():
    from app.agent_workflow.state import AgentRuntimeContext, checkpoint_thread_id

    runtime = AgentRuntimeContext("custom", "model", "sentinel-secret", owner_hash="owner-a", conversation_id="11111111-1111-4111-8111-111111111111")
    rendered = repr(runtime)
    assert "sentinel-secret" not in rendered
    first = checkpoint_thread_id("owner-a", "11111111-1111-4111-8111-111111111111")
    same = checkpoint_thread_id("owner-a", "11111111-1111-4111-8111-111111111111")
    other = checkpoint_thread_id("owner-b", "11111111-1111-4111-8111-111111111111")
    assert first == same and first != other
    assert "sentinel-secret" not in first


def test_checkpoint_uri_uses_postgres_driver_without_touching_request_secret():
    from app.agent_workflow.checkpoints import postgres_saver_url

    assert postgres_saver_url("postgresql+psycopg://db-user@localhost/db") == "postgresql://db-user@localhost/db"
    with pytest.raises(ValueError):
        postgres_saver_url("sqlite:///tmp/test.db")
