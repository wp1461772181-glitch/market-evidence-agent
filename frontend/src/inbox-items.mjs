// Pure helpers of the briefs page (inbox and scheduled tasks), kept apart so node tests read them.

const KIND_LABELS = { task_brief: "任务简报", filing: "新申报", news: "新闻线索", price: "价格线", action: "待你确认" };
const SCHEDULE_LABELS = { daily: "每天", weekdays: "工作日", hourly: "每小时" };

export function inboxKindLabel(kind) { return KIND_LABELS[kind] ?? kind; }

/** "工作日 08:00" / "每天 08:00" / "每小时第 15 分". */
export function scheduleLabel(task) {
  const time = String(task?.schedule_time ?? "");
  if (task?.schedule_kind === "hourly") return `每小时第 ${Number(time.slice(3, 5)) || 0} 分`;
  return `${SCHEDULE_LABELS[task?.schedule_kind] ?? ""} ${time}`.trim();
}

/** Unread first, newest first within each; dismissed items are not shown. */
export function inboxOrder(items) {
  const list = (Array.isArray(items) ? items : []).filter(i => i.status !== "dismissed");
  const unread = i => (i.status === "unread" ? 0 : 1);
  return list.sort((a, b) => unread(a) - unread(b) || String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
}

/** Sources by their number, with only http(s) links kept (a source without a link still shows its title). */
export function numberedSources(item) {
  return (Array.isArray(item?.sources) ? item.sources : [])
    .filter(s => Number.isInteger(s?.ordinal))
    .sort((a, b) => a.ordinal - b.ordinal)
    .map(s => ({ ...s, url: /^https?:\/\//i.test(String(s.url ?? "")) ? s.url : null }));
}

/** "AAPL, msft ,," -> ["AAPL", "MSFT"]. */
export function parseSymbols(text) {
  return [...new Set(String(text ?? "").split(/[\s,，、]+/).map(s => s.trim().toUpperCase()).filter(Boolean))];
}

/** "NVDA>150, AAPL<180" -> [{symbol:"NVDA",above:150},{symbol:"AAPL",below:180}]; null when a part is unreadable. */
export function parsePriceLines(text) {
  const parts = String(text ?? "").split(/[,，;；\n]+/).map(s => s.trim()).filter(Boolean);
  const lines = [];
  for (const part of parts) {
    const match = /^([A-Za-z][A-Za-z0-9.\-]{0,9})\s*([<>])\s*(\d+(?:\.\d+)?)$/.exec(part);
    if (!match) return null;
    lines.push({ symbol: match[1].toUpperCase(), [match[2] === ">" ? "above" : "below"]: Number(match[3]) });
  }
  return lines;
}

export function formatPriceLines(lines) {
  return (Array.isArray(lines) ? lines : []).flatMap(l => [
    ...(l.above != null ? [`${l.symbol}>${l.above}`] : []), ...(l.below != null ? [`${l.symbol}<${l.below}`] : [])]).join(", ");
}

/** How many cards wait for the owner on a bot's 例行任务 card: its unexpired routine proposals and the writes its
 *  unattended runs suggested that are not done or dismissed. */
export function waitingCount(botId, proposals, confirms) {
  return proposals.filter((p) => p.bot_id === botId && !p.expired).length
    + confirms.filter((i) => i.status !== "done" && i.status !== "dismissed").length;
}
