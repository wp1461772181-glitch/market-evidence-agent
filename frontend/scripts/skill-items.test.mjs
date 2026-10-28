import assert from "node:assert/strict";
import test from "node:test";
import { skillFromHash, skillGroups, slashMatches, toggleUsual, usableSkills } from "../src/skill-items.mjs";

const mk = (id, name, extra = {}) => ({ id, name, enabled: true, status: "active", builtin: false, ...extra });

test("skillFromHash reads #skills/<id> only", () => {
  assert.equal(skillFromHash("#skills/abc-123"), "abc-123");
  assert.equal(skillFromHash("#skills"), null);
  assert.equal(skillFromHash("#bot/abc"), null);
  assert.equal(skillFromHash("#skills/a/b"), null);
  assert.equal(skillFromHash(undefined), null);
});

test("skillGroups splits drafts, built-ins and the owner's skills", () => {
  const g = skillGroups([mk("1", "b"), mk("2", "a", { builtin: true }), mk("3", "c", { status: "draft" }), mk("4", "a")]);
  assert.deepEqual(g.drafts.map(s => s.id), ["3"]);
  assert.deepEqual(g.builtin.map(s => s.id), ["2"]);
  assert.deepEqual(g.mine.map(s => s.id), ["4", "1"]);
});

test("slashMatches filters by prefix, puts usual first, caps at 8", () => {
  const skills = [mk("1", "财报速读"), mk("2", "财报对比"), mk("3", "同行对比"), mk("4", "x", { enabled: false }), mk("5", "xd", { status: "draft" })];
  assert.deepEqual(slashMatches(skills, "/财", []).map(s => s.id).sort(), ["1", "2"]);
  assert.equal(slashMatches(skills, "/财", ["2"])[0].id, "2");
  assert.deepEqual(slashMatches(skills, "/x", []), []);
  assert.equal(slashMatches(skills, "/", []).length, 3);
  assert.deepEqual(slashMatches(skills, "/财报速读 问题", []), []);
  assert.deepEqual(slashMatches(skills, "财", []), []);
  const many = Array.from({ length: 12 }, (_, i) => mk(String(i), `s${i}`));
  assert.equal(slashMatches(many, "/s", []).length, 8);
});

test("usableSkills and toggleUsual", () => {
  assert.deepEqual(usableSkills([mk("1", "b"), mk("2", "a", { enabled: false })]).map(s => s.id), ["1"]);
  assert.deepEqual(toggleUsual(["a"], "b"), ["a", "b"]);
  assert.deepEqual(toggleUsual(["a", "b"], "a"), ["b"]);
  assert.deepEqual(toggleUsual(["a"], "b", 1), ["a"]);
});
