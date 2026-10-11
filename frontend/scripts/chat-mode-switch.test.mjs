import test from 'node:test';
import assert from 'node:assert/strict';
import { completePersonalLogin } from '../src/chat-mode-switch.mjs';

test('successful personal login persists identity before notifying root to remount provider', () => {
  const events = [];
  completePersonalLogin('generated-owner-session', (...args) => events.push(['persist', ...args]), mode => events.push(['root', mode]));
  assert.deepEqual(events, [['persist', 'personal', 'generated-owner-session'], ['root', 'personal']]);
});
