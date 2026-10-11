import test from 'node:test';
import assert from 'node:assert/strict';
import {createModelDiscovery, createModelDiscoveryScheduler} from '../src/model-discovery.mjs';
import {fetchAgentChatModels} from '../src/agent-chat-models.mjs';

test('only latest provider/key/url response is returned and cancel invalidates ignored aborts', async()=>{
  const pending=[];
  const discovery=createModelDiscovery(async(payload,{signal})=>new Promise(resolve=>pending.push({payload,signal,resolve})));
  const first=discovery.fetch({provider:'deepseek',base_url:'https://one.test/v1',api_key:'synthetic-key-one'});
  const second=discovery.fetch({provider:'kimi',base_url:'https://two.test/v1',api_key:'synthetic-key-two'});
  pending[0].resolve({models:[{id:'stale'}]});
  pending[1].resolve({models:[{id:'fresh'}]});
  assert.equal(await first,null);
  assert.deepEqual(await second,{models:[{id:'fresh'}]});
  const third=discovery.fetch({provider:'deepseek',base_url:'https://one.test/v1',api_key:'synthetic-key-one'});
  discovery.cancel(); pending[2].resolve({models:[{id:'closed'}]});
  assert.equal(await third,null);
  assert.equal(pending[1].payload.api_key,'synthetic-key-two');
});

test('requires key except explicitly public OpenRouter, limits choices to five, and preserves manual model', async()=>{
  const calls=[];
  const discovery=createModelDiscovery(async(payload)=>{calls.push(payload);return {models:Array.from({length:8},(_,i)=>({id:`model-${i}`}))}});
  assert.equal(await discovery.fetch({provider:'deepseek',base_url:'https://host/v1',api_key:''}),null);
  await assert.rejects(discovery.fetch({provider:'deepseek',base_url:'https://host/v1',api_key:'short'}),/model_discovery_invalid_key/);
  const result=await discovery.fetch({provider:'openrouter',base_url:'https://openrouter.ai/api/v1',api_key:''});
  assert.equal(calls.length,1); assert.equal('api_key' in calls[0],false); assert.equal(result.models.length,5);
});

test('model list transport uses relative proxy and configured absolute API base exactly once',async()=>{
  const seen=[];const fetcher=async(url,init)=>{seen.push({url,init});return new Response('{}')};
  await fetchAgentChatModels({provider:'openrouter',base_url:'https://openrouter.ai/api/v1'},new AbortController().signal,'',fetcher);
  await fetchAgentChatModels({provider:'openrouter',base_url:'https://openrouter.ai/api/v1'},new AbortController().signal,'http://127.0.0.1:8027',fetcher);
  assert.deepEqual(seen.map(x=>x.url),['/api/agent-chat/models','http://127.0.0.1:8027/agent-chat/models']);
  assert.equal(seen[1].init.cache,'no-store');
  assert.equal('api_key' in JSON.parse(seen[0].init.body),false);
  assert.equal(new Headers(seen[0].init.headers).has('Authorization'),false);
});

test('pending key timer is invalidated by URL context change and close',()=>{
  let next=0;const tasks=new Map(),scheduler=createModelDiscoveryScheduler((fn)=>{const id=++next;tasks.set(id,fn);return id},id=>tasks.delete(id));let calls=[];
  scheduler.setContext('provider-a:https://one.test/v1');
  scheduler.schedule(()=>calls.push('old key to edited URL'));
  scheduler.setContext('provider-a:https://two.test/v1');
  assert.equal(tasks.size,0);
  scheduler.setContext('provider-b:https://two.test/v1');
  scheduler.schedule(()=>calls.push('second key'));
  const pending=[...tasks.values()][0];tasks.clear();pending();
  scheduler.schedule(()=>calls.push('closed dialog key'));scheduler.cancel();
  for(const fn of tasks.values())fn();
  assert.deepEqual(calls,['second key']);
});
