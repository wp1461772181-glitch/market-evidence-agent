// A routine task's result is posted into a bot's line as a turn whose user row is a system head, not the owner's words.

export const TASK_POST_PREFIX = "【例行任务结果】";
const HEAD = /^【例行任务结果】「(.+?)」\s*(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)?/;

/** True when a user row is the system head of a posted task result. */
export function isTaskPostHead(content) { return String(content ?? "").startsWith(TASK_POST_PREFIX); }

/** The head's task name and time; both null when it cannot be parsed, with the raw text kept. */
export function parseTaskPostHead(content) {
  const raw = String(content ?? "");
  const match = HEAD.exec(raw);
  return { raw, name: match ? match[1] : null, time: match?.[2] ?? null };
}
