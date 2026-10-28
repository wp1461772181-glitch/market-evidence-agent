// Pure helpers of the skills page and the "/" picker (personal agent, stage E), kept apart so node tests read them.

const SKILL_LINK = /^#skills\/([A-Za-z0-9_-]{1,64})$/;
export const MAX_SLASH_MATCHES = 8;

/** The skill id of a skill link (#skills/<id>), else null. */
export function skillFromHash(hash) { const match = SKILL_LINK.exec(String(hash ?? "")); return match ? match[1] : null; }

const byName = (a, b) => String(a.name).localeCompare(String(b.name));
/** The skills page's groups: Agent drafts, built-ins, then the owner's own and imported ones (each by name). */
export function skillGroups(skills) {
  const list = Array.isArray(skills) ? skills : [];
  return {
    drafts: list.filter(s => s.status === "draft").sort(byName),
    builtin: list.filter(s => s.status !== "draft" && s.builtin).sort(byName),
    mine: list.filter(s => s.status !== "draft" && !s.builtin).sort(byName),
  };
}

/** Skills the "/" menu offers for a draft like "/ab": enabled, active, name starting with what follows the slash;
 * the bot's usual skills first; at most 8.  Once the draft has a space (or does not start with "/") nothing matches. */
export function slashMatches(skills, text, usualIds) {
  const value = String(text ?? "");
  if (!value.startsWith("/") || /\s/.test(value)) return [];
  const prefix = value.slice(1).toLowerCase();
  const usual = new Set(Array.isArray(usualIds) ? usualIds : []);
  return (Array.isArray(skills) ? skills : [])
    .filter(s => s.enabled && s.status === "active" && String(s.name).toLowerCase().startsWith(prefix))
    .sort((a, b) => Number(usual.has(b.id)) - Number(usual.has(a.id)) || byName(a, b))
    .slice(0, MAX_SLASH_MATCHES);
}

/** Enabled active skills, for pickers (the bot's usual skills, a task's skill). */
export function usableSkills(skills) { return (Array.isArray(skills) ? skills : []).filter(s => s.enabled && s.status === "active").sort(byName); }

/** Toggle one id in the list of usual skills (max 20). */
export function toggleUsual(ids, id, max = 20) {
  const list = Array.isArray(ids) ? ids : [];
  if (list.includes(id)) return list.filter(x => x !== id);
  return list.length >= max ? list : [...list, id];
}
