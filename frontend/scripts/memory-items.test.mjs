import assert from "node:assert/strict";
import test from "node:test";
import { conversationFromHash, originLabel, profileGroups, recallKindLabel } from "../src/memory-items.mjs";

const item = (id, origin, status, created_at) => ({ id, origin, status, created_at, kind: "preference", text: id, quote: id });

test("active items come first, explicit before inferred, oldest first within each; archived apart", () => {
  const groups = profileGroups([
    item("b", "inferred", "active", "2026-10-02"), item("old", "explicit", "archived", "2026-09-01"),
    item("c", "explicit", "active", "2026-10-03"), item("a", "explicit", "active", "2026-10-01"),
  ]);
  assert.deepEqual(groups.active.map(i => i.id), ["a", "c", "b"]);
  assert.deepEqual(groups.archived.map(i => i.id), ["old"]);
});

test("badges name where an item came from and what a recall line is", () => {
  assert.equal(originLabel("explicit"), "你说的");
  assert.equal(originLabel("inferred"), "推断");
  assert.equal(recallKindLabel("summary"), "摘要");
  assert.equal(recallKindLabel("questions"), "你的提问");
});

test("a recall link names one conversation; anything else is not one", () => {
  const id = "0f8b3c1e-2d4a-4b6c-8e9f-0123456789ab";
  assert.equal(conversationFromHash(`#agent/${id}`), id);
  assert.equal(conversationFromHash("#agent"), null);
  assert.equal(conversationFromHash("#agent/not-an-id"), null);
  assert.equal(conversationFromHash(`#memory/${id}`), null);
});
