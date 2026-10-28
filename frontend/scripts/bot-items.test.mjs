import assert from "node:assert/strict";
import test from "node:test";
import { newBotFields, botDraft, botFromHash, botModelLabel, botSections, focusText, keyStatusLabel, parseFocusSymbols, toggleTool, toolEnabled } from "../src/bot-items.mjs";

const bot = (id, extra = {}) => ({ id, name: id, group: "", pinned: false, hidden: false, is_default: false, last_at: null, model: null, ...extra });

test("a bot link names a bot id; anything else is not one", () => {
  assert.equal(botFromHash("#bot/0A1B2C3D-0000-4000-8000-000000000001"), "0a1b2c3d-0000-4000-8000-000000000001");
  assert.equal(botFromHash("#bot/not-an-id"), null);
  assert.equal(botFromHash("#agent/0a1b2c3d-0000-4000-8000-000000000001"), null);
  assert.equal(botFromHash(undefined), null);
});

test("the default bot sits alone on top; groups follow, ungrouped first; pinned, then recent, inside each", () => {
  const { top, groups } = botSections([
    bot("news", { group: "职能", last_at: "2026-10-27" }), bot("helper", { is_default: true, last_at: "2026-01-01" }),
    bot("old"), bot("fresh", { last_at: "2026-10-28" }), bot("pin", { pinned: true }), bot("gone", { hidden: true }),
    bot("aapl", { group: "公司" }),
  ]);
  assert.equal(top.id, "helper");
  assert.deepEqual(groups.map(g => g.name), ["", "公司", "职能"]);
  assert.deepEqual(groups[0].bots.map(b => b.id), ["pin", "fresh", "old"]);
  assert.ok(!groups.flatMap(g => g.bots).some(b => b.id === "gone"), "hidden bots stay out of the sidebar");
  assert.ok(botSections([bot("gone", { hidden: true })], { showHidden: true }).groups[0].bots.length === 1);
});

test("a template only fills the form: every field is editable and unknown tools are dropped", () => {
  const tools = [{ name: "web_research" }, { name: "search_knowledge" }];
  const draft = botDraft({ id: "news_monitor", name: "抓新闻", title: "新闻监控", description: "盯新闻", tool_scope: ["web_research", "renamed_tool"] }, tools);
  assert.deepEqual(draft, { name: "抓新闻", title: "新闻监控", description: "盯新闻", tool_scope: ["web_research"], template: "news_monitor", focus: { companies: [], theme: null }, model: null });
  assert.equal(botDraft(null, tools).template, "blank");
  assert.equal(botDraft({ id: "blank", tool_scope: null }, tools).tool_scope, null, "a blank bot gets every tool");
});

test("tool scope only narrows: turning one off lists the rest, turning all back on gives every tool (null)", () => {
  const all = ["a", "b", "c"];
  const off = toggleTool(null, "b", all);
  assert.deepEqual(off, ["a", "c"]);
  assert.equal(toggleTool(off, "b", all), null);
  assert.deepEqual(toggleTool(["a", "zzz"], "c", all), ["a", "c"], "names outside the catalog are never kept");
  assert.deepEqual(toggleTool(["a"], "zzz", all), ["a"], "a tool outside the catalog cannot be added");
  assert.ok(toolEnabled(null, "a") && toolEnabled(["a"], "a") && !toolEnabled(["a"], "b"));
});

test("focus companies read from ticker text; non-tickers come back as invalid", () => {
  assert.deepEqual(parseFocusSymbols("aapl, MSFT，brk.b aapl"), { companies: [{ symbol: "AAPL" }, { symbol: "MSFT" }, { symbol: "BRK.B" }], invalid: [], truncated: false });
  assert.deepEqual(parseFocusSymbols("苹果 TOOLONGX").invalid, ["苹果", "TOOLONGX"]);
  assert.deepEqual(parseFocusSymbols("").companies, []);
  const many = Array.from({ length: 25 }, (_, i) => "A" + String.fromCharCode(65 + (i % 26)) + String.fromCharCode(65 + Math.floor(i / 26))).join(" ");
  const parsed = parseFocusSymbols(many);
  assert.equal(parsed.companies.length, 20); assert.equal(parsed.truncated, true);
  assert.equal(focusText({ companies: [{ symbol: "AAPL" }, { symbol: "MSFT" }], theme: null }), "AAPL, MSFT");
});

test("a catalog model shows configured or missing, never its key; a bot without a model uses the default", () => {
  const catalog = { default: "main", models: [{ name: "main", display_name: "主模型", key_status: "configured" }, { name: "cheap", display_name: "", key_status: "missing" }] };
  assert.equal(keyStatusLabel(catalog.models[0]), "已配置");
  assert.equal(keyStatusLabel(catalog.models[1]), "缺 key");
  assert.deepEqual(botModelLabel(bot("x"), catalog), { name: "main", label: "主模型", usable: true, isDefault: true });
  assert.deepEqual(botModelLabel(bot("x", { model: "cheap" }), catalog), { name: "cheap", label: "cheap", usable: false, isDefault: false });
  assert.equal(botModelLabel(bot("x", { model: "gone" }), catalog).usable, false);
});

test("a new bot is a name only; a template just pre-fills what was left blank", () => {
  const tools = [{ name: "web_research" }, { name: "remember" }];
  const news = { id: "news_monitor", name: "抓新闻", title: "新闻监控", description: "盯新闻", tool_scope: ["web_research", "gone_tool"] };
  assert.deepEqual(newBotFields("  小助手 ", null, tools), { name: "小助手", template: "blank" });
  assert.deepEqual(newBotFields("小助手", { id: "blank", name: "", title: "", description: "", tool_scope: null }, tools), { name: "小助手", template: "blank" });
  assert.deepEqual(newBotFields("", news, tools), { name: "抓新闻", template: "news_monitor", title: "新闻监控", description: "盯新闻", tool_scope: ["web_research"] });
  // The owner's own name wins; nothing about focus, model, skills or the daily cap is ever sent.
  const fields = newBotFields("我的新闻", { ...news, tool_scope: null }, tools);
  assert.equal(fields.name, "我的新闻");
  for (const key of ["tool_scope", "focus", "model", "skills", "daily_notify_cap"]) assert.ok(!(key in fields), key);
});
