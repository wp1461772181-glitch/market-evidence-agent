"""UX batch 3 (2026-10-28): the settings page keeps only the server model catalog.

Its add/edit form tests a model before saving and lists the provider's models, with the key typed, an
environment variable's, or (editing, same provider and endpoint) the entry's stored one.  Owner turns
outside a bot and material analysis pick a catalog entry by name; no key comes from the browser.
"""
import pytest

from app import agent_model_catalog as catalog
from app.agent_chat_api import ChatResponse
from tests.test_agent_bots import GUEST, H, KEY_A, KEY_B, _keys_absent, owner  # noqa: F401  (fixture)


@pytest.fixture
def probes(monkeypatch):
    """Record what the provider would have been sent instead of calling it."""
    import app.agent_chat_api as chat_api
    sent = {"hello": [], "models": []}

    async def hello(request, messages, max_tokens):
        sent["hello"].append(request)
        return ChatResponse(content="hi", model=request.model)

    async def discover(request):
        sent["models"].append(request)
        return {"status": "available", "models": [{"id": "deepseek-chat", "name": "DeepSeek Chat"}], "ordering": "upstream_order_unknown_recency"}
    monkeypatch.setattr(chat_api, "_limited_call", hello)
    monkeypatch.setattr(chat_api, "discover_models", discover)
    return sent


def test_test_and_discovery_use_the_typed_key_and_never_echo_it(owner, client, probes):
    body = {"provider": "deepseek", "model": "deepseek-chat", "api_key": KEY_A}
    tested = client.post("/agent-chat/model-catalog/test", headers=H, json=body)
    assert tested.status_code == 200 and tested.json() == {"ok": True, "model": "deepseek-chat"}
    assert probes["hello"][0].api_key.get_secret_value() == KEY_A
    listed = client.post("/agent-chat/model-catalog/models", headers=H, json={"provider": "deepseek", "api_key": KEY_A})
    assert listed.json()["models"][0]["id"] == "deepseek-chat"
    for response in (tested, listed):
        _keys_absent(response.text)
    # Nothing was saved by testing.
    assert catalog.list_public()["models"] == []
    assert client.post("/agent-chat/model-catalog/test", headers={"X-Chat-Session": GUEST}, json=body).status_code == 403


def test_env_key_and_stored_key_are_used_server_side(owner, client, probes, monkeypatch):
    monkeypatch.setenv("BRAVO_API_KEY", KEY_B)
    assert client.post("/agent-chat/model-catalog/test", headers=H, json={
        "provider": "qwen", "model": "qwen-plus", "key_env": "BRAVO_API_KEY"}).status_code == 200
    assert probes["hello"][-1].api_key.get_secret_value() == KEY_B
    # The server's own secrets cannot be borrowed by a test either.
    assert client.post("/agent-chat/model-catalog/test", headers=H, json={
        "provider": "qwen", "model": "m", "key_env": "MODEL_CREDENTIAL_KEY"}).status_code == 422
    catalog.save_entry("alpha", provider="deepseek", model="deepseek-chat", api_key=KEY_A, create=True)
    # Editing an entry: its stored key, as long as provider and endpoint stay.
    assert client.post("/agent-chat/model-catalog/test", headers=H, json={
        "name": "alpha", "provider": "deepseek", "model": "deepseek-reasoner"}).status_code == 200
    assert probes["hello"][-1].api_key.get_secret_value() == KEY_A
    moved = client.post("/agent-chat/model-catalog/test", headers=H, json={
        "name": "alpha", "provider": "custom", "base_url": "https://llm.example.com/v1", "model": "m"})
    assert moved.status_code == 422 and moved.json()["detail"] == "key_required"
    assert client.post("/agent-chat/model-catalog/test", headers=H, json={
        "provider": "deepseek", "model": "deepseek-chat"}).status_code == 422  # no key at all


def test_owner_turn_outside_a_bot_takes_its_model_from_the_catalog(owner, client, monkeypatch):
    import app.agent_chat_stream as stream
    # No catalog entry yet: the turn is refused with a code, before anything runs.
    refused = client.post("/agent-chat/stream", headers=H, json={"message": "你好", "catalog_model": ""})
    assert refused.status_code == 409 and refused.json()["detail"] == "model_not_configured"
    assert client.post("/agent-chat/stream", headers={"X-Chat-Session": GUEST},
                       json={"message": "你好", "catalog_model": ""}).status_code == 403
    catalog.save_entry("alpha", provider="deepseek", model="deepseek-chat", api_key=KEY_A, is_default=True, create=True)
    resolved = stream._catalog_turn("", H["X-Chat-Session"])
    assert resolved["name"] == "alpha" and resolved["api_key"] == KEY_A


def test_material_analysis_settings_name_a_catalog_model():
    from pydantic import ValidationError
    from app.material_analysis_api import AnalysisJobRequest, AnalysisSettingsRequest
    assert AnalysisSettingsRequest(catalog_model="alpha").catalog_model == "alpha"
    with pytest.raises(ValidationError):
        AnalysisSettingsRequest(catalog_model="alpha", provider="deepseek", model="m")
    with pytest.raises(ValidationError):
        AnalysisSettingsRequest()
    assert AnalysisSettingsRequest(provider="deepseek", model="m").catalog_model is None
    assert AnalysisJobRequest(idempotency_key="k", catalog_model="alpha").catalog_model == "alpha"
    with pytest.raises(ValidationError):
        AnalysisJobRequest(idempotency_key="k", catalog_model="alpha", provider="deepseek", model="m", api_key=KEY_A)
