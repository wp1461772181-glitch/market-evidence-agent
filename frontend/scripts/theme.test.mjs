import assert from "node:assert/strict";
import test from "node:test";
import { applyTheme, resolvedTheme, saveTheme, storedTheme } from "../src/theme.mjs";

test("theme choice is stored and resolved to one of two variable sets", () => {
  const data = new Map();
  const storage = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) };
  assert.equal(storedTheme(storage), "light");
  saveTheme("system", storage);
  assert.equal(storedTheme(storage), "system");
  saveTheme("neon", storage);
  assert.equal(storedTheme(storage), "system");
  assert.equal(resolvedTheme("system", true), "dark");
  const root = { dataset: {} };
  assert.equal(applyTheme("dark", root, { matches: false }), "dark");
  assert.equal(root.dataset.theme, "dark");
});
