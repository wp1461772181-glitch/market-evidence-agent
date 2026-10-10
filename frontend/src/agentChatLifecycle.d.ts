export class AgentChatLifecycle {
  generation: number;
  active: { token: number; controller: AbortController } | null;
  begin(controller: AbortController): number;
  isCurrent(token: number): boolean;
  invalidate(): void;
  finish(token: number): boolean;
}
export function chatContext<T extends { role: string; content: string }>(history: T[], current: T, limits?: { maxMessages?: number; maxChars?: number; maxBytes?: number }): T[];
