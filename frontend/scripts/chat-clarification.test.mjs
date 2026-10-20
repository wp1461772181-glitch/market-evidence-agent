import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeClarification, selectedClarification, selectedOptions, manualClarification, clarificationTitle, isActiveClarification } from '../src/chat-clarification.mjs';

test('selection card keeps request identity and rejects a forged choice', () => {
  const card = normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'symbol_suggestion',
    choices:[{label:'Apple / AAPL',value:'AAPL'}]});
  assert.deepEqual(selectedClarification(card,'AAPL'),
    {message:'AAPL',pendingRequestId:'11111111-1111-4111-8111-111111111111'});
  assert.throws(() => selectedClarification(card,'AMD'), /invalid_clarification_choice/);
  assert.equal(normalizeClarification({request_id:'forged',choices:[]}),null);
});

test('legacy action card restores real choices and labels its question', () => {
  const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'missing_action_type',choices:[]});
  assert.equal(card.choices.length,7);
  assert.equal(card.choices.find(x=>x.value==='import').label,'导入已保存资料');
  assert.equal(clarificationTitle(card),'请选择要执行的操作');
  assert.equal(clarificationTitle({...card,reason:'missing_action_source'}),'请指定已保存的来源');
});

test('a reference card is titled after what is missing', () => {
  const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'reference_unavailable',choices:[]});
  assert.equal(clarificationTitle(card),'请选择来源或原网页');
  assert.equal(clarificationTitle({...card,reason:'reference_company_required'}),'请告诉我是哪家公司');
  assert.equal(clarificationTitle({...card,reason:'reference_item_unavailable'}),'请指明是哪一条结果');
  assert.notEqual(clarificationTitle({...card,reason:'reference_company_required'}),clarificationTitle(card));
  // a card for a rewrite that changed the scope keeps the generic title: no single thing is missing
  assert.equal(clarificationTitle({...card,reason:'reference_scope_changed'}),'请补充信息');
});

test('a write confirmation card keeps the server-made buttons and titles itself', () => {
  const id='11111111-1111-4111-8111-111111111111';
  const card=normalizeClarification({request_id:id,reason:'confirm_write',choices:[
    {label:'同意',value:`confirm:${id}`},{label:'选择其他写操作',value:`other:${id}`},{label:'取消',value:`cancel:${id}`}]});
  assert.equal(clarificationTitle(card),'请确认要执行的写操作');
  assert.equal(card.choices.length,3);
  assert.deepEqual(selectedClarification(card,`confirm:${id}`),{message:`confirm:${id}`,pendingRequestId:id});
  assert.deepEqual(selectedClarification(card,`cancel:${id}`),{message:`cancel:${id}`,pendingRequestId:id});
  assert.throws(()=>selectedClarification(card,'confirm:22222222-2222-4222-8222-222222222222'),/invalid_clarification_choice/);
  const pick=normalizeClarification({request_id:id,reason:'choose_write_action',choices:[{label:'导入资料库',value:'op:import'}]});
  assert.equal(clarificationTitle(pick),'请选择要执行的写操作');
  assert.deepEqual(selectedClarification(pick,'op:import'),{message:'op:import',pendingRequestId:id});
});

test('card manual reply accepts Chinese and uses the same pending identity', () => {
  const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'missing_stock_focus',choices:[]});
  assert.deepEqual(manualClarification(card,'  就是苹果  '),{message:'就是苹果',pendingRequestId:card.request_id});
  assert.throws(()=>manualClarification(card,'   '),/invalid_clarification_reply/);
  assert.throws(()=>manualClarification(card,'A'.repeat(4001)),/invalid_clarification_reply/);
});

test('only the last waiting live card can submit', () => {
  const card={role:'assistant',status:'waiting_for_input',clarification:{request_id:'11111111-1111-4111-8111-111111111111'}};
  assert.equal(isActiveClarification([card],0,false),true);
  assert.equal(isActiveClarification([card,{role:'user',content:'new question'}],0,false),false);
  assert.equal(isActiveClarification([card],0,true),false);
  assert.equal(isActiveClarification([{...card,status:'cancelled'}],0,false),false);
});

test('legacy research-queue clarification payload is tolerated without a dedicated route', () => {
  const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'company_not_in_queue',
    choices:[{label:'加入研究队列并查询',value:'queue_add:MU'},{label:'不加入，只看这次',value:'queue_view:MU'}]});
  assert.equal(card.choices.length,2);
  assert.equal(clarificationTitle(card),'请补充信息');
  assert.deepEqual(selectedClarification(card,'queue_view:MU'),
    {message:'queue_view:MU',pendingRequestId:'11111111-1111-4111-8111-111111111111'});
  assert.throws(() => selectedClarification(card,'queue_add:AMD'), /invalid_clarification_choice/);
});

test('new confirmation cards retain reviewable dates, URL and frozen targets',()=>{
 const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'write_confirmation',display:{title:'保存网页',saved_date:'2026-10-01',web_date:'2026-10-14',url:'https://example.org/news',alternative:'也可抓取 SEC',targets:[{symbol:'AMD',days:366}]},choices:[{label:'同意',value:'confirm:abcdefghijklmnopqrstuv0123456789-abcd'}]});
 assert.equal(card.display.saved_date,'2026-10-01');assert.equal(card.display.web_date,'2026-10-14');
 assert.equal(card.display.targets[0].days,'366');assert.equal(card.choices.length,1);
});

test('a batch confirmation keeps the dates beside each individual target',()=>{
 const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'write_confirmation',display:{targets:[{title:'First',url:'https://example.org/1',saved_date:'2026-10-01',web_date:'2026-10-14'},{title:'Second',url:'https://example.org/2',saved_date:'2026-10-02',web_date:'2026-10-15'}]},choices:[]});
 assert.equal(card.display.targets[0].saved_date,'2026-10-01');
 assert.equal(card.display.targets[1].web_date,'2026-10-15');
});

test('agent suggestions stay visibly distinct and preserve server authorization buttons',()=>{
 const id='11111111-1111-4111-8111-111111111111';
 const card=normalizeClarification({request_id:id,reason:'write_confirmation',display:{title:'这是 agent 的建议，不是你的要求：fetch_sec',agent_suggestion:true,targets:[{symbol:'INTC'}]},choices:[{label:'授权，拉取最新一份',value:`authorize:${id}`}]});
 assert.equal(card.display.agent_suggestion,true);
 assert.equal(clarificationTitle(card),'Agent 建议：请决定是否批准');
 assert.deepEqual(selectedClarification(card,`authorize:${id}`),{message:`authorize:${id}`,pendingRequestId:id});
 const ordinary=normalizeClarification({...card,display:{agent_suggestion:'true'}});
 assert.notEqual(ordinary.display.agent_suggestion,true);
 assert.notEqual(clarificationTitle(ordinary),'Agent 建议：请决定是否批准');
});

test('a revision card keeps its materials and sends only ticked ones with the capability', () => {
  const token='t'.repeat(43);
  const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'choose_revision_sources',
    choices:[{label:'用所选材料修订',value:'confirm:'+token},{label:'取消',value:'cancel:11111111-1111-4111-8111-111111111111'}],
    options:[{value:'1',label:'Gemini 发布会报道',detail:'上传材料 · 2026-10-15 发布 · 预测后发布的新材料'},{value:'2',label:'10-Q'},
             {value:'x',label:'forged'},{value:'3',label:''}],select:{min:1,max:2}});
  assert.equal(clarificationTitle(card),'请选择用于修订的材料');
  assert.deepEqual(card.options.map(option=>option.value),['1','2']);
  assert.deepEqual(card.select,{min:1,max:2});
  assert.deepEqual(selectedOptions(card,['2','1']),
    {message:'confirm:'+token+':1.2',pendingRequestId:'11111111-1111-4111-8111-111111111111',label:'用所选的 2 份材料修订'});
  assert.throws(()=>selectedOptions(card,[]),/invalid_clarification_choice/);
  assert.throws(()=>selectedOptions(card,['9']),/invalid_clarification_choice/);
  assert.throws(()=>selectedOptions(card,['1','1']),/invalid_clarification_choice/);
  assert.throws(()=>selectedOptions({...card,choices:card.choices.slice(1)},['1']),/invalid_clarification_choice/);
});

test('a card without materials keeps its old shape', () => {
  const card=normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'write_confirmation',
    choices:[{label:'同意',value:'confirm:'+'t'.repeat(43)}]});
  assert.equal('options' in card,false);
  assert.equal('select' in card,false);
});
