// The bot workspace (UX batch 2, P3): a per-bot current stock and an editable list of data cards, kept in
// this browser (localStorage).  A second phase may move the layout to the server (agent_bots.workspace_layout).

export const CARDS = [
  { id: "price", title: "行情", size: "m" },
  { id: "forecast", title: "研究预测", size: "m" },
  { id: "materials", title: "最近资料", size: "m" },
  { id: "filings", title: "官方申报", size: "m" },
  { id: "confirm", title: "待你确认", size: "s" },
  { id: "routines", title: "例行任务", size: "s" },
  { id: "brief", title: "简报", size: "m" },
  { id: "watchlist", title: "自选列表", size: "s" },
];
export const DEFAULT_LAYOUT = ["price", "forecast", "materials", "filings", "confirm", "routines"].map((card) => ({ card }));
const KNOWN = new Set(CARDS.map((c) => c.id));
const SIZES = new Set(["s", "m", "l"]);
const TICKER = /^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$/;

export const layoutKey = (botId) => `market-evidence-agent:workspace:${botId}:v1`;
export const symbolKey = (botId) => `market-evidence-agent:bot-symbol:${botId}:v1`;

export function cleanLayout(value) {
  if (!Array.isArray(value)) return null;
  const out = [];
  for (const item of value) {
    if (!item || typeof item !== "object" || !KNOWN.has(item.card)) continue;
    const entry = { card: item.card };
    if (SIZES.has(item.size)) entry.size = item.size;
    if (typeof item.symbol === "string" && TICKER.test(item.symbol)) entry.symbol = item.symbol;
    out.push(entry);
  }
  return out.slice(0, 16);
}

export function loadLayout(botId, storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(layoutKey(botId));
    const layout = raw ? cleanLayout(JSON.parse(raw)) : null;
    return layout ?? DEFAULT_LAYOUT.map((x) => ({ ...x }));
  } catch { return DEFAULT_LAYOUT.map((x) => ({ ...x })); }
}

export function saveLayout(botId, layout, storage = globalThis.localStorage) {
  const clean = cleanLayout(layout) ?? [];
  try { storage?.setItem(layoutKey(botId), JSON.stringify(clean)); } catch { /* the layout still applies this visit */ }
  return clean;
}

export function resetLayout(botId, storage = globalThis.localStorage) {
  try { storage?.removeItem(layoutKey(botId)); } catch { /* nothing stored */ }
  return DEFAULT_LAYOUT.map((x) => ({ ...x }));
}

export function addCard(layout, card) { return KNOWN.has(card) ? [...layout, { card }] : layout; }
export function removeCard(layout, index) { return layout.filter((_, i) => i !== index); }
export function moveCard(layout, index, delta) {
  const to = index + delta;
  if (index < 0 || index >= layout.length || to < 0 || to >= layout.length) return layout;
  const next = [...layout];
  [next[index], next[to]] = [next[to], next[index]];
  return next;
}
export function resizeCard(layout, index, size) {
  return SIZES.has(size) ? layout.map((item, i) => (i === index ? { ...item, size } : item)) : layout;
}
export function pinCardSymbol(layout, index, symbol) {
  const clean = String(symbol || "").trim().toUpperCase();
  return layout.map((item, i) => {
    if (i !== index) return item;
    const { symbol: _old, ...rest } = item;
    return TICKER.test(clean) ? { ...rest, symbol: clean } : rest;
  });
}

/** A bot's current stock: what was last picked in this bot › its default company › its first focus company › the page's. */
export function botSymbol(bot, globalSymbol, storage = globalThis.localStorage) {
  try {
    const stored = storage?.getItem(symbolKey(bot.id));
    if (stored && TICKER.test(stored)) return stored;
  } catch { /* fall through */ }
  const companies = bot?.focus?.companies ?? [];
  const fromBot = bot?.default_company?.symbol ?? companies[0]?.symbol;
  return fromBot && TICKER.test(fromBot) ? fromBot : globalSymbol;
}

export function storeBotSymbol(botId, symbol, storage = globalThis.localStorage) {
  const clean = String(symbol || "").trim().toUpperCase();
  if (!TICKER.test(clean)) return null;
  try { storage?.setItem(symbolKey(botId), clean); } catch { /* still applies this visit */ }
  return clean;
}

/** The workspace's stock menu: the bot's focus first, then the watchlist, no repeats. */
export function symbolChoices(bot, watchlist = [], current = "") {
  const out = [];
  for (const s of [current, ...(bot?.focus?.companies ?? []).map((c) => c.symbol), ...watchlist]) {
    if (s && TICKER.test(s) && !out.includes(s)) out.push(s);
  }
  return out;
}

/** Last close, previous close and the change, as facts (no direction advice). */
export function priceSummary(candles) {
  const rows = Array.isArray(candles) ? candles.filter((c) => Number.isFinite(c?.close)) : [];
  if (!rows.length) return null;
  const last = rows[rows.length - 1], prev = rows.length > 1 ? rows[rows.length - 2] : null;
  const change = prev ? last.close - prev.close : null;
  const pct = prev && prev.close ? (change / prev.close) * 100 : null;
  return { date: last.trading_date, close: last.close, change, pct };
}
