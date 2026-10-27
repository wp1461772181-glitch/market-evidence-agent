import type { AgentTask, InboxItem, InboxSource, PriceLine } from "./api";
export function inboxKindLabel(kind: string): string;
export function scheduleLabel(task: Pick<AgentTask, "schedule_kind" | "schedule_time">): string;
export function inboxOrder(items: InboxItem[]): InboxItem[];
export function numberedSources(item: InboxItem): InboxSource[];
export function parseSymbols(text: string): string[];
export function parsePriceLines(text: string): PriceLine[] | null;
export function formatPriceLines(lines: PriceLine[] | undefined): string;
