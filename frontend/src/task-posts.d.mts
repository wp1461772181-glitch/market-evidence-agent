export const TASK_POST_PREFIX: string;
export function isTaskPostHead(content: string | null | undefined): boolean;
export function parseTaskPostHead(content: string | null | undefined): { raw: string; name: string | null; time: string | null };
