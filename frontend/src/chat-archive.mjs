export const ARCHIVE_MARKER = "market-evidence-agent-chat-archive";
export const MAX_ARCHIVE_BYTES = 5_000_000;
export const MAX_ARCHIVE_MESSAGES = 10_000;
const TRACE_STATES = new Set(["running", "complete", "not_found", "failed", "cancelled"]);

function safeJson(value, depth = 0) {
  if (depth > 12) throw new Error("工具记录嵌套过深");
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map(item => safeJson(item, depth + 1));
  if (typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      if (["__proto__", "constructor", "prototype", "api_key", "authorization", "token", "secret", "credential", "command", "executable", "shell"].includes(key.toLowerCase())) throw new Error("工具记录包含不允许的字段");
      result[key] = safeJson(item, depth + 1);
    }
    return result;
  }
  throw new Error("工具记录格式无效");
}

export function createArchive(conversations = [], now = new Date().toISOString()) {
  return { marker: ARCHIVE_MARKER, version: 1, created_at: now, updated_at: now, conversations };
}

export function validateArchive(input) {
  const bytes = new TextEncoder().encode(typeof input === "string" ? input : JSON.stringify(input)).length;
  if (bytes > MAX_ARCHIVE_BYTES) throw new Error("归档文件超过 5 MB 限制");
  const archive = typeof input === "string" ? JSON.parse(input) : input;
  if (!archive || archive.marker !== ARCHIVE_MARKER || archive.version !== 1 || !Number.isFinite(Date.parse(archive.created_at)) || !Number.isFinite(Date.parse(archive.updated_at)) || !Array.isArray(archive.conversations)) throw new Error("归档标记、版本或时间格式无效");
  let count = 0; const ids = new Set();
  const conversations = archive.conversations.map(conversation => {
    if (!conversation || typeof conversation.id !== "string" || !/^[\w-]{1,80}$/.test(conversation.id) || ids.has(conversation.id) || !Number.isFinite(Date.parse(conversation.created_at)) || !Number.isFinite(Date.parse(conversation.updated_at)) || !Array.isArray(conversation.messages)) throw new Error("对话归档结构无效");
    ids.add(conversation.id);
    const messages = conversation.messages.map(message => {
      if (++count > MAX_ARCHIVE_MESSAGES) throw new Error("归档消息数量超过限制");
      if (!message || !["user", "assistant"].includes(message.role) || typeof message.content !== "string" || message.content.length > 100_000 || (message.status !== undefined && !["complete", "failed", "cancelled"].includes(message.status))) throw new Error("消息角色、长度或状态无效");
      const result = { role: message.role, content: message.content };
      if (message.status) result.status = message.status;
      if (message.tool_trace !== undefined) {
        if (message.role !== "assistant" || !Array.isArray(message.tool_trace) || message.tool_trace.length > 100) throw new Error("工具轨迹格式无效");
        result.tool_trace = message.tool_trace.map(trace => {
          if (!trace || typeof trace.name !== "string" || !/^[\w.-]{1,100}$/.test(trace.name) || (trace.call_id !== undefined && (typeof trace.call_id !== "string" || trace.call_id.length > 200)) || !TRACE_STATES.has(trace.status)) throw new Error("工具轨迹字段无效");
          const safe = { name: trace.name, status: trace.status };
          if (trace.call_id) safe.call_id = trace.call_id;
          if (trace.arguments !== undefined) safe.arguments = safeJson(trace.arguments);
          if (trace.result !== undefined) safe.result = safeJson(trace.result);
          return safe;
        });
      }
      return result;
    });
    return { id: conversation.id, created_at: conversation.created_at, updated_at: conversation.updated_at, messages };
  });
  return { marker: ARCHIVE_MARKER, version: 1, created_at: archive.created_at, updated_at: archive.updated_at, conversations };
}

export function mergeConversation(archive, conversation) {
  const valid = validateArchive(archive); const index = valid.conversations.findIndex(item => item.id === conversation.id);
  const next = [...valid.conversations];
  const clean = validateArchive(createArchive([conversation])).conversations[0];
  if (index < 0) next.push(clean); else next[index] = { ...clean, created_at: next[index].created_at };
  return { ...valid, conversations: next, updated_at: new Date().toISOString() };
}

export function createSerializedWriter(write) {
  let queue = Promise.resolve();
  return (target, value) => { const task = queue.then(() => write(target, value)); queue = task.catch(() => {}); return task; };
}

export async function writeAtomically(handle, value) {
  const stream = await handle.createWritable();
  try { await stream.write(value); await stream.close(); }
  catch (error) { try { if (typeof stream.abort === "function") await stream.abort(); else if (typeof stream.close === "function") await stream.close(); } catch {} throw error; }
}

export function isPickerCancelled(error) { return error instanceof Error && error.name === "AbortError"; }

export function serializeArchive(archive) { return `${JSON.stringify(validateArchive(archive), null, 2)}\n`; }
