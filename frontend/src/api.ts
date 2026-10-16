import type { DashboardResponse, EvidenceRevision, EvidenceRevisionInventory, EvidenceRevisionRequest, FilingContent, FilingInventory, FilingReview, FilingScanResult, ForecastRunResult, JevLearningStatus, LocalizationResult, MaterialAnalysisHistory, MaterialAnalysisJob, MaterialAnalysisVersion, MaterialItem, MaterialLibraryResponse, MaterialOriginal, MaterialSourceType, PriceHistory, UploadedEvidence, UploadedEvidenceInventory, V2EvaluationResponse, V2ForecastJob, V2ForecastRoots, V2MonitorStatus, V2SourceRef, V2Timeline, V2VersionDetail, V2Workspace } from "./types";
import { agentChatUrl } from "./agent-chat-url.mjs";
import { fetchAgentChatModels } from "./agent-chat-models.mjs";
import { apiErrorMessage } from "./api-errors.mjs";
import { normalizeContextUsage, type ContextUsage } from "./context-usage.mjs";
import { activeChatCredential, applyChatIdentity, changeChatMode, currentChatMode, conversationCreatedAtForMode, conversationForMode, selectConversationForMode } from "./chat-identity.mjs";

export const configuredApiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

function apiUrl(path: string): string {
  if (!configuredApiBaseUrl) return path;
  const backendPath = path.startsWith("/api/") ? path.slice("/api".length) : path;
  return `${configuredApiBaseUrl}${backendPath}`;
}

async function fetchApi(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = applyChatIdentity(init.headers ?? {}, typeof window === "undefined" ? "" : chatCredential());
  headers.set("Accept", "application/json");
  return fetch(apiUrl(path), { ...init, headers });
}

export type AgentChatProvider = "deepseek" | "kimi" | "minimax" | "qwen" | "zhipu" | "doubao" | "hunyuan" | "baidu" | "siliconflow" | "openrouter" | "custom";
export type AgentChatConfig = { provider: AgentChatProvider; base_url?: string; model: string; api_key: string };
export type AgentCitation = { ordinal?:number; chunk_id?: string | null; document_id?: string | null; title: string; section: string; source_url?: string | null; observed_at?: string | null; published_at?:string|null; quote?:string; source_type: string; truncated: boolean; derived?:boolean };
export type KnowledgeDocumentSummary = { id:string; title:string; symbol:string|null; source_type:string; observed_at:string|null; available_at:string|null; status:string; active:boolean; owner_scope:string; chunks:number };
export type KnowledgeChunkDetail = { id:string; ordinal:number; section:string; answer:string; status:string; needs_review:boolean };
export type AgentClarification = {request_id:string;reason:string;question?:string;display?:{title?:string;saved_date?:string;web_date?:string;url?:string;alternative?:string;targets?:Array<Record<string,string>>};choices:Array<{label:string;value:string}>};
export type AgentChatMessage = { role: "user" | "assistant" | "tool"; content: string; status?: string; turn_id?: string; reused_sources?: boolean; citations?: AgentCitation[]; clarification?: AgentClarification; tool_calls?: Array<{ id: string; function: { name: string; arguments: string } }>; tool_call_id?: string; tool_trace?: Array<{ call_id?: string; name: string; arguments?: unknown; result?: unknown; status: string }> };
export type AgentChatEvent = { event: string; data: Record<string, unknown> };
const chatConversationKey = "market-evidence-agent:chat-conversation:v1";
export type ChatMode = "guest" | "personal";
export function getChatMode(): ChatMode { return currentChatMode(window.localStorage); }
export function setChatMode(mode: ChatMode, credential?: string): void {
  changeChatMode(window.localStorage,window.sessionStorage,mode,credential);
}
function chatCredential(): string {
  return activeChatCredential(window.localStorage,window.sessionStorage,()=>`${crypto.randomUUID()}${crypto.randomUUID()}`);
}
export async function personalChatLogin(code: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, "personal-login"), { method:"POST", signal, cache:"no-store", headers:{Accept:"application/json","Content-Type":"application/json"}, body:JSON.stringify({code}) });
  const payload = await response.json().catch(()=>null) as {credential?:unknown}|null;
  if (!response.ok || typeof payload?.credential !== "string") throw new ApiError("个人登录暂不可用。", response.status);
  return payload.credential;
}
export type ConversationSummary = { id:string; title:string; created_at:string; updated_at:string; custom_title?:boolean };
export type { ContextUsage };
/** What the next message of this conversation carries of the earlier ones, by part; null when it cannot be read.
 *  Without an id it is the fixed part only (a conversation that has not started). */
export async function getContextUsage(id:string|null,signal?:AbortSignal): Promise<ContextUsage|null> {
  const base = agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, "");
  const url = id ? `${base}conversations/${encodeURIComponent(id)}/context` : `${base}context`;
  const response = await fetch(url,{signal,cache:"no-store",headers:{Accept:"application/json","X-Chat-Session":chatCredential()}});
  if(!response.ok) return null;
  return normalizeContextUsage(await response.json().catch(()=>null));
}
/** Name one of the caller's conversations; resolves to the saved name, or null when it was cleared. */
export async function renameAgentConversation(id:string,title:string,signal?:AbortSignal): Promise<string|null> {
  const url = agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, `conversations/${encodeURIComponent(id)}`);
  const response = await fetch(url,{method:"PATCH",signal,cache:"no-store",headers:{Accept:"application/json","Content-Type":"application/json","X-Chat-Session":chatCredential()},body:JSON.stringify({title})});
  if(!response.ok) throw new ApiError(response.status===404?"找不到这个对话。":response.status===422?"名称无效或超过 80 个字。":"重命名失败，请重试。",response.status);
  const data=await response.json().catch(()=>null) as {title?:unknown}|null;
  return typeof data?.title==="string"&&data.title?data.title:null;
}
/** The owner's research queue as the server keeps it: null until one was saved.  Owner only. */
export async function getResearchQueue(signal?:AbortSignal): Promise<string[]|null> {
  const url = agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, "research-queue");
  const response = await fetch(url,{signal,cache:"no-store",headers:{Accept:"application/json","X-Chat-Session":chatCredential()}});
  if(!response.ok) throw new ApiError("研究队列暂时无法读取。",response.status);
  const data=await response.json().catch(()=>null) as {symbols?:unknown}|null;
  return Array.isArray(data?.symbols)?data.symbols.filter((item):item is string=>typeof item==="string"):null;
}
export async function putResearchQueue(symbols:string[],signal?:AbortSignal): Promise<void> {
  const url = agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, "research-queue");
  const response = await fetch(url,{method:"PUT",signal,cache:"no-store",headers:{Accept:"application/json","Content-Type":"application/json","X-Chat-Session":chatCredential()},body:JSON.stringify({symbols})});
  if(!response.ok) throw new ApiError("研究队列保存失败。",response.status);
}
export async function listAgentConversations(offset=0,signal?:AbortSignal): Promise<{conversations:ConversationSummary[];hasMore:boolean}> {
  const url = agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, `conversations?limit=30&offset=${offset}`);
  const response = await fetch(url,{signal,cache:"no-store",headers:{Accept:"application/json","X-Chat-Session":chatCredential()}});
  if(!response.ok) throw new ApiError("历史对话暂时无法加载。",response.status);
  const data=await response.json() as {conversations?:unknown;has_more?:unknown};
  return {conversations:Array.isArray(data.conversations)?data.conversations as unknown as ConversationSummary[]:[],hasMore:data.has_more===true};
}
export async function restoreAgentChat(selectedId?: string | null, signal?: AbortSignal, requireExisting=false): Promise<{ conversationId: string | null; createdAt:string|null; messages: AgentChatMessage[] }> {
  const conversationId = selectedId ?? conversationForMode(window.localStorage) ?? window.sessionStorage.getItem(chatConversationKey);
  if (!conversationId) return { conversationId: null, createdAt:null, messages: [] };
  const response = await fetch(agentChatUrl("messages", configuredApiBaseUrl).replace(/messages$/, `conversations/${encodeURIComponent(conversationId)}/messages`), {
    signal, headers: { Accept: "application/json", "X-Chat-Session": chatCredential() }, cache: "no-store",
  });
  if (response.status === 404) { if(requireExisting)throw new ApiError("无法恢复服务端聊天记录。",404); return { conversationId: null, createdAt:null, messages: [] }; }
  if (!response.ok) throw new ApiError("无法恢复服务端聊天记录。", response.status);
  const data = await response.json() as { messages?: AgentChatMessage[] };
  return { conversationId, createdAt:conversationCreatedAtForMode(window.localStorage), messages: Array.isArray(data.messages) ? data.messages : [] };
}
export function clearAgentConversation(): void { selectConversationForMode(window.localStorage,window.sessionStorage,null); }
export function selectAgentConversation(id:string|null,createdAt?:string|null):void { selectConversationForMode(window.localStorage,window.sessionStorage,id,undefined,createdAt); }
export async function agentChatStream(config: AgentChatConfig, message: string, conversationId: string | null, signal: AbortSignal, onEvent: (event: AgentChatEvent) => boolean | void, pendingRequestId?:string, displayMessage?:string): Promise<string> {
  const response = await fetch(agentChatUrl("stream", configuredApiBaseUrl), { method: "POST", signal, cache: "no-store", headers: { Accept: "text/event-stream", "Content-Type": "application/json", "X-Chat-Session": chatCredential() }, body: JSON.stringify({ ...config, message, conversation_id: conversationId, ...(pendingRequestId?{pending_request_id:pendingRequestId,...(displayMessage?{display_message:displayMessage}:{})}:{}) }) });
  if (!response.ok) { const payload = await response.json().catch(() => null); throw apiErrorFromPayload(payload, response.status); }
  const reader = response.body?.getReader(); if (!reader) throw new ApiError("服务未返回流式响应。");
  const decoder = new TextDecoder(); let buffer = "", answer = "", eventName = "message", receivedDone = false;
  while (true) {
    const { value, done } = await reader.read(); buffer += decoder.decode(value, { stream: !done });
    let boundary; while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2); let raw = "";
      for (const line of chunk.split(/\r?\n/)) { if (line.startsWith("event:")) eventName = line.slice(6).trim(); else if (line.startsWith("data:")) raw += line.slice(5).trim(); }
      if (!raw) continue; let data: Record<string, unknown>; try { data = JSON.parse(raw); } catch { throw new ApiError("服务返回了无效的聊天事件。"); }
      if (eventName === "delta" && typeof data.text === "string") answer += data.text;
      const receivedEvent = eventName;
      const accepted = onEvent({ event: receivedEvent, data });
      if (accepted !== false && receivedEvent === "conversation" && typeof data.conversation_id === "string") window.sessionStorage.setItem(chatConversationKey, data.conversation_id);
      if (receivedEvent === "done" && ["complete","succeeded","partially_succeeded","waiting_for_input","running","failed"].includes(String(data.status))) receivedDone = true;
      eventName = "message";
      if (receivedEvent === "error") throw new ApiError(String(data.code ?? "chat_failed"));
    }
    if (done) break;
  }
  if (!receivedDone) throw new ApiError("provider_incomplete_stream");
  return answer;
}
export async function agentChatRequest(path: "hello" | "messages", config: AgentChatConfig, messages: AgentChatMessage[], signal: AbortSignal): Promise<{ content: string; model: string }> {
  const response = await fetch(agentChatUrl(path, configuredApiBaseUrl), {
    method: "POST", signal, cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ ...config, messages }),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw apiErrorFromPayload(payload, response.status);
  return payload as { content: string; model: string };
}

export async function agentChatModels(config: Pick<AgentChatConfig, "provider" | "base_url"> & Partial<Pick<AgentChatConfig,"api_key">>, signal: AbortSignal): Promise<{status:string;ordering:string;models:Array<{id:string;name:string}>}> {
  const response = await fetchAgentChatModels(config, signal, configuredApiBaseUrl);
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {detail?:unknown}|null;
    const code = typeof payload?.detail === "string" && /^[a-z0-9_]{1,80}$/.test(payload.detail) ? payload.detail : "model_discovery_unavailable";
    throw new ApiError(code, response.status);
  }
  const payload = await response.json() as {status?:unknown;ordering?:unknown;models?:unknown};
  return {status:typeof payload.status === "string"?payload.status:"empty",ordering:typeof payload.ordering === "string"?payload.ordering:"unknown",models:Array.isArray(payload.models)?payload.models.slice(0,5) as Array<{id:string;name:string}>:[]};
}

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

export async function getDashboard(symbol: string, signal: AbortSignal): Promise<DashboardResponse> {
  const timeout = AbortSignal.timeout(12_000);
  let response: Response;
  try {
    response = await fetchApi(`/api/dashboard/${encodeURIComponent(symbol)}`, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.any([signal, timeout]),
    });
  } catch (error) {
    if (timeout.aborted) throw new ApiError("读取超过 12 秒，请确认本地 API 已启动后重试。");
    throw error;
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : `请求失败（${response.status}）`;
    throw new ApiError(detail, response.status);
  }
  if (!isDashboard(payload)) throw new ApiError("服务返回了无法识别的数据格式。");
  return payload;
}

export async function getFilingInventory(symbol: string, signal: AbortSignal): Promise<FilingInventory> {
  return requestJson<FilingInventory>(`/api/filing-inventories/${encodeURIComponent(symbol)}`, { signal });
}

export async function scanOfficialFilings(symbol: string): Promise<FilingScanResult> {
  return requestJson<FilingScanResult>(`/api/filing-inventories/${encodeURIComponent(symbol)}/scan`, { method: "POST" });
}

export async function initializeMarketData(symbol: string): Promise<{ symbol: string; inserted_count: number; skipped_count: number }> {
  return requestJson(`/api/market-data/${encodeURIComponent(symbol)}/initialize`, { method: "POST" });
}


export async function fetchFilingContent(symbol: string, accessionNumber: string, refresh = false): Promise<FilingContent> {
  const query = refresh ? "?refresh=true" : "";
  return requestJson<FilingContent>(`/api/filing-inventories/${encodeURIComponent(symbol)}/${encodeURIComponent(accessionNumber)}/fetch${query}`, { method: "POST" });
}

export async function reviewFiling(symbol: string, accessionNumber: string, decision: "accepted" | "rejected", note: string): Promise<FilingReview> {
  return requestJson<FilingReview>(`/api/filing-inventories/${encodeURIComponent(symbol)}/${encodeURIComponent(accessionNumber)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, note }),
  });
}

export async function createForecastRun(symbol: string): Promise<ForecastRunResult> {
  return requestJson<ForecastRunResult>("/api/forecast-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
}

export async function getUploadedEvidence(symbol: string, signal?: AbortSignal): Promise<UploadedEvidenceInventory> {
  return requestJson<UploadedEvidenceInventory>(`/api/uploaded-evidence/${encodeURIComponent(symbol)}`, { signal });
}

export async function uploadEvidence(
  symbol: string,
  input: {
    file: File;
    title: string;
    sourceUrl: string;
    publishedAt: string;
    credibilityStars: number;
    credibilityReason: string;
    impactSeverity: "low" | "medium" | "high";
  },
): Promise<UploadedEvidence> {
  const form = new FormData();
  form.set("file", input.file);
  form.set("title", input.title);
  form.set("source_url", input.sourceUrl);
  form.set("published_at", input.publishedAt);
  form.set("credibility_stars", String(input.credibilityStars));
  form.set("credibility_reason", input.credibilityReason);
  form.set("impact_severity", input.impactSeverity);
  return requestJson<UploadedEvidence>(`/api/uploaded-evidence/${encodeURIComponent(symbol)}`, { method: "POST", headers: { "X-Chat-Session": chatCredential() }, body: form });
}

export async function getEvidenceRevisions(symbol: string, signal?: AbortSignal): Promise<EvidenceRevisionInventory> {
  return requestJson<EvidenceRevisionInventory>(`/api/evidence-revisions/${encodeURIComponent(symbol)}`, { signal });
}

export async function createEvidenceRevision(symbol: string, input: EvidenceRevisionRequest): Promise<EvidenceRevision> {
  return requestJson<EvidenceRevision>(`/api/evidence-revisions/${encodeURIComponent(symbol)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function getV2Workspace(symbol: string, signal?: AbortSignal): Promise<V2Workspace> {
  return requestJson<V2Workspace>(`/api/v2/stocks/${encodeURIComponent(symbol)}/workspace`, { signal });
}

export async function getV2Prices(symbol: string, signal?: AbortSignal): Promise<{ symbol: string; price_history: PriceHistory }> {
  return requestJson<{ symbol: string; price_history: PriceHistory }>(`/api/v2/stocks/${encodeURIComponent(symbol)}/prices`, { signal });
}

export async function getMaterialLibrary(
  symbol: string,
  options: { sourceType?: MaterialSourceType | "all"; analysisStatus?: string; reviewStatus?: string; offset?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<MaterialLibraryResponse> {
  const params = new URLSearchParams({ symbol, limit: String(options.limit ?? 10), offset: String(options.offset ?? 0) });
  if (options.sourceType && options.sourceType !== "all") params.set("source_type", options.sourceType);
  if (options.analysisStatus && options.analysisStatus !== "all") params.set("analysis_status", options.analysisStatus);
  if (options.reviewStatus && options.reviewStatus !== "all") params.set("review_status", options.reviewStatus);
  return requestJson<MaterialLibraryResponse>(`/api/v3/materials?${params}`, { signal: options.signal });
}

export async function createMaterialAnalysisJob(
  sourceType: MaterialSourceType,
  sourceId: string,
  force = false,
): Promise<MaterialAnalysisJob> {
  const idempotencyKey = newIdempotencyKey();
  return requestJson<MaterialAnalysisJob>(`/api/v3/materials/${sourceType}/${encodeURIComponent(sourceId)}/analysis-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ idempotency_key: idempotencyKey, force }),
  });
}

export async function getMaterialAnalysisJob(jobId: string, signal?: AbortSignal): Promise<MaterialAnalysisJob> {
  return requestJson<MaterialAnalysisJob>(`/api/v3/material-analysis-jobs/${encodeURIComponent(jobId)}`, { signal });
}

export async function getMaterialAnalysisHistory(
  sourceType: MaterialSourceType,
  sourceId: string,
  signal?: AbortSignal,
): Promise<MaterialAnalysisHistory> {
  return requestJson<MaterialAnalysisHistory>(`/api/v3/materials/${sourceType}/${encodeURIComponent(sourceId)}/analyses`, { signal });
}

export async function getMaterialAnalysis(analysisId: string, signal?: AbortSignal): Promise<MaterialAnalysisVersion> {
  return requestJson<MaterialAnalysisVersion>(`/api/v3/material-analyses/${encodeURIComponent(analysisId)}`, { signal });
}

export async function localizeMaterialAnalysis(analysisId: string, signal?: AbortSignal): Promise<LocalizationResult> {
  return requestJson<LocalizationResult>(`/api/v3/material-analyses/${encodeURIComponent(analysisId)}/localization`, { method: "POST", signal });
}

export async function localizeForecastBrief(versionId: string, signal?: AbortSignal): Promise<LocalizationResult> {
  return requestJson<LocalizationResult>(`/api/v2/forecast-versions/${encodeURIComponent(versionId)}/brief-localization`, { method: "POST", signal });
}

export async function getMaterialOriginal(
  sourceType: MaterialSourceType,
  sourceId: string,
  signal?: AbortSignal,
): Promise<MaterialOriginal> {
  return requestJson<MaterialOriginal>(`/api/v3/materials/${sourceType}/${encodeURIComponent(sourceId)}/original`, { signal });
}

export async function getV2Job(jobId: string, signal?: AbortSignal): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>(`/api/v2/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export async function getV2Timeline(rootId: string, signal?: AbortSignal): Promise<V2Timeline> {
  return requestJson<V2Timeline>(`/api/v2/forecast-roots/${encodeURIComponent(rootId)}/timeline`, { signal });
}

export async function getV2ForecastRoots(symbol: string, signal?: AbortSignal): Promise<V2ForecastRoots> {
  return requestJson<V2ForecastRoots>(`/api/v2/stocks/${encodeURIComponent(symbol)}/forecast-roots`, { signal });
}

export async function getV2MonitorStatus(signal?: AbortSignal): Promise<V2MonitorStatus> {
  return requestJson<V2MonitorStatus>("/api/v2/monitor/status", { signal });
}

export async function getV2Evaluations(symbol: string, signal?: AbortSignal): Promise<V2EvaluationResponse> {
  return requestJson<V2EvaluationResponse>(`/api/v2/evaluations?symbol=${encodeURIComponent(symbol)}`, { signal });
}

export async function getJevLearningStatus(symbol: string, signal?: AbortSignal): Promise<JevLearningStatus> {
  return requestJson<JevLearningStatus>(`/api/v2/jev-learning/status?symbol=${encodeURIComponent(symbol)}`, { signal });
}

export async function getV2ForecastVersion(versionId: string, signal?: AbortSignal): Promise<V2VersionDetail> {
  return requestJson<V2VersionDetail>(`/api/v2/forecast-versions/${encodeURIComponent(versionId)}`, { signal });
}

export async function createV2ForecastJob(symbol: string, sourceRefs: V2SourceRef[] = []): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>("/api/v2/forecast-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ symbol, kind: "new", source_refs: sourceRefs }),
  });
}

export async function createV2HistoricalReplayJob(symbol: string, decisionDate: string): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>("/api/v2/historical-replays", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ symbol, decision_date: decisionDate }),
  });
}

export async function createV2ManualRevisionJob(versionId: string, sourceRefs: V2SourceRef[]): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>(`/api/v2/forecast-versions/${encodeURIComponent(versionId)}/revision-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ source_refs: sourceRefs }),
  });
}

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetchApi(url, init);
  } catch (error) {
    throw error;
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const ownerWrite = ["POST", "PUT", "PATCH", "DELETE"].includes((init.method ?? "GET").toUpperCase());
    if (response.status === 401 && ownerWrite) window.dispatchEvent(new Event("owner-auth-required"));
    throw apiErrorFromPayload(payload, response.status, ownerWrite);
  }
  return payload as T;
}

function apiErrorFromPayload(payload: unknown, status: number, ownerWrite = false): ApiError {
  return new ApiError(apiErrorMessage(payload, status, { ownerWrite }), status);
}

function isDashboard(value: unknown): value is DashboardResponse {
  return typeof value === "object" && value !== null
    && "symbol" in value && "snapshots" in value && Array.isArray((value as { snapshots: unknown }).snapshots)
    && "refresh_reports" in value && Array.isArray((value as { refresh_reports: unknown }).refresh_reports);
}

export async function getKnowledgeDocuments(): Promise<KnowledgeDocumentSummary[]> {
  const result=await requestJson<{items:KnowledgeDocumentSummary[]}>('/api/knowledge/documents',{headers:{'X-Chat-Session':chatCredential()}});
  return result.items;
}
export async function getKnowledgeChunks(documentId:string):Promise<{document:{id:string;title:string;source_text:string;source_url:string|null;status:string};chunks:KnowledgeChunkDetail[]}> {
  return requestJson(`/api/knowledge/documents/${encodeURIComponent(documentId)}/chunks`,{headers:{'X-Chat-Session':chatCredential()}});
}
export async function importKnowledge(source_type:'faq'|'upload'|'sec'|'analysis',source_ids:string[]):Promise<{ok:boolean;accepted_sources:number;chunks:number;needs_review:number}> {
  return requestJson('/api/knowledge/import',{method:'POST',headers:{'X-Chat-Session':chatCredential(),'Content-Type':'application/json'},body:JSON.stringify({source_type,source_ids})});
}
export async function runKnowledgeIndex():Promise<{ok:boolean;indexed:number;failed:number;idle:number}> {
  return requestJson('/api/knowledge/index',{method:'POST',headers:{'X-Chat-Session':chatCredential(),'Content-Type':'application/json'},body:JSON.stringify({max_tasks:100})});
}
export async function testKnowledgeRetrieval(kind:'faq'|'material'|'analysis',keyword:string,symbol?:string):Promise<{result:{ok:boolean;data:unknown[]|null;error_code?:string};citations:AgentCitation[]}> {
  return requestJson('/api/knowledge/retrieval-test',{method:'POST',headers:{'X-Chat-Session':chatCredential(),'Content-Type':'application/json'},body:JSON.stringify({kind,keyword,...(symbol?{symbol}:{})})});
}
export async function retryKnowledgeDocument(documentId:string):Promise<{ok:boolean;queued_chunks:number}> {
  return requestJson(`/api/knowledge/documents/${encodeURIComponent(documentId)}/retry`,{method:'POST',headers:{'X-Chat-Session':chatCredential()}});
}
export async function withdrawKnowledgeDocument(documentId:string):Promise<{ok:boolean;status:string}> {
  return requestJson(`/api/knowledge/documents/${encodeURIComponent(documentId)}/withdraw`,{method:'POST',headers:{'X-Chat-Session':chatCredential()}});
}
export async function getKnowledgeCandidates():Promise<Array<{id:string;question:string;answer:string;category:string;evidence_quote:string}>> {
  const result=await requestJson<{items:Array<{id:string;question:string;answer:string;category:string;evidence_quote:string}>}>('/api/knowledge/candidates',{headers:{'X-Chat-Session':chatCredential()}});return result.items;
}
export async function reviewKnowledgeCandidate(id:string,approved:boolean,reviewer:string):Promise<void> {
  await requestJson(`/api/knowledge/candidates/${encodeURIComponent(id)}/review`,{method:'POST',headers:{'X-Chat-Session':chatCredential(),'Content-Type':'application/json'},body:JSON.stringify({approved,reviewer})});
}
export async function getKnowledgeImportable(source:'faq'|'upload'|'sec'|'analysis',symbol:string):Promise<Array<{id:string;title:string;symbol:string|null;source_type:string}>> {
  const result=await requestJson<{items:Array<{id:string;title:string;symbol:string|null;source_type:string}>}>(`/api/knowledge/importable/${source}?symbol=${encodeURIComponent(symbol)}`,{headers:{'X-Chat-Session':chatCredential()}});return result.items;
}
export type KnowledgeCitationDetail={title:string;section:string;excerpt:string;source_url:string|null;observed_at:string|null;coverage:string;start_offset:number;end_offset:number;derived?:boolean;analysis_version?:string;version_no?:number|null;model?:string;classification?:string;original_source_type?:string;original_quotes?:string[]};
export async function getKnowledgeCitation(chunkId:string):Promise<KnowledgeCitationDetail>{return requestJson(`/api/knowledge/citations/${encodeURIComponent(chunkId)}`,{headers:{'X-Chat-Session':chatCredential()}})}
