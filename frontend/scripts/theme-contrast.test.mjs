import assert from "node:assert/strict";
import test from "node:test";
import { check, checkPairs, loadRules, parseCss, resolveToken, themeTokens } from "./theme-contrast.mjs";

const SRC = new URL("../src/", import.meta.url);
const show = (failures) => failures.map((f) => JSON.stringify(f)).join("\n");

test("no hard-coded light surface carries theme-following text, and no light text sits on a light token", () => {
  const failures = check(loadRules(SRC));
  assert.equal(failures.length, 0, show(failures));
});

test("theme token text/background pairs meet WCAG contrast in light and dark", () => {
  const failures = checkPairs(loadRules(SRC));
  assert.equal(failures.length, 0, show(failures));
});

test("--navy stays a dark brand colour in both themes (white text is written on it)", () => {
  const tokens = themeTokens(loadRules(SRC));
  for (const theme of ["light", "dark"]) {
    const navy = resolveToken(tokens[theme], "--navy");
    assert.ok(navy && navy.r + navy.g + navy.b < 3 * 60, `${theme}: --navy resolves to ${JSON.stringify(navy)}`);
  }
});

test("a new light card with token text fails the guard", () => {
  const rules = loadRules(SRC, ".zz-new-card{background:#fff;color:var(--ink)}");
  assert.ok(check(rules).some((f) => f.check === "A1" && f.selector === ".zz-new-card"));
  const onLight = loadRules(SRC, ".zz-hero{background:var(--me-surface);color:#fff}");
  assert.ok(check(onLight).some((f) => f.check === "A2" && f.selector === ".zz-hero"));
});

test("the parser reads minified rules and media blocks", () => {
  const rules = parseCss("@media (max-width:1px){.a,.b>c{background:#fff;color:red}}.d{color:#000}");
  assert.deepEqual(rules.map((r) => r.selectors), [[".a", ".b>c"], [".d"]]);
  assert.equal(rules[0].decls.background, "#fff");
});
