import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeClarification, selectedClarification, manualClarification, clarificationTitle, isActiveClarification } from '../src/chat-clarification.mjs';

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
