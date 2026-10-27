import assert from "node:assert/strict";
import test from "node:test";
import { deltaDirection, formatDelta } from "../src/delta.mjs";

test("signed figures carry an arrow and a sign, not only a colour", () => {
  assert.equal(formatDelta(0.0123, { scale: 100, suffix: "%" }), "↑ +1.2%");
  assert.equal(formatDelta(-0.004, { scale: 100, suffix: "pp" }), "↓ −0.4pp");
  assert.equal(formatDelta(0.00001, { scale: 100, suffix: "%" }), "→ 0.0%");
  assert.equal(formatDelta(Number.NaN), "—");
  assert.equal(deltaDirection(-2), "down");
});
