import type { DashboardResponse, EvidenceRevision, EvidenceRevisionInventory, EvidenceRevisionRequest, FilingContent, FilingInventory, FilingReview, FilingScanResult, ForecastRunResult, JevLearningStatus, LocalizationResult, MaterialAnalysisHistory, MaterialAnalysisJob, MaterialAnalysisVersion, MaterialItem, MaterialLibraryResponse, MaterialOriginal, MaterialSourceType, PriceHistory, UploadedEvidence, UploadedEvidenceInventory, V2EvaluationResponse, V2ForecastJob, V2ForecastRoots, V2MonitorStatus, V2SourceRef, V2Timeline, V2VersionDetail, V2Workspace } from "./types";
import { agentChatUrl } from "./agent-chat-url.mjs";
import { fetchAgentChatModels } from "./agent-chat-models.mjs";
import { apiErrorMessage } from "./api-errors.mjs";
import { normalizeContextUsage, type ContextUsage } from "./context-usage.mjs";
import { activeChatCredential, applyChatIdentity, changeChatMode, currentChatMode, conversationCreatedAtForMode, conversationForMode, selectConversationForMode } from "./chat-identity.mjs";
import type { FlowNode } from "./turn-flow.mjs";

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
export type AgentClarification = {request_id:string;reason:string;question?:string;display?:{title?:string;agent_suggestion?:boolean;saved_date?:string;web_date?:string;url?:string;alternative?:string;targets?:Array<Record<string,string>>};choices:Array<{label:string;value:string}>;options?:Array<{value:string;label:string;detail?:string}>;select?:{min:number;max:number}};
export type AgentChatMessage = { role: "user" | "assistant" | "tool"; content: string; status?: string; turn_id?: string; reused_sources?: boolean; citations?: AgentCitation[]; clarification?: AgentClarification; tool_calls?: Array<{ id: string; function: { name: string; arguments: string } }>; tool_call_id?: string; tool_trace?: Array<{ call_id?: string; name: string; arguments?: unknown; result?: unknown; status: string; card?: boolean }>; trace?: FlowNode[]; thinking_ms?: number; started_at?: number };
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
/** Archive (hide) or restore one of this credential's conversations: a logical delete. */
export async function archiveAgentConversation(id:string,archived=true,signal?:AbortSignal): Promise<boolean> {
  const url = agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, `conversations/${encodeURIComponent(id)}`);
  const response = await fetch(url,{method:"PATCH",signal,cache:"no-store",headers:{Accept:"application/json","Content-Type":"application/json","X-Chat-Session":chatCredential()},body:JSON.stringify({archived})});
  if(!response.ok) throw new ApiError(response.status===404?"找不到这个对话。":"归档失败，请重试。",response.status);
  return true;
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
export type ProfileItem = { id:string; handle:string; kind:"preference"|"about_me"|"instruction"; topic:string|null; text:string; origin:"explicit"|"inferred"|"manual"; quote:string; status:"active"|"archived"; scope:"global"|"bot"; bot_id:string|null; bot_name:string|null; created_at:string|null; updated_at:string|null; last_used_at:string|null };
export type ProfileBot = { id:string; name:string; is_default:boolean };
export type RecallItem = { conversation_id:string; title:string; date:string|null; summary:string; kind:"summary"|"questions"; link:string; bot_id:string|null; bot_name:string|null };
function agentPath(path:string):string { return agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, path); }
function ownerHeaders(json=false):Record<string,string> { return {Accept:"application/json",...(json?{"Content-Type":"application/json"}:{}),"X-Chat-Session":chatCredential()}; }
/** The owner's profile items, active and archived (memory page).  Owner only: a guest gets 403. */
export async function listProfileItems(signal?:AbortSignal): Promise<{items:ProfileItem[];bots:ProfileBot[]}> {
  const response = await fetch(agentPath("profile"),{signal,cache:"no-store",headers:ownerHeaders()});
  if(!response.ok) throw new ApiError(response.status===403?"登录个人聊天后才能查看记忆。":"画像暂时无法读取。",response.status);
  const data=await response.json().catch(()=>null) as {items?:unknown;bots?:unknown}|null;
  return {items:Array.isArray(data?.items)?data.items as ProfileItem[]:[],bots:Array.isArray(data?.bots)?data.bots as ProfileBot[]:[]};
}
/** The owner types an item on the memory page: global (bot_id null) or one bot's own memory. */
export async function createProfileItem(fields:{kind:ProfileItem["kind"];text:string;bot_id:string|null},signal?:AbortSignal): Promise<ProfileItem> {
  const response = await fetch(agentPath("profile"),{method:"POST",signal,cache:"no-store",headers:ownerHeaders(true),body:JSON.stringify(fields)});
  if(!response.ok) throw new ApiError(response.status===409?"已有一条你确认过的同类设定，先编辑或删除它。":response.status===422?"这条没法保存：内容为空、太长，或 bot 已不存在。":"保存失败，请重试。",response.status);
  return (await response.json() as {item:ProfileItem}).item;
}
/** The owner edits an item's text or moves it to another layer (bot_id null: global). */
export async function updateProfileItem(id:string,fields:{text?:string;bot_id?:string|null},signal?:AbortSignal): Promise<ProfileItem> {
  const response = await fetch(agentPath(`profile/${encodeURIComponent(id)}`),{method:"PATCH",signal,cache:"no-store",headers:ownerHeaders(true),body:JSON.stringify(fields)});
  if(!response.ok) throw new ApiError(response.status===404?"这条已不存在。":"保存失败，请重试。",response.status);
  return (await response.json() as {item:ProfileItem}).item;
}
export async function confirmProfileItem(id:string,signal?:AbortSignal): Promise<ProfileItem> {
  const response = await fetch(agentPath(`profile/${encodeURIComponent(id)}/confirm`),{method:"POST",signal,cache:"no-store",headers:ownerHeaders()});
  if(!response.ok) throw new ApiError(response.status===404?"这条已不存在。":"确认失败，请重试。",response.status);
  return (await response.json() as {item:ProfileItem}).item;
}
export async function deleteProfileItem(id:string,signal?:AbortSignal): Promise<void> {
  const response = await fetch(agentPath(`profile/${encodeURIComponent(id)}`),{method:"DELETE",signal,cache:"no-store",headers:ownerHeaders()});
  if(!response.ok&&response.status!==404) throw new ApiError("删除失败，请重试。",response.status);
}
/** The owner's past conversations: newest first, or the best matches for a query.  Memory, not evidence. */
export async function listRecall(query="",signal?:AbortSignal,botId?:string|null): Promise<RecallItem[]> {
  const params=new URLSearchParams(query.trim()?{q:query.trim()}:{limit:"20"});
  if(botId) params.set("bot_id",botId);
  const response = await fetch(agentPath(`recall?${params.toString()}`),{signal,cache:"no-store",headers:ownerHeaders()});
  if(!response.ok) throw new ApiError(response.status===403?"登录个人聊天后才能查看记忆。":"往事暂时无法读取。",response.status);
  const data=await response.json().catch(()=>null) as {items?:unknown}|null;
  return Array.isArray(data?.items)?data.items as RecallItem[]:[];
}
// Scheduled tasks, the inbox and the background model (owner only).  The background key is sent once
// when saved and never comes back: the server answers with has_key only.
export type InboxSource = { ordinal:number; title:string; url:string|null; source_type:string|null; published_at?:string|null };
export type InboxItem = { id:string; section:"brief"|"confirm"; kind:"task_brief"|"filing"|"news"|"price"|"action"; title:string; body:string; sources:InboxSource[];
  payload:{ task_name?:string; channel_pending?:string; missed_slots?:number; display?:{ title?:string; targets?:unknown[]; alternative?:string } };
  task_id:string|null; bot_id:string|null; posted:boolean; conversation_id:string|null; status:"unread"|"read"|"done"|"dismissed"; created_at:string|null };
export type PriceLine = { symbol:string; above?:number; below?:number };
export type AgentTask = { id:string; name:string; kind:"brief"|"alerts"; schedule_kind:"daily"|"weekdays"|"hourly"; schedule_time:string; timezone:string;
  bot_id:string|null; skill_id:string|null; instruction:string|null; scope:"research_queue"|"symbols"; symbols:string[]; channel:"inbox"|"telegram";
  config:{ filings?:boolean; news?:boolean; price_lines?:PriceLine[] }; enabled:boolean; run_token_cap:number; daily_token_cap:number; tokens_today:number;
  last_run_at:string|null; next_run_at:string|null; last_status:string|null; last_status_label:string|null; last_conversation_id:string|null };
export type AgentTaskInput = Partial<Omit<AgentTask,"skill_id"|"id"|"tokens_today"|"last_run_at"|"next_run_at"|"last_status"|"last_status_label"|"last_conversation_id">> & {skill_id?:string|null};
export type BackgroundModel = { configured:boolean; has_key:boolean; provider?:string; model?:string; base_url?:string|null; revoked_at?:string|null; updated_at?:string|null };
async function ownerJson<T>(path:string,init:{method?:string;body?:unknown;signal?:AbortSignal},failure:string):Promise<T>{
  const response=await fetch(agentPath(path),{method:init.method??"GET",signal:init.signal,cache:"no-store",headers:ownerHeaders(init.body!==undefined),
    ...(init.body!==undefined?{body:JSON.stringify(init.body)}:{})});
  if(!response.ok){
    const detail=(await response.json().catch(()=>null) as {detail?:unknown}|null)?.detail;
    throw new ApiError(response.status===403||response.status===401?"登录个人聊天后才能使用简报和任务。":(typeof detail==="string"&&TASK_ERRORS[detail])||failure,response.status);
  }
  return await response.json() as T;
}
const TASK_ERRORS:Record<string,string>={invalid_schedule:"时间格式应为 HH:MM。",invalid_timezone:"时区无效。",invalid_instruction:"请写任务说明（最多 2000 字）。",
  invalid_symbols:"股票代码无效，或指定公司时没有填代码。",invalid_price_lines:"价格线无效：填股票代码和上穿或下穿的价格。",invalid_token_caps:"token 上限无效：单次 5000 到 150000，每日不少于单次。",
  too_many_tasks:"任务已达上限（20 个）。",invalid_name:"请填任务名称（最多 80 字）。",key_required:"请填写 API key。",invalid_key:"API key 格式不对。",
  invalid_endpoint:"接口地址无效：须为 HTTPS，自定义服务必须填写。",credential_key_missing:"服务器没有配置加密密钥，无法保存 key。",invalid_agent_chat_request:"填写的内容无效。",token_in_use:"这个 token 已经用在别的 bot 上了：一个 Telegram bot 只能接一个 bot。",bind_code_expired:"绑定码已过期，请重新生成。",
  invalid_bot_token:"Bot Token 格式无效：应从 BotFather 获取。",no_bot_token:"请先保存 Bot Token。",
  no_active_bind:"没有正在进行的绑定。",bind_code_mismatch:"绑定码不匹配。",bind_already_confirmed:"已绑定，请勿重复确认。",
  not_bound:"尚未绑定任何 Telegram 账号。",invalid_bind_code:"绑定码格式无效。",invalid_chat_id:"Chat ID 无效。",unknown_bot:"这个 bot 不存在或不是你的。",
  unknown_skill:"这个技能不存在。"};
export async function listAgentTasks(signal?:AbortSignal,botId?:string):Promise<AgentTask[]>{ return (await ownerJson<{tasks:AgentTask[]}>(botId?`tasks?bot_id=${encodeURIComponent(botId)}`:"tasks",{signal},"任务暂时无法读取。")).tasks; }
export type TaskRun = { id:string; task_id:string; task_name:string; status:string; status_label:string; posted:number; tokens:number; conversation_id:string|null; created_at:string|null };
export async function listTaskRuns(botId:string|undefined,signal?:AbortSignal):Promise<TaskRun[]>{ return (await ownerJson<{runs:TaskRun[]}>(botId?`tasks/runs?bot_id=${encodeURIComponent(botId)}&limit=20`:"tasks/runs?limit=20",{signal},"最近运行暂时无法读取。")).runs; }
/** A routine card a bot proposed in chat that the owner has not pressed yet (expired: its press no longer runs). */
export type TaskProposal = { id:string; conversation_id:string; bot_id:string|null; tools:string[]; title:string|null; targets:Array<Record<string,string>>; detail:string|null; expires_at:string; expired:boolean; created_at:string|null };
export async function listTaskProposals(signal?:AbortSignal):Promise<TaskProposal[]>{ return (await ownerJson<{proposals:TaskProposal[]}>("tasks/proposals",{signal},"任务暂时无法读取。")).proposals; }
export async function markBotRead(botId:string):Promise<number>{ return (await ownerJson<{read:number}>(`bots/${encodeURIComponent(botId)}/read`,{method:"POST"},"操作失败，请重试。")).read; }
export async function createAgentTask(task:AgentTaskInput):Promise<AgentTask>{ return (await ownerJson<{task:AgentTask}>("tasks",{method:"POST",body:task},"保存任务失败。")).task; }
export async function updateAgentTask(id:string,task:AgentTaskInput):Promise<AgentTask>{ return (await ownerJson<{task:AgentTask}>(`tasks/${encodeURIComponent(id)}`,{method:"PATCH",body:task},"保存任务失败。")).task; }
export async function deleteAgentTask(id:string):Promise<void>{ await ownerJson(`tasks/${encodeURIComponent(id)}`,{method:"DELETE"},"删除任务失败。"); }
export async function listInbox(section?:"brief"|"confirm",signal?:AbortSignal,botId?:string):Promise<InboxItem[]>{
  const query=[section&&`section=${section}`,botId&&`bot_id=${encodeURIComponent(botId)}`].filter(Boolean).join("&");
  return (await ownerJson<{items:InboxItem[]}>(query?`inbox?${query}`:"inbox",{signal},"简报暂时无法读取。")).items;
}
export async function setInboxStatus(id:string,status:InboxItem["status"]):Promise<InboxItem>{
  return (await ownerJson<{item:InboxItem}>(`inbox/${encodeURIComponent(id)}/status`,{method:"POST",body:{status}},"操作失败，请重试。")).item;
}
/** Mint the confirmation card of a 待你确认 item in its conversation; open that conversation to press it. */
export async function mintInboxCard(id:string):Promise<{conversation_id:string;request_id:string;expires_at:string|null}>{
  return await ownerJson(`inbox/${encodeURIComponent(id)}/card`,{method:"POST"},"生成确认卡失败，请重试。");
}
export async function getBackgroundModel(signal?:AbortSignal):Promise<BackgroundModel>{ return await ownerJson("background-model",{signal},"后台模型设置暂时无法读取。"); }
export async function saveBackgroundModel(value:{provider:string;model:string;base_url?:string|null;api_key?:string}):Promise<BackgroundModel>{
  return await ownerJson("background-model",{method:"PUT",body:value},"保存失败。");
}
export async function revokeBackgroundKey():Promise<BackgroundModel>{ return await ownerJson("background-model/key",{method:"DELETE"},"撤销失败，请重试。"); }
export type TelegramBindStatus = { configured:boolean; has_token:boolean; bound:boolean; bot_username?:string|null; chat_id?:number|null; bound_at?:string|null; bind_code_active?:boolean };
export async function getTelegramBind(signal?:AbortSignal):Promise<TelegramBindStatus>{ return await ownerJson("telegram/status",{signal},"Telegram 设置暂时无法读取。"); }
export async function saveTelegramToken(token:string):Promise<TelegramBindStatus>{
  return await ownerJson("telegram/token",{method:"POST",body:{bot_token:token}},"保存失败。");
}
export async function startTelegramBind():Promise<{code:string;bot_username?:string|null}>{
  return await ownerJson("telegram/bind",{method:"POST"},"生成绑定码失败。");
}
export async function unbindTelegram():Promise<TelegramBindStatus>{
  return await ownerJson("telegram/unbind",{method:"POST"},"解绑失败。");
}
// A bot's own Telegram bot (UX batch 2, P4).  The default bot's is the shared one above.
export type BotTelegramStatus = TelegramBindStatus & { shared:boolean; status?:"none"|"unbound"|"running"|"invalid"|"conflict"; token_tail?:string|null; last_error?:string|null };
const botTelegram=(botId:string,rest="")=>`bots/${encodeURIComponent(botId)}/telegram${rest}`;
export async function getBotTelegram(botId:string,signal?:AbortSignal):Promise<BotTelegramStatus>{ return await ownerJson(botTelegram(botId),{signal},"Telegram 设置暂时无法读取。"); }
export async function saveBotTelegramToken(botId:string,token:string):Promise<BotTelegramStatus>{ return await ownerJson(botTelegram(botId,"/token"),{method:"POST",body:{bot_token:token}},"保存失败：token 无效，或已用在别的 bot 上。"); }
export async function startBotTelegramBind(botId:string):Promise<{code:string;bot_username?:string|null;link?:string|null;expires_at:string}>{ return await ownerJson(botTelegram(botId,"/bind"),{method:"POST"},"生成绑定码失败。"); }
export async function unbindBotTelegram(botId:string):Promise<BotTelegramStatus>{ return await ownerJson(botTelegram(botId,"/unbind"),{method:"POST"},"解绑失败。"); }
export async function deleteBotTelegram(botId:string):Promise<BotTelegramStatus>{ return await ownerJson(botTelegram(botId),{method:"DELETE"},"删除失败。"); }
export async function listAgentConversations(offset=0,signal?:AbortSignal): Promise<{conversations:ConversationSummary[];hasMore:boolean}> {
  const url = agentChatUrl("stream", configuredApiBaseUrl).replace(/stream$/, `conversations?limit=30&offset=${offset}`);
  const response = await fetch(url,{signal,cache:"no-store",headers:{Accept:"application/json","X-Chat-Session":chatCredential()}});
  if(!response.ok) throw new ApiError("历史对话暂时无法加载。",response.status);
  const data=await response.json() as {conversations?:unknown;has_more?:unknown};
  return {conversations:Array.isArray(data.conversations)?data.conversations as unknown as ConversationSummary[]:[],hasMore:data.has_more===true};
}
export async function restoreAgentChat(selectedId?: string | null, signal?: AbortSignal, requireExisting=false, before?: number | null, limit=20): Promise<{ conversationId: string | null; createdAt:string|null; messages: AgentChatMessage[]; hasMore:boolean; nextBefore:number|null }> {
  const conversationId = selectedId ?? conversationForMode(window.localStorage) ?? window.sessionStorage.getItem(chatConversationKey);
  if (!conversationId) return { conversationId: null, createdAt:null, messages: [], hasMore:false, nextBefore:null };
  const params = new URLSearchParams({ limit: String(limit) });
  if (before !== undefined && before !== null) params.set("before", String(before));
  const response = await fetch(agentChatUrl("messages", configuredApiBaseUrl).replace(/messages$/, `conversations/${encodeURIComponent(conversationId)}/messages`) + `?${params.toString()}`, {
    signal, headers: { Accept: "application/json", "X-Chat-Session": chatCredential() }, cache: "no-store",
  });
  if (response.status === 404) { if(requireExisting)throw new ApiError("无法恢复服务端聊天记录。",404); return { conversationId: null, createdAt:null, messages: [], hasMore:false, nextBefore:null }; }
  if (!response.ok) throw new ApiError("无法恢复服务端聊天记录。", response.status);
  const data = await response.json() as { messages?: AgentChatMessage[]; has_more?:unknown; next_before?:unknown };
  return { conversationId, createdAt:conversationCreatedAtForMode(window.localStorage), messages: Array.isArray(data.messages) ? data.messages : [],
    hasMore: data.has_more === true, nextBefore: typeof data.next_before === "number" ? data.next_before : null };
}
export function clearAgentConversation(): void { selectConversationForMode(window.localStorage,window.sessionStorage,null); }
export function selectAgentConversation(id:string|null,createdAt?:string|null):void { selectConversationForMode(window.localStorage,window.sessionStorage,id,undefined,createdAt); }
export async function agentChatStream(config: AgentChatConfig | { bot_id: string } | { catalog_model: string }, message: string, conversationId: string | null, signal: AbortSignal, onEvent: (event: AgentChatEvent) => boolean | void, pendingRequestId?:string, displayMessage?:string): Promise<string> {
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
      if (accepted === false) { await reader.cancel().catch(() => {}); return answer; }
      if (receivedEvent === "conversation" && typeof data.conversation_id === "string") window.sessionStorage.setItem(chatConversationKey, data.conversation_id);
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
  options: { sourceType?: MaterialSourceType | "all"; analysisStatus?: string; reviewStatus?: string; order?: "published" | "recent"; offset?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<MaterialLibraryResponse> {
  // An empty symbol is 全部股票: the library lists every stock's materials.
  const params = new URLSearchParams({ limit: String(options.limit ?? 10), offset: String(options.offset ?? 0) });
  if (symbol) params.set("symbol", symbol);
  if (options.order && options.order !== "published") params.set("order", options.order);
  if (options.sourceType && options.sourceType !== "all") params.set("source_type", options.sourceType);
  if (options.analysisStatus && options.analysisStatus !== "all") params.set("analysis_status", options.analysisStatus);
  if (options.reviewStatus && options.reviewStatus !== "all") params.set("review_status", options.reviewStatus);
  return requestJson<MaterialLibraryResponse>(`/api/v3/materials?${params}`, { signal: options.signal });
}

/** A server catalog entry for one analysis (UX batch 3); the provider form is kept for older callers. */
export type MaterialAnalysisModelOverride = { catalog_model: string } | {
  provider: AgentChatProvider;
  base_url?: string;
  model: string;
  api_key: string;
};
export type MaterialAnalysisSettings = {
  source: "environment" | "settings";
  provider: AgentChatProvider;
  base_url: string | null;
  model: string;
  has_api_key: boolean;
  api_key_last4: string | null;
};

export async function createMaterialAnalysisJob(
  sourceType: MaterialSourceType,
  sourceId: string,
  force = false,
  model?: MaterialAnalysisModelOverride,
): Promise<MaterialAnalysisJob> {
  const idempotencyKey = newIdempotencyKey();
  const body = {
    idempotency_key: idempotencyKey,
    force,
    ...(model ? "catalog_model" in model ? { catalog_model: model.catalog_model } : { provider: model.provider, base_url: model.base_url, model: model.model, api_key: model.api_key } : {}),
  };
  return requestJson<MaterialAnalysisJob>(`/api/v3/materials/${sourceType}/${encodeURIComponent(sourceId)}/analysis-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  });
}

export async function getMaterialAnalysisSettings(signal?: AbortSignal): Promise<MaterialAnalysisSettings> {
  return requestJson<MaterialAnalysisSettings>("/api/v3/materials/analysis-settings", { signal });
}

export async function saveMaterialAnalysisSettings(
  settings: { provider: AgentChatProvider; base_url?: string; model: string; api_key?: string } | { catalog_model: string },
  signal?: AbortSignal,
): Promise<MaterialAnalysisSettings> {
  return requestJson<MaterialAnalysisSettings>("/api/v3/materials/analysis-settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
    signal,
  });
}

export async function resetMaterialAnalysisSettings(signal?: AbortSignal): Promise<MaterialAnalysisSettings> {
  return requestJson<MaterialAnalysisSettings>("/api/v3/materials/analysis-settings", { method: "DELETE", signal });
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

export type V2RevisionCandidate = {
  source_type: "official_filing" | "uploaded_media";
  source_id: string;
  title: string;
  kind: string;
  published_at: string;
  observed_at: string;
  category: "new" | "backfilled" | "preexisting" | "updated" | "reanalyzed";
  badge: string | null;
};

export type V2RevisionCandidates = {
  version_id: string;
  version_no: number;
  symbol: string;
  decision_at: string;
  window_start: string;
  window_end: string;
  target_end_date: string | null;
  expired: boolean;
  candidates: V2RevisionCandidate[];
  excluded: Array<{
    source_type: "official_filing" | "uploaded_media";
    source_id: string;
    title: string;
    kind: string;
    published_at: string;
    reason: string;
    reason_text: string | null;
  }>;
};

export async function getV2RevisionCandidates(versionId: string, signal?: AbortSignal): Promise<V2RevisionCandidates> {
  return requestJson<V2RevisionCandidates>(`/api/v2/forecast-versions/${encodeURIComponent(versionId)}/revision-candidates`, { signal });
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

// Bots (personal agent, second edition).  A bot's turn sends only bot_id: the server picks the model from its
// catalog, so no key leaves the browser.  Catalog entries say configured / missing, never the key.
export type BotFocus = { companies: Array<{ symbol: string; name?: string | null }>; theme: string | null };
export type Bot = { id: string; name: string; title: string; description: string; focus: BotFocus; tool_scope: string[] | null; model: string | null;
  skills: string[]; avatar: string; group: string; pinned: boolean; hidden: boolean; is_default: boolean; template: string; conversation_id: string | null;
  daily_notify_cap: number; default_company: { symbol: string; company: string | null } | null; last_message: string | null; last_at: string | null; unread: number; pending: number;
  created_at?: string | null; updated_at?: string | null };
export type BotTemplate = { id: string; label: string; name: string; title: string; description: string; tool_scope: string[] | null };
export type BotTool = { name: string; level: string; owner_only: boolean };
export type BotFields = Partial<Pick<Bot, "name" | "title" | "description" | "focus" | "tool_scope" | "model" | "avatar" | "group" | "pinned" | "hidden" | "template" | "daily_notify_cap" | "skills">>;
export type CatalogModel = { name: string; display_name: string; provider: string; model: string; base_url: string | null; origin: "file" | "server";
  key_source: "vault" | "env"; key_env: string | null; key_status: "configured" | "missing"; is_default: boolean; migrated: boolean };
export type ModelCatalog = { models: CatalogModel[]; default: string | null; config_file: { present: boolean; problems: string[] } };
const BOT_ERRORS:Record<string,string>={invalid_name:"名字无效（1 到 40 字）。",name_taken:"已有同名 bot。",invalid_title:"头衔最多 60 字。",invalid_description:"说明最多 4000 字。",
  invalid_focus:"关注范围无效。",invalid_focus_symbol:"股票代码无效，例如 AAPL 或 BRK.B。",too_many_companies:"关注公司最多 20 家。",invalid_tool_scope:"工具范围无效。",
  unknown_tool:"工具范围里有不存在的工具。",unknown_model:"模型目录里没有这个模型。",invalid_avatar:"头像最多 16 个字符。",invalid_group:"分组名最多 40 字。",
  default_bot_not_hidden:"默认 bot 不能隐藏。",default_bot_undeletable:"默认 bot 不能删除。",invalid_notify_cap:"每日通知上限为 0 到 50。",too_many_bots:"bot 已达上限（30 个）。",
  bot_not_found:"bot 不存在或已删除。",unknown_template:"模板无效。",unknown_skill:"这个技能不存在。",model_exists:"已有同名模型。",model_not_found:"模型不存在。",model_in_use:"有 bot 正在用这个模型，先给它们换模型。",
  model_from_file:"这个模型来自服务器配置文件，只能在服务器上改。",default_from_file:"默认模型由服务器配置文件指定。",invalid_provider:"模型服务无效。",invalid_model:"Model ID 无效。",
  invalid_endpoint:"接口地址无效：须为 HTTPS，自定义服务必须填写。",invalid_key:"API key 格式不对。",invalid_key_env:"环境变量名须以 _API_KEY 结尾，且不能是服务器自己的密钥。",
  one_key_source:"API key 和环境变量只能填一个。",key_required:"换了服务或接口地址，需要重新填写 key。",credential_key_missing:"服务器没有配置加密密钥，无法保存 key。",
  too_many_models:"模型已达上限（40 个）。",invalid_agent_chat_request:"填写的内容无效。",
  model_key_missing:"服务器上没有找到这个 key：检查环境变量名，或重新填写 key。",invalid_model_request:"Model ID 或接口地址无效。",model_not_configured:"模型目录是空的：先在设置里添加一个模型。"};
async function botJson<T>(path:string,init:{method?:string;body?:unknown;signal?:AbortSignal},failure:string):Promise<T>{
  const response=await fetch(agentPath(path),{method:init.method??"GET",signal:init.signal,cache:"no-store",headers:ownerHeaders(init.body!==undefined),
    ...(init.body!==undefined?{body:JSON.stringify(init.body)}:{})});
  if(!response.ok){
    const detail=(await response.json().catch(()=>null) as {detail?:unknown}|null)?.detail;
    throw new ApiError(response.status===403||response.status===401?"登录个人聊天后才能使用 bot。":(typeof detail==="string"&&BOT_ERRORS[detail])||failure,response.status);
  }
  return await response.json() as T;
}
export async function listBots(signal?:AbortSignal):Promise<{bots:Bot[];default_bot_id:string|null}>{ return await botJson("bots",{signal},"bot 列表暂时无法读取。"); }
export async function getBotTemplates(signal?:AbortSignal):Promise<{templates:BotTemplate[];tools:BotTool[];always_on:string[]}>{ return await botJson("bot-templates",{signal},"模板暂时无法读取。"); }
export async function createBot(fields:BotFields):Promise<Bot>{ return (await botJson<{bot:Bot}>("bots",{method:"POST",body:fields},"创建失败，请重试。")).bot; }
export async function updateBot(id:string,fields:BotFields):Promise<Bot>{ return (await botJson<{bot:Bot}>(`bots/${encodeURIComponent(id)}`,{method:"PATCH",body:fields},"保存失败，请重试。")).bot; }
export async function deleteBot(id:string):Promise<void>{ await botJson(`bots/${encodeURIComponent(id)}`,{method:"DELETE"},"删除失败，请重试。"); }
export async function getModelCatalog(signal?:AbortSignal):Promise<ModelCatalog>{ return await botJson("model-catalog",{signal},"模型目录暂时无法读取。"); }
export type CatalogEntryInput = { name?: string; display_name?: string; provider?: string; model?: string; base_url?: string | null; api_key?: string; key_env?: string; is_default?: boolean };
export async function addCatalogModel(value:CatalogEntryInput):Promise<CatalogModel>{ return (await botJson<{model:CatalogModel}>("model-catalog",{method:"POST",body:value},"保存失败。")).model; }
export async function updateCatalogModel(name:string,value:CatalogEntryInput):Promise<CatalogModel>{ return (await botJson<{model:CatalogModel}>(`model-catalog/${encodeURIComponent(name)}`,{method:"PATCH",body:value},"保存失败。")).model; }
export async function deleteCatalogModel(name:string):Promise<void>{ await botJson(`model-catalog/${encodeURIComponent(name)}`,{method:"DELETE"},"删除失败。"); }
/** 测试并保存 / 服务商可用模型 of the catalog form: the typed key, an environment variable, or (``name``, same
 *  provider and endpoint) the entry's stored key.  Nothing is saved; nothing returned carries a key. */
export type CatalogProbe = { name?: string; provider: string; model?: string; base_url?: string | null; api_key?: string; key_env?: string };
export async function testCatalogModel(value:CatalogProbe,signal?:AbortSignal):Promise<void>{ await botJson("model-catalog/test",{method:"POST",body:value,signal},"连接失败，请检查服务、Model ID 和 key。"); }
export async function discoverCatalogModels(value:CatalogProbe,signal?:AbortSignal):Promise<{status:string;ordering:string;models:Array<{id:string;name:string}>}>{ return await botJson("model-catalog/models",{method:"POST",body:value,signal},"暂时无法获取服务商模型列表。"); }
export async function setDefaultCatalogModel(name:string):Promise<CatalogModel>{ return (await botJson<{model:CatalogModel}>("model-catalog/default",{method:"PUT",body:{name}},"保存失败。")).model; }

// Skills (stage E): recipes — name, one-line description, steps, allowed catalog tools, optional output template; never code.
export type Skill = { id:string; name:string; description:string; body:string; allowed_tools:string[]; output_template:string|null; version:number; enabled:boolean;
  origin:"builtin"|"mine"|"imported"; status:"active"|"draft"; builtin:boolean; updated_at:string|null };
export type SkillInput = { name:string; description:string; body:string; allowed_tools:string[]; output_template?:string|null; enabled?:boolean; save_draft?:boolean };
export type SkillTool = { name:string; level:string };
export type SkillImportPreview = { skill:{ name:string; description:string; body:string; output_template:string|null; allowed_tools:string[] }; tools:Array<{ name:string; level:string; write:boolean }> };
const SKILL_ERRORS:Record<string,string>={...TASK_ERRORS,invalid_name:"名字不能为空，最多 40 字，不能含 / @ #。",unknown_tool:"技能里有工具目录之外的工具，不能保存。",code_not_allowed:"技能只能是文字步骤，不能带代码。",
  invalid_skill_file:"文件格式不对：开头要有 name / description / tools。",name_taken:"已经有同名技能。",invalid_description:"请写一句话说明什么时候用（最多 120 字）。",invalid_body:"请写步骤（最多 6000 字）。",
  invalid_allowed_tools:"至少选一个工具。",too_many_skills:"技能太多了（最多 60 个）。",unknown_skill:"这个技能不存在。"};
async function skillJson<T>(path:string,init:{method?:string;body?:unknown;signal?:AbortSignal},failure:string):Promise<T>{
  const response=await fetch(agentPath(path),{method:init.method??"GET",signal:init.signal,cache:"no-store",headers:ownerHeaders(init.body!==undefined),
    ...(init.body!==undefined?{body:JSON.stringify(init.body)}:{})});
  if(!response.ok){
    const detail=(await response.json().catch(()=>null) as {detail?:unknown}|null)?.detail;
    throw new ApiError(response.status===403||response.status===401?"登录个人聊天后才能使用技能。":(typeof detail==="string"&&SKILL_ERRORS[detail])||(response.status===404?SKILL_ERRORS.unknown_skill:failure),response.status);
  }
  return await response.json() as T;
}
export async function listSkills(signal?:AbortSignal):Promise<{skills:Skill[];tools:SkillTool[]}>{ return await skillJson("skills",{signal},"技能暂时无法读取。"); }
export async function createSkill(input:SkillInput):Promise<Skill>{ return (await skillJson<{skill:Skill}>("skills",{method:"POST",body:input},"保存失败，请重试。")).skill; }
export async function updateSkill(id:string,input:Partial<SkillInput>):Promise<Skill>{ return (await skillJson<{skill:Skill}>(`skills/${encodeURIComponent(id)}`,{method:"PATCH",body:input},"保存失败，请重试。")).skill; }
export async function setSkillEnabled(id:string,enabled:boolean):Promise<Skill>{ return (await skillJson<{skill:Skill}>(`skills/${encodeURIComponent(id)}/enabled`,{method:"POST",body:{enabled}},"操作失败，请重试。")).skill; }
export async function copySkill(id:string):Promise<Skill>{ return (await skillJson<{skill:Skill}>(`skills/${encodeURIComponent(id)}/copy`,{method:"POST"},"复制失败，请重试。")).skill; }
export async function deleteSkill(id:string):Promise<void>{ await skillJson(`skills/${encodeURIComponent(id)}`,{method:"DELETE"},"删除失败，请重试。"); }
export async function exportSkill(id:string):Promise<string>{
  const response=await fetch(agentPath(`skills/${encodeURIComponent(id)}/export`),{cache:"no-store",headers:ownerHeaders()});
  if(!response.ok) throw new ApiError(response.status===404?SKILL_ERRORS.unknown_skill:"导出失败，请重试。",response.status);
  return await response.text();
}
export async function previewSkillImport(markdown:string):Promise<SkillImportPreview>{ return await skillJson("skills/import/preview",{method:"POST",body:{markdown}},"文件无法预览。"); }
export async function importSkill(markdown:string):Promise<Skill>{ return (await skillJson<{skill:Skill}>("skills/import",{method:"POST",body:{markdown}},"导入失败，请重试。")).skill; }
