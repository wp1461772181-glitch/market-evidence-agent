export function setWatchlistSnapshot(list: string[] | null): void;
export function watchlistSnapshot(): string[] | null;
export function registerWatchlistAdder(fn: ((ticker: string) => void) | null): () => void;
export function addToWatchlist(ticker: string): boolean;
export function queueAddTicker(value: unknown): string | null;
