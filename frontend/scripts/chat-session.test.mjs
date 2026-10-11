import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentChatLifecycle } from '../src/agentChatLifecycle.js';
import { chatSessionKey, restoreConversationAfterPersistence, restoreConversationSafely } from '../src/chat-session.mjs';

function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}

test('mode identity remounts a distinct provider and personal credential changes force a fresh instance',()=>{
 assert.notEqual(chatSessionKey('guest','guest-token'),chatSessionKey('personal','personal-token'));
 assert.notEqual(chatSessionKey('personal','personal-token'),chatSessionKey('personal','other-token'));
});

test('history selection aborts old restore and commits only the latest selected conversation',async()=>{
 const lifecycle=new AgentChatLifecycle(),oldController=new AbortController(),newController=new AbortController();
 const oldToken=lifecycle.begin(oldController),old=deferred(),committed=[];
 const oldRun=restoreConversationSafely(lifecycle,oldToken,oldController.signal,()=>old.promise,value=>committed.push(value.id));
 const newToken=lifecycle.begin(newController),fresh=deferred();
 const newRun=restoreConversationSafely(lifecycle,newToken,newController.signal,()=>fresh.promise,value=>committed.push(value.id));
 fresh.resolve({id:'history-new'});assert.equal(await newRun,true);
 old.resolve({id:'history-old'});assert.equal(await oldRun,false);
 assert.deepEqual(committed,['history-new']);
});

test('new chat invalidates a pending restore so stale history cannot resurrect messages',async()=>{
 const lifecycle=new AgentChatLifecycle(),controller=new AbortController(),pending=deferred(),committed=[];
 const token=lifecycle.begin(controller);
 const run=restoreConversationSafely(lifecycle,token,controller.signal,()=>pending.promise,value=>committed.push(value));
 lifecycle.invalidate();controller.abort();pending.resolve(['stale messages']);
 assert.equal(await run,false);assert.deepEqual(committed,[]);
});

test('mode switch unmount invalidates a pending guest restore before personal provider mounts',async()=>{
 const guestLifecycle=new AgentChatLifecycle(),controller=new AbortController(),pending=deferred(),guestMessages=[];
 const token=guestLifecycle.begin(controller);
 const run=restoreConversationSafely(guestLifecycle,token,controller.signal,()=>pending.promise,value=>guestMessages.push(value));
 guestLifecycle.invalidate();controller.abort();pending.resolve(['guest history']);
 assert.equal(await run,false);assert.deepEqual(guestMessages,[]);
});

test('new chat during a slow archive write prevents the older history selection from starting restore',async()=>{
 const lifecycle=new AgentChatLifecycle(),controller=new AbortController(),token=lifecycle.begin(controller),write=deferred(),restores=[],commits=[];
 const opening=restoreConversationAfterPersistence(lifecycle,token,controller.signal,()=>write.promise,async()=>{restores.push('started');return{id:'old',messages:[]}},value=>commits.push(value.id));
 lifecycle.invalidate();controller.abort();
 write.resolve();
 assert.equal(await opening,false);
 assert.deepEqual(restores,[]);
 assert.deepEqual(commits,[]);
});
