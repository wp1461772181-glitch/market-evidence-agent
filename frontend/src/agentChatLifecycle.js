/** Request identity shared by hello and send; late callbacks must pass isCurrent. */
export class AgentChatLifecycle {
  generation = 0;
  active = null;

  begin(controller) {
    // A new request supersedes the previous one: abort it so the server stops billing for a turn
    // whose answer can no longer reach the page.
    this.active?.controller.abort();
    const token = ++this.generation;
    this.active = { token, controller };
    return token;
  }

  isCurrent(token) {
    return token === this.generation && this.active?.token === token;
  }

  invalidate() {
    ++this.generation;
    this.active?.controller.abort();
    this.active = null;
  }

  finish(token) {
    if (!this.isCurrent(token)) return false;
    this.active = null;
    return true;
  }
}

/** Keep recent complete user/assistant pairs plus the current user, within API limits. */
export function chatContext(history, current, { maxMessages = 20, maxChars = 20000, maxBytes = 30000 } = {}) {
  const pairs = [];
  for (let i = 0; i + 1 < history.length; i++) {
    if (history[i].role === "user" && history[i + 1].role === "assistant") pairs.push([history[i], history[i + 1]]);
  }
  const encoder = new TextEncoder();
  const fits = (items) => {
    const json = JSON.stringify(items);
    return items.length <= maxMessages && items.reduce((n, m) => n + m.content.length, 0) <= maxChars && encoder.encode(json).length <= maxBytes;
  };
  const result = [current];
  for (const pair of pairs.reverse()) {
    if (!fits([...pair, ...result])) break;
    result.unshift(...pair);
  }
  if (!fits(result)) throw new Error("消息过长，请缩短后重试。");
  return result;
}
