export const MAX_WATCHLIST: number;
export function normalizeTicker(value: unknown): string | null;
export function normalizeWatchlist(value: unknown, defaults: string[]): string[];
export function selectionForWatchlist(current: string, remaining: string[]): string;
