export function plainChatPreview(value, limit = 140) {
  const text = String(value ?? "")
    .replace(/<[^>]*>/g, " ")
    .replace(/```[\s\S]*?```/g, " code ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_~|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (/\{\s*["'][^"']+["']\s*:/.test(text) || /^\[\s*\{/.test(text)) return "结构化内容";
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…` : text;
}

export function chatTurnPreviews(messages) {
  const turns = [];
  messages.forEach((message, userIndex) => {
    if (message.role !== "user") return;
    const nextUserIndex = messages.findIndex((item, index) => index > userIndex && item.role === "user");
    const turnEnd = nextUserIndex < 0 ? messages.length : nextUserIndex;
    const answer = messages.slice(userIndex + 1, turnEnd).find(item => item.role === "assistant")?.content ?? "";
    turns.push({ userIndex, question: plainChatPreview(message.content, 220), answer: plainChatPreview(answer, 300) });
  });
  return turns;
}
