import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { waitingCount } from "../src/inbox-items.mjs";
import { CARDS, cleanLayout } from "../src/workspace-cards.mjs";

// The 例行任务 hub (2026-10-28, Levi: 「待你确认」没有图标，改成例行任务).  No component renderer here, so the wiring
// is checked in the source the build compiles.
const src = (name) => readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");

test("the rail row, the mobile tab and the page are 例行任务 with a drawn icon, not 待你确认", () => {
  const rail = src("BotPages.tsx"), app = src("App.tsx"), page = src("PersonalPages.tsx");
  assert.match(rail, /<RoutineIcon \/><\/span><span className="bot-row-text"><strong>\{t\("例行任务"\)\}/);
  assert.match(rail, /export function RoutineIcon\(\)[\s\S]*?<svg/);
  assert.match(app, /\{ id: "routines", label: t\("例行任务"\), icon: <RoutineIcon \/>/);
  assert.match(page, /<h1 id="routines-title"><RoutineIcon \/> \{t\("例行任务"\)\}<\/h1>/);
  for (const text of [rail, app, page]) assert.doesNotMatch(text, /t\("待你确认"\)/);
  assert.doesNotMatch(rail, />✉</);
});

test("#briefs still opens the page, which lists every routine, the proposals and the run suggestions", () => {
  assert.match(src("App.tsx"), /briefs: "routines"/);
  const page = src("PersonalPages.tsx");
  assert.match(page, /listTaskProposals\(signal\)/);
  assert.match(page, /<TaskManager bots=\{bots\}/);          // every bot's routines: no botId
  assert.match(page, /listInbox\("confirm", signal\)/);
  const manager = src("BotRoutines.tsx");
  assert.match(manager, /botId\?: string/);
  assert.match(manager, /bot_id: botId \?\? draftBot/);       // the hub's form picks the bot
});

test("one 例行任务 workspace card; a saved 待你确认 card becomes it", () => {
  assert.ok(!CARDS.some((c) => c.id === "confirm") && CARDS.some((c) => c.id === "routines" && c.title === "例行任务"));
  assert.deepEqual(cleanLayout([{ card: "confirm" }]), [{ card: "routines" }]);
});

test("the card counts this bot's live proposals and open suggestions", () => {
  const proposals = [{ bot_id: "b1", expired: false }, { bot_id: "b1", expired: true }, { bot_id: "b2", expired: false }];
  const confirms = [{ status: "unread" }, { status: "dismissed" }, { status: "done" }, { status: "read" }];
  assert.equal(waitingCount("b1", proposals, confirms), 3);
  assert.equal(waitingCount("b3", [], []), 0);
});

test("新闻源 is a rail row right under 例行任务, opens its own page, and the 例行任务 page no longer carries it", () => {
  const rail = src("BotPages.tsx"), app = src("App.tsx"), page = src("PersonalPages.tsx");
  assert.match(rail, /onClick=\{onRoutines\}[\s\S]*?onClick=\{onNews\}[\s\S]*?<strong>\{t\("新闻源"\)\}<\/strong>/);
  assert.match(app, /newsActive=\{activeSection === "news"\} onNews=\{\(\) => \{ close\(\); navigate\("news"\); \}\}/);
  assert.match(app, /if \(activeSection === "news"\) return <Shell \{\.\.\.shellProps\}><NewsSourcesPage \/><\/Shell>;/);
  assert.match(app, /"routines", "news", "skills"/);
  const routines = page.slice(page.indexOf("export function RoutinesPage"), page.indexOf("const HEALTH_LABELS"));
  assert.doesNotMatch(routines, /NewsSourcesPanel|getNewsSourcesConfig/);
  const newsPage = page.slice(page.indexOf("export function NewsSourcesPage"), page.indexOf("const KIND_LABELS"));
  assert.match(newsPage, /<NewsSourcesPanel /);
});

test("the routine form has no token caps; the frequency offers intervals and run counts", () => {
  const form = src("BotRoutines.tsx");
  assert.doesNotMatch(form, /token|Cap/);
  assert.match(form, /<option value="every">\{t\("每隔一段时间"\)\}<\/option>/);
  assert.match(form, /max_runs: draftCount === "once" \? 1 : draftCount === "count" \? draftRuns : 0/);
});
