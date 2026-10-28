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

test("profile items are grouped 全局 first, then each bot (default first), then deleted bots", async () => {
  const { profileLayers, layerLabel } = await import("../src/memory-items.mjs");
  const bots = [{ id: "apple", name: "苹果", is_default: false }, { id: "main", name: "助理", is_default: true }];
  const layers = profileLayers([
    { ...item("g", "explicit", "active", "2026-10-01"), bot_id: null },
    { ...item("a", "inferred", "active", "2026-10-02"), bot_id: "apple", bot_name: "苹果" },
    { ...item("x", "explicit", "active", "2026-10-03"), bot_id: "gone-bot", bot_name: null },
    { ...item("old", "explicit", "archived", "2026-09-01"), bot_id: "apple" },
  ], bots);
  assert.deepEqual(layers.map(l => [l.label, l.items.map(i => i.id)]),
    [["全局", ["g"]], ["助理", []], ["苹果", ["a"]], ["已删除的 bot", ["x"]]]);
  assert.equal(layerLabel({ bot_id: null }), "全局");
  assert.equal(layerLabel({ bot_id: "apple", bot_name: "苹果" }), "苹果");
});

test("past conversations are grouped by bot in the order they come", async () => {
  const { recallGroups } = await import("../src/memory-items.mjs");
  const groups = recallGroups([
    { conversation_id: "1", bot_id: "news", bot_name: "抓新闻" }, { conversation_id: "2", bot_id: "apple", bot_name: "苹果" },
    { conversation_id: "3", bot_id: "news", bot_name: "抓新闻" }, { conversation_id: "4", bot_id: null, bot_name: null },
  ]);
  assert.deepEqual(groups.map(g => [g.label, g.items.map(i => i.conversation_id)]),
    [["抓新闻", ["1", "3"]], ["苹果", ["2"]], ["未归属", ["4"]]]);
});

test("a typed item is checked before it is sent: text, kind and its layer (\"\" is 全局)", async () => {
  const { manualItemDraft, originLabel: label, profileGroups: groups } = await import("../src/memory-items.mjs");
  assert.deepEqual(manualItemDraft({ kind: "preference", text: "  金额都用  亿美元 ", layer: "" }),
    { fields: { kind: "preference", text: "金额都用 亿美元", bot_id: null } });
  assert.deepEqual(manualItemDraft({ kind: "instruction", text: "先讲服务业务", layer: "apple" }).fields.bot_id, "apple");
  assert.equal(manualItemDraft({ kind: "preference", text: "   " }).error, "先写下要记住的内容。");
  assert.equal(manualItemDraft({ kind: "preference", text: "x".repeat(201) }).error, "最多 200 个字。");
  assert.equal(manualItemDraft({ kind: "wish", text: "x" }).error, "请选择类型。");
  assert.equal(label("manual"), "你写的");
  // Typed items rank with the owner's own words, before inferred ones.
  const ordered = groups([item("i", "inferred", "active", "2026-10-01"), item("m", "manual", "active", "2026-10-02")]);
  assert.deepEqual(ordered.active.map(i => i.id), ["m", "i"]);
});
