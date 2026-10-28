// Pure helpers of the memory page (profile items and past conversations), kept apart so node tests read them.

const ORIGIN_LABELS = { explicit: "你说的", inferred: "推断" };
const RECALL_KIND_LABELS = { summary: "摘要", questions: "你的提问" };
const CONVERSATION_LINK = /^#agent\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

export function originLabel(origin) { return ORIGIN_LABELS[origin] ?? origin; }
export function recallKindLabel(kind) { return RECALL_KIND_LABELS[kind] ?? kind; }

/** Active items first (explicit, then inferred; oldest first, the order the model sees), archived apart. */
export function profileGroups(items) {
  const rank = item => (item.origin === "explicit" ? 0 : 1);
  const byAge = (a, b) => rank(a) - rank(b) || String(a.created_at ?? "").localeCompare(String(b.created_at ?? ""));
  const list = Array.isArray(items) ? items : [];
  return { active: list.filter(i => i.status === "active").sort(byAge), archived: list.filter(i => i.status !== "active").sort(byAge) };
}

/** Active profile items by layer: 全局 first, then each bot (the default bot first, in the order given),
 *  then items of bots that no longer exist.  Empty layers are kept for bots so the page can name them. */
export function profileLayers(items, bots) {
  const list = Array.isArray(items) ? items : [];
  const known = Array.isArray(bots) ? [...bots].sort((a, b) => Number(Boolean(b.is_default)) - Number(Boolean(a.is_default))) : [];
  const { active } = profileGroups(list);
  const layers = [{ key: "global", label: "全局", bot: null, items: active.filter(i => !i.bot_id) }];
  for (const bot of known) layers.push({ key: bot.id, label: bot.name, bot, items: active.filter(i => i.bot_id === bot.id) });
  const ids = new Set(known.map(b => b.id));
  const orphans = active.filter(i => i.bot_id && !ids.has(i.bot_id));
  if (orphans.length) layers.push({ key: "gone", label: "已删除的 bot", bot: null, items: orphans });
  return layers;
}

/** Where an item lives, as a label: 全局 or the bot's name. */
export function layerLabel(item) { return item?.bot_id ? (item.bot_name || "已删除的 bot") : "全局"; }

/** Past conversations grouped by bot, in first-seen order (the list is newest first). */
export function recallGroups(items) {
  const groups = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const key = item.bot_id ?? "none";
    if (!groups.has(key)) groups.set(key, { key, label: item.bot_name || (item.bot_id ? "已删除的 bot" : "未归属"), items: [] });
    groups.get(key).items.push(item);
  }
  return [...groups.values()];
}

/** The conversation id of a recall link (#agent/<id>), else null. */
export function conversationFromHash(hash) { const match = CONVERSATION_LINK.exec(String(hash ?? "")); return match ? match[1] : null; }
