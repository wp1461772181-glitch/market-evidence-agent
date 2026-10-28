import type { Skill } from "./api";
export const MAX_SLASH_MATCHES: number;
export function skillFromHash(hash: string): string | null;
export function skillGroups(skills: Skill[]): { drafts: Skill[]; builtin: Skill[]; mine: Skill[] };
export function slashMatches(skills: Skill[], text: string, usualIds: string[] | null | undefined): Skill[];
export function usableSkills(skills: Skill[]): Skill[];
export function toggleUsual(ids: string[] | null | undefined, id: string, max?: number): string[];
