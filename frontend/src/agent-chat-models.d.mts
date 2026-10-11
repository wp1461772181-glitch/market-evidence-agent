import type { AgentChatConfig } from "./api";
export function fetchAgentChatModels(config: Pick<AgentChatConfig,"provider"|"base_url"> & Partial<Pick<AgentChatConfig,"api_key">>,signal:AbortSignal,configuredApiBaseUrl?:string,fetcher?:typeof fetch):Promise<Response>;
