// Pure helpers of the bot sidebar and the bot profile (personal agent, second edition), kept apart so node tests read them.

const BOT_LINK = /^#bot\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;
const SYMBOL = /^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$/;
export const MAX_FOCUS_COMPANIES = 20;

/** The bot id of a bot link (#bot/<id>), else null. */
export function botFromHash(hash) { const match = BOT_LINK.exec(String(hash ?? "")); return match ? match[1].toLowerCase() : null; }

/** The sidebar: the default bot alone on top, then the owner's groups (ungrouped first, then by name); pinned
 * first inside each, then the most recently active, then by name.  Hidden bots are left out unless asked for. */
export function botSections(bots, { showHidden = false } = {}) {
  const list = (Array.isArray(bots) ? bots : []).filter(b => showHidden || !b.hidden || b.is_default);
  const top = list.find(b => b.is_default) ?? null;
  const rest = list.filter(b => b !== top).sort((a, b) =>
    Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || String(b.last_at ?? "").localeCompare(String(a.last_at ?? "")) || a.name.localeCompare(b.name));
  const groups = new Map();
  for (const bot of rest) { const key = (bot.group ?? "").trim(); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(bot); }
  const names = [...groups.keys()].sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)));
  return { top, groups: names.map(name => ({ name, bots: groups.get(name) })) };
}

/** The new-bot form a template fills; every field stays editable.  A template's tools are kept only when the
 * server still offers them, so a renamed tool never makes the form unsavable. */
export function botDraft(template, tools) {
  const known = new Set((Array.isArray(tools) ? tools : []).map(t => t.name));
  const scope = template?.tool_scope;
  return { name: template?.name ?? "", title: template?.title ?? "", description: template?.description ?? "",
    tool_scope: Array.isArray(scope) ? scope.filter(n => known.has(n)) : null, template: template?.id ?? "blank",
    focus: { companies: [], theme: null }, model: null };
}

/** What a new bot is created with: the name, and nothing else unless a template was picked.  A template only
 * pre-fills what the owner left blank (its name when the name is empty, its title, description and tool scope);
 * focus, model, skills and the daily cap stay at the server's defaults until the 档案 tab changes them. */
export function newBotFields(name, template, tools) {
  const typed = String(name ?? "").trim();
  if (!template || template.id === "blank") return { name: typed, template: "blank" };
  const draft = botDraft(template, tools);
  return { name: typed || draft.name.trim(), template: draft.template,
    ...(draft.title ? { title: draft.title } : {}), ...(draft.description ? { description: draft.description } : {}),
    ...(draft.tool_scope !== null ? { tool_scope: draft.tool_scope } : {}) };
}

/** Turn one tool on or off.  null means every tool; turning all of them on again gives null back, so the bot keeps
 * up with tools added later.  A scope only ever narrows the catalog: names outside it are dropped. */
export function toggleTool(scope, name, allNames) {
  const all = Array.isArray(allNames) ? allNames : [];
  const current = scope === null || scope === undefined ? new Set(all) : new Set(scope.filter(n => all.includes(n)));
  if (current.has(name)) current.delete(name); else if (all.includes(name)) current.add(name);
  return current.size === all.length ? null : all.filter(n => current.has(n));
}
export function toolEnabled(scope, name) { return scope === null || scope === undefined || scope.includes(name); }

/** Ticker text ("AAPL, msft brk.b") → focus companies; what does not read as a ticker comes back as invalid. */
export function parseFocusSymbols(text) {
  const seen = new Set(), companies = [], invalid = [];
  for (const raw of String(text ?? "").split(/[\s,，、;；]+/)) {
    const symbol = raw.trim().toUpperCase();
    if (!symbol) continue;
    if (!SYMBOL.test(symbol)) { invalid.push(raw.trim()); continue; }
    if (!seen.has(symbol)) { seen.add(symbol); companies.push({ symbol }); }
  }
  return { companies: companies.slice(0, MAX_FOCUS_COMPANIES), invalid, truncated: companies.length > MAX_FOCUS_COMPANIES };
}
export function focusText(focus) { return (focus?.companies ?? []).map(c => c.symbol).join(", "); }

/** What the page may say about a catalog model's key: configured or missing, never any part of the key. */
export function keyStatusLabel(entry) { return entry?.key_status === "configured" ? "已配置" : "缺 key"; }
/** The bot's model line: its catalog entry, or the catalog default when it has none. */
export function botModelLabel(bot, catalog) {
  const models = catalog?.models ?? [];
  const name = bot?.model ?? catalog?.default ?? null;
  const entry = models.find(m => m.name === name);
  if (!entry) return { name, label: name ?? "", usable: false, isDefault: !bot?.model };
  return { name, label: entry.display_name || entry.name, usable: entry.key_status === "configured", isDefault: !bot?.model };
}
