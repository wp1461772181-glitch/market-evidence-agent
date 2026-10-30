import assert from "node:assert/strict";
import test from "node:test";
import { formatPriceLines, inboxKindLabel, inboxOrder, numberedSources, parsePriceLines, parseSymbols, runsLabel, scheduleLabel } from "../src/inbox-items.mjs";

test("schedules read as the owner set them", () => {
  assert.equal(scheduleLabel({ schedule_kind: "weekdays", schedule_time: "08:00" }), "工作日 08:00");
  assert.equal(scheduleLabel({ schedule_kind: "daily", schedule_time: "21:30" }), "每天 21:30");
  assert.equal(scheduleLabel({ schedule_kind: "hourly", schedule_time: "00:15" }), "每小时第 15 分");
  assert.equal(scheduleLabel({ schedule_kind: "every", schedule_time: "08:00", every_value: 7, every_unit: "days" }), "每 7 天（从 08:00 起）");
  assert.equal(scheduleLabel({ schedule_kind: "every", schedule_time: "06:30", every_value: 6, every_unit: "hours" }), "每 6 小时（从 06:30 起）");
  assert.equal(inboxKindLabel("price"), "价格线");
});

test("a run limit reads as one run or a count, and no limit reads as nothing", () => {
  assert.equal(runsLabel({ max_runs: 1 }), "只执行一次");
  assert.equal(runsLabel({ max_runs: 4 }), "共 4 次");
  assert.equal(runsLabel({ max_runs: null }), "");
});

test("unread first, newest first, dismissed hidden", () => {
  const items = [
    { id: "old-unread", status: "unread", created_at: "2026-10-01" }, { id: "new-read", status: "read", created_at: "2026-10-03" },
    { id: "new-unread", status: "unread", created_at: "2026-10-02" }, { id: "gone", status: "dismissed", created_at: "2026-10-04" },
  ];
  assert.deepEqual(inboxOrder(items).map(i => i.id), ["new-unread", "old-unread", "new-read"]);
});

test("sources are numbered in order and only web links stay links", () => {
  const sources = numberedSources({ sources: [{ ordinal: 2, title: "b", url: "javascript:alert(1)" }, { ordinal: 1, title: "a", url: "https://sec.gov/a" }, { title: "no number" }] });
  assert.deepEqual(sources.map(s => [s.ordinal, s.url]), [[1, "https://sec.gov/a"], [2, null]]);
});

test("symbols and price lines are parsed from what the owner types", () => {
  assert.deepEqual(parseSymbols("nvda, AAPL，msft  nvda"), ["NVDA", "AAPL", "MSFT"]);
  assert.deepEqual(parsePriceLines("NVDA>150, aapl < 180.5"), [{ symbol: "NVDA", above: 150 }, { symbol: "AAPL", below: 180.5 }]);
  assert.deepEqual(parsePriceLines(""), []);
  assert.equal(parsePriceLines("NVDA 150"), null);
  assert.equal(formatPriceLines([{ symbol: "NVDA", above: 150, below: 120 }]), "NVDA>150, NVDA<120");
});
