import test from "node:test";
import assert from "node:assert/strict";
import { normalizeTicker, normalizeWatchlist, selectionForWatchlist, MAX_WATCHLIST } from "../src/watchlist.mjs";

test("watchlist normalizes aliases and storage rows, filters invalid entries, and caps size", () => {
  assert.equal(normalizeTicker(" brk-b "), "BRK.B");
  assert.deepEqual(normalizeWatchlist([" amd ", "AMD", "BRK-B", "https://evil.test", 4], ["AAPL"]), ["AMD", "BRK.B"]);
  assert.equal(normalizeWatchlist(null, ["AAPL"])[0], "AAPL");
  assert.equal(normalizeWatchlist(Array.from({ length: 35 }, (_, i) => `A${String.fromCharCode(65 + i % 26)}${i > 25 ? "A" : ""}`), []).length, MAX_WATCHLIST);
});

test("selection follows a removed current ticker and empty list can recover to defaults", () => {
  assert.equal(selectionForWatchlist("BRK.B", ["AMD", "AAPL"]), "AMD");
  assert.equal(selectionForWatchlist("AMD", []), "");
  assert.deepEqual(normalizeWatchlist([], ["AAPL", "MSFT"]), ["AAPL", "MSFT"]);
});
