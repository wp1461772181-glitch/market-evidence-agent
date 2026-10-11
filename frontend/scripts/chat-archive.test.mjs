import test from "node:test";
import assert from "node:assert/strict";
import { createArchive, createSerializedWriter, isPickerCancelled, mergeConversation, serializeArchive, validateArchive, writeAtomically } from "../src/chat-archive.mjs";

const t0="2026-10-11T00:00:00.000Z";
const conversation={id:"local-chat-1",created_at:t0,updated_at:t0,messages:[{role:"user",content:"查找中文资料 🌱"},{role:"assistant",content:"已找到",tool_trace:[{call_id:"call-1",name:"search_filings",arguments:{query:"中文"},result:{ok:true,count:1},status:"complete"}]}]};

test("UTF-8 messages and tool traces round trip with credential fields excluded",()=>{
 const text=serializeArchive(createArchive([conversation],t0));const loaded=validateArchive(text);
 assert.equal(loaded.conversations[0].messages[0].content,"查找中文资料 🌱");assert.deepEqual(loaded.conversations[0].messages[1].tool_trace[0].result,{ok:true,count:1});
 assert.doesNotMatch(text,/api_key|credential|conversation_id|chat-session/i);
});
test("invalid and oversized imports throw before caller state can be replaced",()=>{
 let current=conversation;
 for(const input of ['{"marker":"wrong"}',JSON.stringify({...createArchive([conversation]),version:99}),"x".repeat(5_000_001)]) assert.throws(()=>{const next=validateArchive(input);current=next.conversations[0];});
 assert.equal(current,conversation);
 assert.throws(()=>validateArchive(createArchive([{...conversation,messages:[{role:"tool",content:"unsafe"}]}])));
 assert.throws(()=>validateArchive(createArchive([{...conversation,messages:[{role:"assistant",content:"",tool_trace:[{name:"shell",arguments:{command:"rm"},status:"complete"}]}]}])));
});
test("merging a new conversation retains older history and same id updates in place",()=>{
 const other={...conversation,id:"local-chat-2",messages:[{role:"user",content:"another"}]};const merged=mergeConversation(createArchive([conversation],t0),other);
 assert.deepEqual(merged.conversations.map(x=>x.id),["local-chat-1","local-chat-2"]);const updated=mergeConversation(merged,{...other,messages:[{role:"user",content:"updated"}]});assert.equal(updated.conversations.length,2);assert.equal(updated.conversations[1].messages[0].content,"updated");
});
test("queued writes retain their original target after reconnect",async()=>{
 const seen=[];let release;const A={name:"A"},B={name:"B"};const writer=createSerializedWriter(async(target,value)=>{if(value==="Adata")await new Promise(r=>release=r);seen.push([target.name,value]);});
 const old=writer(A,"Adata"),queued=writer(A,"Aqueued");await new Promise(r=>setTimeout(r,0));const next=writer(B,"Bdata");release();await Promise.all([old,queued,next]);assert.deepEqual(seen,[["A","Adata"],["A","Aqueued"],["B","Bdata"]]);
});
test("serialized writes preserve order and recover after a failed write",async()=>{
 const seen=[];const writer=createSerializedWriter(async(_target,value)=>{seen.push(`start:${value}`);await new Promise(r=>setTimeout(r,5));seen.push(`end:${value}`);if(value==="bad")throw Error("disk");});
 const a=writer(null,"one"),b=writer(null,"bad"),c=writer(null,"three");await Promise.allSettled([a,b,c]);assert.deepEqual(seen,["start:one","end:one","start:bad","end:bad","start:three","end:three"]);
});
test("picker cancellation is distinguishable from permission and disk failures",()=>{assert.equal(isPickerCancelled(Object.assign(new Error("dismissed"),{name:"AbortError"})),true);assert.equal(isPickerCancelled(new Error("disk denied")),false);});
test("conversation updates preserve the original created_at timestamp",()=>{const changed=mergeConversation(createArchive([conversation],t0),{...conversation,created_at:"2026-10-12T00:00:00.000Z",messages:[{role:"user",content:"later"}]});assert.equal(changed.conversations[0].created_at,t0);assert.equal(changed.conversations[0].messages[0].content,"later");});
test("new-chat snapshot can be persisted immediately before clearing the debounce",async()=>{const target={name:"file"},seen=[];const writer=createSerializedWriter(async(t,value)=>seen.push([t.name,validateArchive(value).conversations.map(c=>c.messages[0].content)]));const previous=mergeConversation(createArchive(),{...conversation,created_at:t0,messages:[{role:"user",content:"completed old turn"}]});await writer(target,serializeArchive(previous));const fresh=mergeConversation(previous,{id:"local-chat-2",created_at:t0,updated_at:t0,messages:[{role:"user",content:"new chat"}]});await writer(target,serializeArchive(fresh));assert.deepEqual(seen,[ ["file",["completed old turn"]], ["file",["completed old turn","new chat"]] ]);});
test("failed writes abort streams and file size is checked before reading",async()=>{let aborted=false,read=false;await assert.rejects(writeAtomically({createWritable:async()=>({write:async()=>{throw Error("disk")},close:async()=>{},abort:async()=>{aborted=true;}})},"x"));assert.equal(aborted,true);const file={size:5_000_001,text:async()=>{read=true;return ""}};assert.throws(()=>{if(file.size>5_000_000)throw Error("too large");return validateArchive(file.text())},/too large/);assert.equal(read,false);});
