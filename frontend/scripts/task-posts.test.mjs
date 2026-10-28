import assert from "node:assert/strict";
import test from "node:test";
import { isTaskPostHead, parseTaskPostHead } from "../src/task-posts.mjs";

const HEAD = "【例行任务结果】「新申报提醒」2026-10-30 08:05（系统自动贴入，不是所有者说的话）";

test("a task post head is detected and parsed into task name and time", () => {
  assert.equal(isTaskPostHead(HEAD), true);
  assert.deepEqual(parseTaskPostHead(HEAD), { raw: HEAD, name: "新申报提醒", time: "2026-10-30 08:05" });
});

test("a normal user message is not a task post head", () => {
  assert.equal(isTaskPostHead("AAPL 最近怎么样？"), false);
  assert.equal(isTaskPostHead("请看【例行任务结果】"), false);
  assert.equal(isTaskPostHead(undefined), false);
});

test("a malformed head falls back to the raw text", () => {
  const raw = "【例行任务结果】坏掉的格式";
  assert.equal(isTaskPostHead(raw), true);
  assert.deepEqual(parseTaskPostHead(raw), { raw, name: null, time: null });
  assert.equal(parseTaskPostHead("【例行任务结果】「只有名字」").time, null);
});
