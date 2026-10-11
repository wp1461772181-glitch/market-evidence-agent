import test from 'node:test';
import assert from 'node:assert/strict';
import { activeChatCredential, applyChatIdentity, changeChatMode, chatIdentityKeys, currentChatMode, conversationCreatedAtForMode, conversationForMode, selectConversationForMode } from '../src/chat-identity.mjs';
function storage(seed={}) { const data=new Map(Object.entries(seed)); return {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,String(v)),removeItem:k=>data.delete(k),data}; }
test('guest credential migrates from session storage once and persists across reloads',()=>{
 const local=storage(),session=storage({[chatIdentityKeys.legacySession]:'legacy-guest'});
 assert.equal(activeChatCredential(local,session,()=>{throw Error('must migrate')}),'legacy-guest');
 assert.equal(session.getItem(chatIdentityKeys.legacySession),null);
 assert.equal(activeChatCredential(local,storage(),()=> 'new'),'legacy-guest');
});
test('personal identity is stable and switching clears selected conversation without changing guest identity',()=>{
 const local=storage({[chatIdentityKeys.guest]:'guest-token'}),session=storage({[chatIdentityKeys.conversation]:'guest-conversation','market-evidence-agent-api-access-key':'old-owner-key'});
 changeChatMode(local,session,'personal','personal-token');
 assert.equal(conversationForMode(local,'guest'),'guest-conversation');
 assert.equal(session.getItem(chatIdentityKeys.conversation),null);
 assert.equal(session.getItem('market-evidence-agent-api-access-key'),null);
 assert.equal(currentChatMode(local),'personal');
 assert.equal(activeChatCredential(local,session,()=> 'new'),'personal-token');
 changeChatMode(local,session,'guest');
 assert.equal(session.getItem('market-evidence-agent-api-access-key'),null);
 assert.equal(activeChatCredential(local,session,()=> 'new'),'guest-token');
 assert.equal(conversationForMode(local,'personal'),null);
});

test('first personal switch preserves a legacy guest credential and clears the session owner key',()=>{
 const local=storage(),session=storage({[chatIdentityKeys.legacySession]:'legacy-guest','market-evidence-agent-api-access-key':'owner-key'});
 changeChatMode(local,session,'personal','personal-token');
 assert.equal(local.getItem(chatIdentityKeys.guest),'legacy-guest');
 assert.equal(session.getItem(chatIdentityKeys.legacySession),null);
 assert.equal(session.getItem('market-evidence-agent-api-access-key'),null);
 changeChatMode(local,session,'guest');
 assert.equal(activeChatCredential(local,session,()=> 'unexpected-random'),'legacy-guest');
});

test('selected conversation IDs remain isolated by mode across remounts',()=>{
 const local=storage({[chatIdentityKeys.guest]:'g'}),session=storage();
 selectConversationForMode(local,session,'guest-id','guest','2026-10-01T00:00:00Z');
 changeChatMode(local,session,'personal','p');
 selectConversationForMode(local,session,'personal-id','personal','2026-10-02T00:00:00Z');
 changeChatMode(local,session,'guest');
 assert.equal(conversationForMode(local,'guest'),'guest-id');
 assert.equal(conversationForMode(local,'personal'),'personal-id');
 assert.equal(conversationCreatedAtForMode(local,'guest'),'2026-10-01T00:00:00Z');
 assert.equal(conversationCreatedAtForMode(local,'personal'),'2026-10-02T00:00:00Z');
});


test('business request identity follows selected mode and keeps explicit identity',()=>{
 const local=storage({[chatIdentityKeys.guest]:'guest-session'}),session=storage({'market-evidence-agent-api-access-key':'obsolete'});
 const guest=applyChatIdentity({},activeChatCredential(local,session,()=> 'random-guest'));
 assert.equal(guest.get('X-Chat-Session'),'guest-session');assert.equal(guest.has('X-App-Access-Key'),false);
 changeChatMode(local,session,'personal','owner-login-session');
 const owner=applyChatIdentity({},activeChatCredential(local,session,()=> 'unused'));
 assert.equal(owner.get('X-Chat-Session'),'owner-login-session');assert.equal(session.getItem('market-evidence-agent-api-access-key'),null);
 assert.equal(applyChatIdentity({'X-Chat-Session':'explicit'},'owner-login-session').get('X-Chat-Session'),'explicit');
 changeChatMode(local,session,'guest');
 assert.equal(applyChatIdentity({},activeChatCredential(local,session,()=> 'unused')).get('X-Chat-Session'),'guest-session');
});
