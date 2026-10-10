export const MAX_WATCHLIST = 30;
export function normalizeTicker(value) {
  if (typeof value !== "string") return null;
  const ticker = value.trim().toUpperCase().replace(/-/g, ".");
  return /^[A-Z]{1,5}(?:\.[A-Z]{1,2})?$/.test(ticker) ? ticker : null;
}
export function normalizeWatchlist(value, defaults) {
  if (!Array.isArray(value)) return defaults;
  const clean = [...new Set(value.map(normalizeTicker).filter(Boolean))].slice(0, MAX_WATCHLIST);
  return clean.length ? clean : defaults;
}
export function selectionForWatchlist(current, remaining) {
  return remaining.includes(current) ? current : remaining[0] ?? "";
}
