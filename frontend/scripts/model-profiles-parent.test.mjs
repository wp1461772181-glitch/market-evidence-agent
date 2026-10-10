import assert from 'node:assert/strict';
import test from 'node:test';
import { loadProfiles, saveProfiles } from '../src/model-profiles.mjs';
function data(name='A') {return {version:1,selected_id:'id-'+name,profiles:[{id:'id-'+name,display_name:name,provider:'deepseek',base_url:'https://api.deepseek.com',model:'deepseek-chat',api_key:'demo-not-a-real-key',tested_at:'2026-10-10T00:00:00.000Z'}]};}
function opfs(initial=null,delay=false){
 let contents=initial,writes=0;
 const handle={getFile:async()=>({size:Buffer.byteLength(contents??''),text:async()=>contents}),createWritable:async()=>{let pending;return {write:async(v)=>{writes++;pending=v;if(delay&&JSON.parse(v).profiles[0].display_name==='A')await new Promise(r=>setTimeout(r,25));},close:async()=>{contents=pending},abort:async()=>{}}}};
 const dir={getFileHandle:async(name,opts)=>{if(contents===null&&!opts?.create)throw Object.assign(new Error('missing'),{name:'NotFoundError'});return handle;}};
 const env={navigator:{storage:{getDirectory:async()=>dir}}};
 return {env,contents:()=>contents,writes:()=>writes};
}
test('keys, display name, actual model and selected profile survive file roundtrip',async()=>{const f=opfs();await saveProfiles(data(),f.env);const x=await loadProfiles(f.env);assert.equal(x.backend,'OPFS');assert.deepEqual(x.data,data());assert.ok(f.contents().includes('demo-not-a-real-key'));});
test('malformed JSON is an explicit load failure and original file survives',async()=>{const f=opfs('{bad-json');f.env.indexedDB={open(){const r={result:{transaction(){return {objectStore(){return {get(){const q={result:undefined};queueMicrotask(()=>q.onsuccess?.());return q;}}}}}}};queueMicrotask(()=>r.onsuccess?.());return r;}};await assert.rejects(()=>loadProfiles(f.env));assert.equal(f.contents(),'{bad-json');assert.equal(f.writes(),0);});
test('latest queued write wins even if first write is slow',async()=>{const f=opfs(null,true);await Promise.all([saveProfiles(data('A'),f.env),saveProfiles(data('B'),f.env)]);assert.equal((await loadProfiles(f.env)).data.selected_id,'id-B');});
test('corrupt profile key and untested data cannot be silently promoted ready',async()=>{const d=data();d.profiles[0].model='';const f=opfs(JSON.stringify(d));await assert.rejects(()=>loadProfiles(f.env));assert.equal(f.writes(),0);});
test('write error never claims success',async()=>{const f=opfs();f.env.navigator.storage.getDirectory=async()=>({getFileHandle:async()=>({createWritable:async()=>({write:async()=>{throw Error('disk-full')},abort:async()=>{},close:async()=>{}})})});await assert.rejects(()=>saveProfiles(data(),f.env));});
