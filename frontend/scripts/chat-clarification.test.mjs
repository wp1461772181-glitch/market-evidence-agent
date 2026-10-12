import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeClarification, selectedClarification } from '../src/chat-clarification.mjs';

test('selection card keeps request identity and rejects a forged choice', () => {
  const card = normalizeClarification({request_id:'11111111-1111-4111-8111-111111111111',reason:'symbol_suggestion',
    choices:[{label:'Apple / AAPL',value:'AAPL'}]});
  assert.deepEqual(selectedClarification(card,'AAPL'),
    {message:'AAPL',pendingRequestId:'11111111-1111-4111-8111-111111111111'});
  assert.throws(() => selectedClarification(card,'AMD'), /invalid_clarification_choice/);
  assert.equal(normalizeClarification({request_id:'forged',choices:[]}),null);
});
