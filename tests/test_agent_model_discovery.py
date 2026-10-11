import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.exceptions import RequestValidationError
from app.agent_chat_api import router, safe_validation_response

app = FastAPI()
app.add_exception_handler(RequestValidationError, safe_validation_response)
app.include_router(router)
client = TestClient(app)

class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, response): self.response = response; self.request = None
    async def handle_async_request(self, request):
        self.request = request
        return self.response(request)
    async def aclose(self): pass

@pytest.fixture
def mock_provider(monkeypatch):
    capture = {}
    transport = MockTransport(lambda request: httpx.Response(200, json=capture.get('payload', {'data': []})))
    class Pinned(httpx.AsyncBaseTransport):
        def __init__(self, **kwargs): pass
        async def handle_async_request(self, request): return await transport.handle_async_request(request)
        async def aclose(self): pass
    monkeypatch.setattr('app.agent_chat_api.PinnedPublicTransport', Pinned)
    return capture, transport

def body(**overrides):
    value = {'provider':'deepseek','api_key':'synthetic-test-key','base_url':'https://api.deepseek.com'}
    value.update(overrides)
    return value

def test_discovery_orders_and_filters_before_selecting_five(mock_provider):
    capture, transport = mock_provider
    capture['payload'] = {'data': [
        {'id':'chat-old','created':10}, {'id':'chat-new','created':50},
        {'id':'embed-v3','created':99,'type':'embedding'}, {'id':'chat-mid','created':30},
        {'id':'chat-top','created':70}, {'id':'chat-second','created':60}, {'id':'chat-fourth','created':20},
        {'id':'chat-new','created':55},
    ]}
    response = client.post('/agent-chat/models', json=body())
    assert response.status_code == 200
    assert [row['id'] for row in response.json()['models']] == ['chat-top','chat-second','chat-new','chat-mid','chat-fourth']
    assert response.json()['ordering'] == 'provider_timestamp_desc'
    assert transport.request.url.path == '/models'
    assert transport.request.headers['authorization'] == 'Bearer synthetic-test-key'

def test_custom_chat_completions_url_is_normalized_and_missing_timestamps_keep_upstream_order(mock_provider):
    capture, transport = mock_provider
    capture['payload'] = {'data':[{'id':'a'},{'id':'b'}]}
    response = client.post('/agent-chat/models', json=body(provider='custom',base_url='https://api.example.test/custom/v2/chat/completions'))
    assert response.status_code == 200
    assert transport.request.url.path == '/custom/v2/models'
    assert [x['id'] for x in response.json()['models']] == ['a','b']
    assert response.json()['ordering'] == 'upstream_order_unknown_recency'

def test_discovery_redacts_validation_and_upstream_error_body(mock_provider):
    _, transport = mock_provider
    transport.response = lambda request: httpx.Response(401, text='do not expose synthetic-test-key')
    response = client.post('/agent-chat/models', json=body())
    assert response.status_code == 502
    assert 'synthetic-test-key' not in response.text
    response = client.post('/agent-chat/models', json=body(provider='custom',base_url='http://127.0.0.1'))
    assert response.status_code == 422
    assert 'synthetic-test-key' not in response.text

def test_fixed_provider_without_verified_listing_contract_reports_unsupported(mock_provider):
    response = client.post('/agent-chat/models', json=body(provider='minimax',base_url='https://api.minimax.cn/v1'))
    assert response.status_code == 200
    assert response.json()['status'] == 'unsupported'
    assert response.json()['models'] == []

@pytest.mark.parametrize(('status','expected','result_status'), [
    (401,502,None),(403,502,None),(404,200,'unsupported'),(405,200,'unsupported'),
    (429,429,None),(302,502,None),(500,502,None),
])
def test_provider_statuses_are_safe_and_unsupported_is_distinct(mock_provider, status, expected, result_status):
    _, transport = mock_provider
    transport.response = lambda request: httpx.Response(status, text='private upstream details and synthetic-test-key')
    response = client.post('/agent-chat/models', json=body())
    assert response.status_code == expected
    assert 'synthetic-test-key' not in response.text
    assert 'private upstream details' not in response.text
    if result_status: assert response.json()['status'] == result_status

def test_discovery_filters_modalities_and_types_before_sorting_and_slicing(mock_provider):
    capture, _ = mock_provider
    capture['payload'] = {'data':[
      {'id':'video-chat-llm','created':40,'architecture':{'input_modalities':['text','video'],'output_modalities':['text']}},
      {'id':'embed-text','created':99,'type':'embedding','input_modalities':['text'],'output_modalities':['text']},
      {'id':'vision-chat','created':30,'input_modalities':['text','image'],'output_modalities':['text']},
      {'id':'image-only','created':100,'input_modalities':['image'],'output_modalities':['image']},
      {'id':'named','name':'Named model','published_time':'2026-01-01T00:00:00Z'},
      {'id':'invalid id','created':200}, None, {'name':'missing id'},
    ]}
    result = client.post('/agent-chat/models', json=body()).json()
    assert [model['id'] for model in result['models']] == ['named','video-chat-llm','vision-chat']
    assert result['models'][0]['name'] == 'Named model'
    assert result['ordering'] == 'provider_timestamp_desc'

@pytest.mark.parametrize('row', [
    {'id':'image-only','input_modalities':['text'],'output_modalities':['image']},
    {'id':'image-mixed','input_modalities':['text','image'],'output_modalities':['text','image']},
    {'id':'arch-image-only','architecture':{'input_modalities':['text'],'output_modalities':['image']}},
    {'id':'arch-image-mixed','architecture':{'input_modalities':['text','image'],'output_modalities':['text','image']}},
    {'id':'type-image','type':'image_generation','input_modalities':['text'],'output_modalities':['text']},
])
def test_discovery_excludes_generative_outputs_and_explicit_nonchat_types(mock_provider, row):
    capture, _ = mock_provider
    capture['payload'] = {'data':[row, {'id':'vision-chat','input_modalities':['text','image'],'output_modalities':['text']}]}
    result = client.post('/agent-chat/models', json=body()).json()
    assert [model['id'] for model in result['models']] == ['vision-chat']

def test_discovery_rejects_all_malformed_but_accepts_valid_empty_and_nondated_models(mock_provider):
    capture, _ = mock_provider
    capture['payload'] = {'data':[None, {'oops':'unexpected'}, 'row']}
    invalid = client.post('/agent-chat/models', json=body())
    assert invalid.status_code == 502 and invalid.json()['detail'] == 'model_discovery_incompatible_response'
    capture['payload'] = {'data':[]}
    assert client.post('/agent-chat/models', json=body()).json()['status'] == 'empty'
    capture['payload'] = {'data':[{'id':'undated'},{'id':'bad-time','created':'n/a'},{'id':'dated','created':10}]}
    result = client.post('/agent-chat/models', json=body()).json()
    assert [row['id'] for row in result['models']] == ['dated','undated','bad-time']
    assert result['ordering'] == 'provider_timestamp_desc_with_undated'

def test_openrouter_accepts_absent_or_empty_key_without_authorization(mock_provider):
    capture, transport = mock_provider
    capture['payload'] = {'data':[{'id':'public-model'}]}
    for request_body in (body(provider='openrouter', api_key=''), {'provider':'openrouter','base_url':'https://openrouter.ai/api/v1'}):
        response = client.post('/agent-chat/models', json=request_body)
        assert response.status_code == 200
        assert 'authorization' not in transport.request.headers

def test_siliconflow_exact_preset_uses_chat_query_and_override_does_not(mock_provider):
    capture, transport = mock_provider
    capture['payload'] = {'data':[{'id':'BAAI/bge-m3','object':'model'}, {'id':'Qwen/Qwen3','object':'model'}]}
    result = client.post('/agent-chat/models', json=body(provider='siliconflow',base_url='https://api.siliconflow.cn/v1')).json()
    assert transport.request.url.path == '/v1/models'
    assert transport.request.url.query == b'sub_type=chat'
    assert result['models'] == [{'id':'Qwen/Qwen3','name':'Qwen/Qwen3'}]
    client.post('/agent-chat/models', json=body(provider='siliconflow',base_url='https://custom.example/v1'))
    assert transport.request.url.query == b''

def test_minimax_override_is_attempted_but_preset_is_unsupported(mock_provider):
    _, transport = mock_provider
    assert client.post('/agent-chat/models', json=body(provider='minimax',base_url='https://api.minimax.cn/v1')).json()['status'] == 'unsupported'
    client.post('/agent-chat/models', json=body(provider='minimax',base_url='https://custom.example/v1'))
    assert transport.request.url.path == '/v1/models'

def test_oversized_and_timeout_provider_responses_are_bounded(mock_provider):
    _, transport = mock_provider
    transport.response = lambda request: httpx.Response(200, content=b'x' * (8_000_001))
    oversized = client.post('/agent-chat/models', json=body())
    assert oversized.status_code == 502 and oversized.json()['detail'] == 'model_discovery_response_too_large'
    def timeout(_request): raise httpx.ReadTimeout('synthetic timeout')
    transport.response = timeout
    timed_out = client.post('/agent-chat/models', json=body())
    assert timed_out.status_code == 504 and timed_out.json()['detail'] == 'model_discovery_timeout'
