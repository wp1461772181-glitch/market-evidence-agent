import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chinese = JSON.parse(await readFile(new URL("../src/locales/zh-CN.json", import.meta.url), "utf8"));
const english = JSON.parse(await readFile(new URL("../src/locales/en-US.json", import.meta.url), "utf8"));

test("Chinese phrase catalog is unique and English has an exact translation for every phrase", () => {
  assert.equal(new Set(chinese).size, chinese.length, "Chinese phrase keys must be unique");
  assert.deepEqual(Object.keys(english).sort(), [...chinese].sort());
  for (const phrase of chinese) assert.ok(english[phrase].trim(), `English phrase is empty: ${phrase}`);
});
