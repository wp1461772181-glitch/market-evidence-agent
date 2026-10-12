"""Independent acceptance of user-controlled endpoints and safe configuration boundaries."""
import asyncio
import json
import socket
import httpx
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.agent_chat_api import ChatRequest, PinnedPublicTransport
from app.agent_chat_stream import StreamRequest

FAKE='parent-demo-key-not-real'

@pytest.fixture(autouse=True)
def setup_agent_checkpoint(disposable_database):
 import os
 from app.agent_workflow.checkpoints import open_postgres_checkpointer
 async def setup():
  async with open_postgres_checkpointer(os.environ["DATABASE_URL"]) as saver: await saver.setup()
 asyncio.run(setup())

def body(url,stream=False,provider='custom'):
 d={'provider':provider,'model':'user-model','api_key':FAKE,'base_url':url}
 d['message' if stream else 'messages']='hello' if stream else []
 return d

@pytest.mark.parametrize('url',[
 'http://example.com/v1','https://user:pass@example.com/v1',
 'https://example.com/v1?api_key='+FAKE,'https://example.com/v1#key',
 'https://example.com\\@127.0.0.1/v1','https://%31%32%37.0.0.1/v1',
 'https://example.com/v1\n','https://example.com:0/v1',
])
@pytest.mark.parametrize('model,stream',[(ChatRequest,False),(StreamRequest,True)])
def test_both_request_schemas_reject_unsafe_address(model,stream,url):
 with pytest.raises(ValidationError):model(**body(url,stream))

@pytest.mark.parametrize('model,stream',[(ChatRequest,False),(StreamRequest,True)])
def test_custom_requires_explicit_endpoint_before_io(model,stream):
 d=body(None,stream)
 with pytest.raises(ValidationError):model(**d)

@pytest.mark.parametrize('path,stream',[('/agent-chat/hello',False),('/agent-chat/stream',True)])
def test_validation_never_echoes_stored_key(monkeypatch,path,stream):
 with TestClient(app) as client:
  r=client.post(path,json=body('https://user:'+FAKE+'@example.com',stream),headers={'X-Chat-Session':'test-session-'+'a'*48})
 assert r.status_code==422
 assert FAKE not in r.text

@pytest.mark.parametrize('addresses',[
 ['127.0.0.1'],['10.1.2.3'],['169.254.169.254'],['100.64.0.1'],['::1'],
 ['::ffff:127.0.0.1'],['8.8.8.8','192.168.0.2'],['ff02::1'],['::ffff:8.8.8.8'],
])
def test_actual_transport_rejects_nonpublic_and_mixed_answers(monkeypatch,addresses):
 called=[]
 async def fake_resolve(self,host,port,*args,**kwargs):
  return [(socket.AF_INET6 if ':' in ip else socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in addresses]
 monkeypatch.setattr(asyncio.BaseEventLoop,'getaddrinfo',fake_resolve)
 async def inner(req):called.append(req);return httpx.Response(200,json={})
 async def run():
  guard=PinnedPublicTransport(inner=httpx.MockTransport(inner))
  try:
   with pytest.raises(httpx.HTTPError):await guard.handle_async_request(httpx.Request('POST','https://research.example/v1/chat/completions',headers={'Authorization':'Bearer '+FAKE}))
  finally:await guard.aclose()
 asyncio.run(run());assert not called

def test_pinning_keeps_hostname_tls_and_api_path(monkeypatch):
 calls=[];seen=[]
 async def fake_resolve(self,host,port,*args,**kwargs):
  calls.append(host)
  # A second resolution would return localhost; connection must use the first validated IP.
  return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8' if len(calls)==1 else '127.0.0.1',port))]
 monkeypatch.setattr(asyncio.BaseEventLoop,'getaddrinfo',fake_resolve)
 async def inner(req):
  seen.append(req)
  return httpx.Response(200,json={'ok':True})
 async def run():
  guard=PinnedPublicTransport(inner=httpx.MockTransport(inner))
  try:await guard.handle_async_request(httpx.Request('POST','https://research.example/custom/v1/chat/completions',headers={'Authorization':'Bearer '+FAKE}))
  finally:await guard.aclose()
 asyncio.run(run())
 assert len(calls)==1 and seen[0].url.host=='8.8.8.8'
 assert seen[0].headers['host']=='research.example'
 assert seen[0].extensions['sni_hostname']=='research.example'
 assert isinstance(seen[0].extensions['sni_hostname'],str)
 assert seen[0].url.path=='/custom/v1/chat/completions'
 assert seen[0].headers['authorization']=='Bearer '+FAKE

@pytest.mark.parametrize('prefix',['https://provider.example/compatible-mode/v1/','https://provider.example/custom/chat/completions'])
def test_hello_preserves_custom_prefix_and_appends_once(monkeypatch,prefix):
 seen=[]
 real_client=httpx.AsyncClient
 def capture(req):
  seen.append(req);return httpx.Response(200,json={'choices':[{'message':{'content':'hello'}}],'model':'user-model'})
 monkeypatch.setattr('app.agent_chat_api.httpx.AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(capture)))
 with TestClient(app) as client:r=client.post('/agent-chat/hello',json=body(prefix))
 assert r.status_code==200,r.text
 expected=prefix.rstrip('/')
 if not expected.endswith('/chat/completions'):expected+='/chat/completions'
 assert str(seen[0].url)==expected
 assert seen[0].headers['authorization']=='Bearer '+FAKE
 assert json.loads(seen[0].content)['model']=='user-model'
 assert FAKE not in r.text

def test_every_preset_can_make_hello_request_without_key_default(monkeypatch):
 from app.agent_chat_api import PROVIDER_NAMES,BASE_URLS
 assert {'deepseek','kimi','minimax','qwen','zhipu','doubao','hunyuan','baidu','siliconflow','openrouter','custom'}==set(PROVIDER_NAMES)
 real_client=httpx.AsyncClient;seen=[]
 def capture(req):
  seen.append(req);return httpx.Response(200,json={'choices':[{'message':{'content':'hello'}}],'model':'user-model'})
 monkeypatch.setattr('app.agent_chat_api.httpx.AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(capture)))
 with TestClient(app) as client:
  for provider in set(PROVIDER_NAMES)-{'custom'}:
   d=body(None,provider=provider);d.pop('base_url')
   r=client.post('/agent-chat/hello',json=d);assert r.status_code==200,(provider,r.text)
 assert len(seen)==10
 assert all(r.headers['authorization']=='Bearer '+FAKE for r in seen)

# Regression: the isolated demo can still rewire fixed providers without hitting paid network.
def test_existing_local_demo_provider_rewire_is_respected(monkeypatch):
 from app.agent_chat_api import PROVIDERS
 real_client=httpx.AsyncClient;seen=[]
 monkeypatch.setitem(PROVIDERS,'deepseek','http://127.0.0.1:8899/chat/completions')
 def capture(req):
  seen.append(req);return httpx.Response(200,json={'choices':[{'message':{'content':'DEMO MOCK'}}],'model':'user-model'})
 monkeypatch.setattr('app.agent_chat_api.httpx.AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(capture)))
 d=body(None,provider='deepseek');d.pop('base_url')
 with TestClient(app) as client:r=client.post('/agent-chat/hello',json=d)
 assert r.status_code==200
 assert str(seen[0].url)=='http://127.0.0.1:8899/chat/completions'

@pytest.mark.parametrize('model,stream',[(ChatRequest,False),(StreamRequest,True)])
def test_public_https_endpoint_can_use_custom_tls_port(model,stream):
 value=model(**body('https://provider.example:8443/custom/v1/',stream))
 assert value.base_url.startswith('https://provider.example:8443/custom/v1')


def test_custom_endpoint_and_model_reach_both_sse_requests(monkeypatch):
 real_client=httpx.AsyncClient;seen=[]
 def capture(req):
  b=json.loads(req.content);seen.append((str(req.url),b,req.headers['authorization']))
  if b.get('stream'):
   raw=b'data: {"choices":[{"delta":{"content":"DEMO CUSTOM ANSWER"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
   return httpx.Response(200,headers={'Content-Type':'text/event-stream'},content=raw)
  if 'Classify a market evidence chat request' in b['messages'][0]['content']:
   return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{"intent":"general","symbol":null}'}}]})
  return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ready'}}]})
 monkeypatch.setattr('app.agent_chat_provider.httpx.AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(capture)))
 with TestClient(app) as client:
  r=client.post('/agent-chat/stream',json=body('https://provider.example/custom/v1/chat/completions',True),headers={'X-Chat-Session':'parent-config-'+'c'*48})
 assert r.status_code==200,r.text
 assert 'event: done' in r.text,r.text
 assert len(seen)==3
 assert all(url=='https://provider.example/custom/v1/chat/completions' for url,_,_ in seen)
 assert all(b['model']=='user-model' and auth=='Bearer '+FAKE for _,b,auth in seen)
 assert FAKE not in r.text
 assert seen[0][1]['tool_choice']=='auto' and 'Classify a market evidence chat request' in seen[0][1]['messages'][0]['content']
 assert seen[1][1]['tool_choice']=='auto' and seen[2][1]['tool_choice']=='none'

@pytest.mark.parametrize('path,expect',[('/agent-chat/hello',200),('/agent-chat/messages',502)])
def test_reasoning_only_response_can_verify_connection_but_is_not_a_chat_answer(monkeypatch,path,expect):
 real_client=httpx.AsyncClient
 private='private reasoning fixture NEVER expose'
 def capture(req):
  assert json.loads(req.content).get('reasoning_split') is True
  return httpx.Response(200,json={'model':'MiniMax-M2.7','choices':[{'finish_reason':'length','message':{'content':'','reasoning_content':private}}]})
 monkeypatch.setattr('app.agent_chat_api.httpx.AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(capture)))
 d=body(None,provider='minimax');d.pop('base_url')
 if path.endswith('messages'):d['messages']=[{'role':'user','content':'hello'}]
 with TestClient(app) as client:r=client.post(path,json=d)
 assert r.status_code==expect,r.text
 assert private not in r.text

@pytest.mark.parametrize('model,stream',[(ChatRequest,False),(StreamRequest,True)])
def test_long_provider_key_is_supported_within_request_byte_budget(model,stream):
 # Provider-issued keys can be JWTs; a small arbitrary 512-char cap is not a protocol limit.
 d=body('https://provider.example/v1',stream,provider='minimax');d['api_key']='demo-key-not-real-'+'x'*1200
 request=model(**d)
 assert request.api_key.get_secret_value()==d['api_key']
 assert d['api_key'] not in repr(request)


def test_custom_hello_response_size_is_bounded_before_json_decode(monkeypatch):
 real_client=httpx.AsyncClient
 monkeypatch.setattr('app.agent_chat_api.httpx.AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=b'x'*1000001))))
 with TestClient(app) as client:r=client.post('/agent-chat/hello',json=body('https://provider.example/v1'))
 assert r.status_code==502
 assert r.json()['detail']=='provider_response_too_large'

def test_final_sse_delivers_first_delta_before_upstream_finishes(monkeypatch):
 import app.agent_chat_provider as provider
 real_client=httpx.AsyncClient
 class SlowChunks(httpx.AsyncByteStream):
  async def __aiter__(self):
   yield b'data: {"choices":[{"delta":{"content":"first"},"finish_reason":null}]}\n\n'
   await asyncio.sleep(2)
   yield b'data: {"choices":[{"delta":{"content":"last"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
 async def handler(request):return httpx.Response(200,stream=SlowChunks())
 monkeypatch.setattr(provider.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler)))
 async def run():
  stream=provider.stream_answer('deepseek','demo-mock',FAKE,[])
  try:assert await asyncio.wait_for(anext(stream),.4)=='first'
  finally:await stream.aclose()
 asyncio.run(run())
