export function agentChatUrl(path, configuredApiBaseUrl = "") {
  const base = configuredApiBaseUrl.replace(/\/+$/, "");
  const backendPath = `/agent-chat/${path}`;
  return base ? `${base}${backendPath}` : `/api${backendPath}`;
}

export function rewriteAgentChatProxyPath(path) {
  return path.replace(/^\/api(?=\/agent-chat\/)/, "");
}
