import test from "node:test";
import assert from "node:assert/strict";
import { adjacentTurnIndex, markerProgress, railShouldShow, readBookmarkedTurns, writeBookmarkedTurns } from "../src/question-rail.mjs";

function memoryStorage() {
  const data = new Map();
  return { getItem: key => (data.has(key) ? data.get(key) : null), setItem: (key, value) => data.set(key, String(value)) };
}

test("the question rail appears once a conversation has four questions", () => {
  assert.equal(railShouldShow(3), false);
  assert.equal(railShouldShow(4), true);
});

test("marker length falls off with distance from the focused question", () => {
  assert.equal(markerProgress(5, 5), 1);
  assert.equal(markerProgress(6, 5), 0.7);
  assert.equal(markerProgress(3, 5), 0.4);
  assert.equal(markerProgress(8, 5), 0.2);
  assert.equal(markerProgress(9, 5), 0);
  assert.equal(markerProgress(0, null), 0);
});

test("a next step lands on the first question below the transcript top", () => {
  assert.equal(adjacentTurnIndex([-400, -100, 40, 300], 0, "next"), 2);
  assert.equal(adjacentTurnIndex([-400, -100], 0, "next"), null);
});

test("a previous step lands on the question above the top, or the one before an aligned question", () => {
  assert.equal(adjacentTurnIndex([-400, -100, 40, 300], 0, "previous"), 1);
  assert.equal(adjacentTurnIndex([-400, 5, 300], 0, "previous"), 0);
  assert.equal(adjacentTurnIndex([5, 300], 0, "previous"), null);
  assert.equal(adjacentTurnIndex([100, 200], 0, "previous"), 0);
});

test("bookmarks are kept per conversation and survive a reload", () => {
  const storage = memoryStorage();
  writeBookmarkedTurns(storage, "conv-a", new Set(["turn-1"]));
  writeBookmarkedTurns(storage, "conv-b", new Set(["turn-9"]));
  assert.deepEqual([...readBookmarkedTurns(storage, "conv-a")], ["turn-1"]);
  assert.deepEqual([...readBookmarkedTurns(storage, "conv-b")], ["turn-9"]);
  assert.deepEqual([...readBookmarkedTurns(storage, null)], []);
});

test("unreadable bookmark storage degrades to no bookmarks", () => {
  const storage = { getItem: () => "{not json", setItem() { throw new Error("quota exceeded"); } };
  assert.deepEqual([...readBookmarkedTurns(storage, "conv-a")], []);
  assert.doesNotThrow(() => writeBookmarkedTurns(storage, "conv-a", new Set(["turn-1"])));
});
