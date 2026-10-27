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

/** The conversation id of a recall link (#agent/<id>), else null. */
export function conversationFromHash(hash) { const match = CONVERSATION_LINK.exec(String(hash ?? "")); return match ? match[1] : null; }
