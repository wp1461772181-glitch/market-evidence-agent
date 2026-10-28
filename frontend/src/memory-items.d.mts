import type { ProfileBot, ProfileItem, RecallItem } from "./api";
export function originLabel(origin: string): string;
export function recallKindLabel(kind: string): string;
export function profileGroups(items: ProfileItem[]): { active: ProfileItem[]; archived: ProfileItem[] };
export function conversationFromHash(hash: string): string | null;
export type ProfileLayer = { key: string; label: string; bot: ProfileBot | null; items: ProfileItem[] };
export function profileLayers(items: ProfileItem[], bots: ProfileBot[]): ProfileLayer[];
export function layerLabel(item: ProfileItem): string;
export function recallGroups(items: RecallItem[]): { key: string; label: string; items: RecallItem[] }[];
