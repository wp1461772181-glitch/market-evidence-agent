// The research queue is held by the workspace (App.tsx).  The chat request carries it, and the card the
// agent shows for a company that is not in it ("加入研究队列并查询") adds to it through the same list.
let snapshot = null;   // string[] | null: what the workspace holds right now (null: not known yet)
let adder = null;      // (ticker) => void: the workspace's own add

export function setWatchlistSnapshot(list) { snapshot = Array.isArray(list) ? [...list] : null; }
export function watchlistSnapshot() { return snapshot ? [...snapshot] : null; }

export function registerWatchlistAdder(fn) {
  adder = typeof fn === "function" ? fn : null;
  return () => { if (adder === fn) adder = null; };
}

/** Add one ticker to the workspace's queue; false when the workspace is not there to take it. */
export function addToWatchlist(ticker) {
  if (!adder) return false;
  adder(ticker);
  if (snapshot && !snapshot.includes(ticker)) snapshot = [...snapshot, ticker];
  return true;
}

/** The ticker of a server-made "add and search" button ("queue_add:MU"), else null. */
export function queueAddTicker(value) {
  const match = /^queue_add:([A-Z]{1,5}(?:\.[A-Z]{1,2})?)$/.exec(String(value ?? ""));
  return match ? match[1] : null;
}
