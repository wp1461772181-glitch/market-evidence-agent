import test from 'node:test';
import assert from 'node:assert/strict';
import { addToWatchlist, queueAddTicker, registerWatchlistAdder, setWatchlistSnapshot, watchlistSnapshot } from '../src/watchlist-bridge.mjs';

test('the snapshot is a copy and is null until the workspace has one', () => {
  setWatchlistSnapshot(null);
  assert.equal(watchlistSnapshot(), null);
  const list = ['AAPL', 'AMD'];
  setWatchlistSnapshot(list);
  list.push('MU');
  assert.deepEqual(watchlistSnapshot(), ['AAPL', 'AMD']);
  watchlistSnapshot().push('X');
  assert.deepEqual(watchlistSnapshot(), ['AAPL', 'AMD']);
});

test('the add goes to the workspace that registered it and shows in the snapshot at once', () => {
  setWatchlistSnapshot(['AAPL']);
  assert.equal(addToWatchlist('MU'), false);            // no workspace: nothing is added
  const added = [];
  const unregister = registerWatchlistAdder(ticker => added.push(ticker));
  assert.equal(addToWatchlist('MU'), true);
  assert.deepEqual(added, ['MU']);
  assert.deepEqual(watchlistSnapshot(), ['AAPL', 'MU']);
  unregister();
  assert.equal(addToWatchlist('AMD'), false);
});

test('only a server-made add button names a ticker', () => {
  assert.equal(queueAddTicker('queue_add:MU'), 'MU');
  assert.equal(queueAddTicker('queue_add:BRK.B'), 'BRK.B');
  for (const value of ['queue_view:MU', 'queue_add:', 'queue_add:mu', 'queue_add:TOOLONGX', 'MU', null, undefined]) {
    assert.equal(queueAddTicker(value), null, String(value));
  }
});
