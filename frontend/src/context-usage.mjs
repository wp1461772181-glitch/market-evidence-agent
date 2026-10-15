// The numbers of the context card, as the server reports them (see agent_context.usage_report).
// "Remaining" is the room left in this product's history budget; it is not a model window.
export const PART_KEYS = ["system", "tools", "summary", "compressed", "recent", "free"];

function count(value) {
  const number = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return number > 0 ? Math.round(number) : 0;
}

export function normalizeContextUsage(value) {
  if (!value || typeof value !== "object") return null;
  const found = new Map();
  for (const part of Array.isArray(value.parts) ? value.parts : []) {
    if (!part || typeof part !== "object" || !PART_KEYS.includes(part.key) || found.has(part.key)) continue;
    found.set(part.key, {
      key: part.key, bytes: count(part.bytes), tokens: count(part.tokens),
      ...(typeof part.segments === "number" ? { segments: count(part.segments) } : {}),
      ...(typeof part.turns === "number" ? { turns: count(part.turns) } : {}),
    });
  }
  const capacityBytes = count(value.capacity_bytes);
  if (!capacityBytes || !found.size) return null;
  const percent = typeof value.remaining_percent === "number" && Number.isFinite(value.remaining_percent)
    ? Math.min(100, Math.max(0, Math.round(value.remaining_percent))) : 0;
  return {
    budgetKb: count(value.budget_kb), capacityBytes, usedBytes: Math.min(count(value.used_bytes), capacityBytes),
    remainingBytes: count(value.remaining_bytes), remainingPercent: percent,
    compressing: value.compressing === true, hiddenTurns: count(value.hidden_turns),
    parts: PART_KEYS.filter(key => found.has(key)).map(key => found.get(key)),
  };
}

export function formatKb(bytes) {
  const kb = count(bytes) / 1024;
  return kb >= 100 ? `${Math.round(kb)} KB` : `${kb.toFixed(1)} KB`;
}

export function formatTokens(tokens) {
  const n = count(tokens);
  return n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`;
}

/** Share of the whole bar each part takes, in percent (parts with no bytes are left out). */
export function barSegments(usage) {
  if (!usage) return [];
  return usage.parts.filter(part => part.bytes > 0)
    .map(part => ({ key: part.key, percent: Math.round((part.bytes / usage.capacityBytes) * 1000) / 10 }));
}

/** How the pill looks: plenty left, getting full, or compressing hard. */
export function meterTone(usage) {
  if (!usage) return "ok";
  if (usage.remainingPercent <= 10) return "full";
  if (usage.remainingPercent <= 30) return "warn";
  return "ok";
}
