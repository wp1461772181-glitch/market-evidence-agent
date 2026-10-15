export type ContextPartKey = "system" | "tools" | "summary" | "compressed" | "recent" | "free";
export type ContextPart = { key: ContextPartKey; bytes: number; tokens: number; segments?: number; turns?: number };
export type ContextUsage = {
  budgetKb: number; capacityBytes: number; usedBytes: number; remainingBytes: number; remainingPercent: number;
  compressing: boolean; hiddenTurns: number; parts: ContextPart[];
};
export const PART_KEYS: ContextPartKey[];
export function normalizeContextUsage(value: unknown): ContextUsage | null;
export function formatKb(bytes: number): string;
export function formatTokens(tokens: number): string;
export function barSegments(usage: ContextUsage | null): Array<{ key: ContextPartKey; percent: number }>;
export function meterTone(usage: ContextUsage | null): "ok" | "warn" | "full";
