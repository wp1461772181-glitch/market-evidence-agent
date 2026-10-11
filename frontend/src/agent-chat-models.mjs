import { agentChatUrl } from "./agent-chat-url.mjs";

export function fetchAgentChatModels(config, signal, configuredApiBaseUrl = "", fetcher = fetch) {
  return fetcher(agentChatUrl("models", configuredApiBaseUrl), {
    method: "POST", signal, cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}
