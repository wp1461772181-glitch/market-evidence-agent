import type { ProfileItem } from "./api";
export function originLabel(origin: string): string;
export function recallKindLabel(kind: string): string;
export function profileGroups(items: ProfileItem[]): { active: ProfileItem[]; archived: ProfileItem[] };
export function conversationFromHash(hash: string): string | null;
