import { dispatchAgentAction } from "./agent-client-actions.mjs";
import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { agentChatRequest, agentChatModels, agentChatStream, configuredApiBaseUrl, restoreAgentChat, clearAgentConversation, getChatMode, setChatMode, listAgentConversations, renameAgentConversation, archiveAgentConversation, selectAgentConversation, personalChatLogin, getKnowledgeCitation, type KnowledgeCitationDetail, type ConversationSummary, type AgentChatConfig, type AgentChatMessage, type AgentChatProvider, type AgentChatEvent } from "./api";
import { chatTurnPreviews, plainChatPreview } from "./chat-thread.mjs";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Tx, useLocale } from "./i18n";
import { createModelDiscovery, createModelDiscoveryScheduler } from "./model-discovery.mjs";
import { CATALOG, loadProfiles, saveProfiles, parseProfileBackup, mergeImportedProfiles, createMutationCoordinator, type ModelProfile, type ProfileDocument } from "./model-profiles.mjs";
import { AgentChatLifecycle, chatContext } from "./agentChatLifecycle.js";
import { KnowledgeAdminPage } from './KnowledgeAdminPage';
import { ContextMeter } from "./ContextMeter";
import { restoreConversationAfterPersistence, restoreConversationSafely } from "./chat-session.mjs";
import { normalizeClarification, selectedClarification, manualClarification, clarificationTitle, isActiveClarification } from "./chat-clarification.mjs";
import { toolLabel } from "./agent-tool-labels.mjs";
import { addToWatchlist, queueAddTicker, watchlistSnapshot } from "./watchlist-bridge.mjs";
import { createArchive, createSerializedWriter, isPickerCancelled, mergeConversation, serializeArchive, validateArchive, writeAtomically, type ArchiveConversation, type ChatArchive } from "./chat-archive.mjs";

type State = "idle" | "testing" | "ready" | "sending" | "restoring" | "error";
// What a confirmation card may show, in words.  The card used to print the raw field names of the
// tool arguments, so the user read "source_id: c2462639-…" and "title: analyze".
const TARGET_FIELD_LABELS:Record<string,string>={
  symbol:"公司代码",title:"材料",url:"链接",source_type:"来源类型",accession_number:"申报编号",
  form:"表单类型",days:"天数",max_tasks:"最多处理",decision_date:"决策日",kind:"类型",
  record_id:"记录",document_id:"文档",version_id:"版本",candidate_id:"候选",
  approved:"审核结果",saved_date:"知识库日期",web_date:"网页日期",source_refs:"来源",
};
const HIDDEN_TARGET_FIELDS=new Set(["source_id","record_id","document_id","version_id","candidate_id","source_refs"]);

function toolResultSummary(value:unknown):string {
  if (!value || typeof value!=="object") return "";
  const result=value as {data?:unknown;job_status?:unknown;status?:unknown;error_code?:unknown};
  if (typeof result.job_status==="string") return `任务 ${result.job_status.slice(0,32)}`;
  if (Array.isArray(result.data)) return `${result.data.length} 条结果`;
  if (typeof result.error_code==="string") return result.error_code.slice(0,48);
  return "";
}
type FileHandle = { name: string; getFile: () => Promise<File>; createWritable: () => Promise<{ write: (value: string) => Promise<void>; close: () => Promise<void> }> };
type ChatSession = { conversationId:string|null; loadingProfiles:boolean; mutationPending:boolean; profilePath:string; importProfiles:(file:File)=>Promise<void>; exportProfiles:()=>void; provider: AgentChatProvider; model: string; key: string; baseUrl: string; displayName: string; profiles: ModelProfile[]; selectedId: string | null; storage: string; storageError: string; selectProfile: (id: string) => void; saveProfile: (p: ModelProfile) => Promise<void>; verified: boolean; state: State; status: string; restoring:boolean; messages: AgentChatMessage[]; draft: string; setDraft: (v: string) => void; send: (e: FormEvent) => Promise<void>; choose:(value:string,requestId:string)=>Promise<void>; replyToCard:(value:string,requestId:string)=>Promise<void>; cancel: () => void; clear: () => void; openConversation:(id:string,created?:string)=>Promise<void>; archive: ChatArchive | null; archiveView: ArchiveConversation | null; fileName: string; archiveStatus: string; connectFile: () => Promise<void>; disconnectFile: () => void; exportArchive: () => void; importArchive: (file: File) => Promise<void>; selectArchive: (id:string) => void; returnLive: () => void };
const Context = createContext<ChatSession | null>(null);
const WRITE_CARDS = new Set(["write_confirmation", "confirm_write", "choose_write_action"]);
function Icon({ children }: { children: ReactNode }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>;
}
const CopyIcon = () => <Icon><rect x="9" y="9" width="11" height="11" rx="2.5" /><path d="M5 15V6.5A2.5 2.5 0 0 1 7.5 4H15" /></Icon>;
const CheckIcon = () => <Icon><path d="M5 12.5l4.5 4.5L19 7.5" /></Icon>;
const ThumbUpIcon = () => <Icon><path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3z" /><path d="M7 11l4-7a2 2 0 0 1 2 2v3h5.2a2 2 0 0 1 2 2.3l-1.1 7A2 2 0 0 1 17.1 20H7" /></Icon>;
const ThumbDownIcon = () => <Icon><path d="M17 13V4h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-3z" /><path d="M17 13l-4 7a2 2 0 0 1-2-2v-3H5.8a2 2 0 0 1-2-2.3l1.1-7A2 2 0 0 1 6.9 4H17" /></Icon>;
const PencilIcon = () => <Icon><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3z" /><path d="M13.5 6.5l3 3" /></Icon>;
const ArchiveIcon = () => <Icon><path d="M4 5h16v3H4V5z" /><path d="M5.5 8v10.5a1.5 1.5 0 0 0 1.5 1.5h10a1.5 1.5 0 0 0 1.5-1.5V8" /><path d="M9.5 12h5" /></Icon>;
const SendIcon = () => <Icon><path d="M12 19V5" /><path d="M5.5 11.5L12 5l6.5 6.5" /></Icon>;

function ClarificationCard({clarification,active,onChoose,onReply}:{clarification:NonNullable<AgentChatMessage["clarification"]>;active:boolean;onChoose:(value:string)=>Promise<void>;onReply:(value:string)=>Promise<void>}) {
  const { t } = useLocale();
  const [reply,setReply]=useState("");
  const write=WRITE_CARDS.has(clarification.reason), confirm=["write_confirmation","confirm_write"].includes(clarification.reason);
  const title=t(clarificationTitle(clarification));
  return <div className={`agent-clarification-card${write?" is-write":""}${active?"":" is-locked"}`} role="group" aria-label={title}>
    <strong>{title}</strong>
    {clarification.display?.agent_suggestion&&<p>{t("这是 agent 的建议，不是你的要求。批准后才会继续。")}</p>}
    {clarification.question&&<p>{clarification.question}</p>}
    {clarification.display&&<div className="agent-confirmation-details">
      <p><strong>{clarification.display.title}</strong></p>
      {clarification.display.saved_date&&<p>{t("知识库日期")}：{clarification.display.saved_date}</p>}
      {clarification.display.web_date&&<p>{t("网页日期")}：{clarification.display.web_date}</p>}
      {clarification.display.url&&<p><a href={clarification.display.url} target="_blank" rel="noopener noreferrer">{clarification.display.url}</a></p>}
      {clarification.display.targets?.map((target,index)=><p key={index}>{Object.entries(target).filter(([key])=>!HIDDEN_TARGET_FIELDS.has(key)).map(([key,value])=>`${t(TARGET_FIELD_LABELS[key]??key)}：${value}`).join(" · ")}</p>)}
      {clarification.display.alternative&&<p>{clarification.display.alternative}</p>}
      {write&&<small>{t("确认有效期为十分钟；改变目标需要重新确认。")}</small>}
    </div>}
    <div className="agent-clarification-options">{clarification.choices.map((choice,index)=>{
      const cancel=/^cancel:/.test(choice.value);
      const kind=write&&confirm&&index===0?"is-primary":write&&(cancel||(confirm&&index===clarification.choices.length-1))?"is-quiet":"";
      return <button type="button" className={kind} key={choice.value} disabled={!active} onClick={()=>void onChoose(choice.value)}>{t(choice.label)}</button>;
    })}</div>
    {!write&&<form className="agent-clarification-reply" onSubmit={event=>{event.preventDefault();if(active&&reply.trim())void onReply(reply)}}>
      <label>{t("手动输入")}<input value={reply} onChange={event=>setReply(event.target.value)} maxLength={4000} disabled={!active} placeholder={t("也可以输入自己的回答")} /></label>
      <button type="submit" disabled={!active||!reply.trim()}>{t("发送")}</button>
    </form>}
  </div>;
}

function shortDate(value:string, locale:string):string {
  const date=new Date(value); if(Number.isNaN(date.valueOf())) return "";
  const today=new Date(), same=date.toDateString()===today.toDateString();
  return same?date.toLocaleTimeString(locale,{hour:"2-digit",minute:"2-digit"}):date.toLocaleDateString(locale,{month:"numeric",day:"numeric"});
}

function HistoryRow({item,current,onOpen,onRename,onArchive}:{item:ConversationSummary;current:boolean;onOpen:()=>void;onRename:(title:string)=>Promise<void>;onArchive:()=>Promise<void>}) {
  const { t, locale } = useLocale();
  const [editing,setEditing]=useState(false),[value,setValue]=useState(item.title),[error,setError]=useState(""),[saving,setSaving]=useState(false);
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{if(editing){input.current?.focus();input.current?.select()}},[editing]);
  async function commit(){
    if(saving)return;
    const next=value.replace(/\s+/g," ").trim();
    if(next===item.title.trim()){setEditing(false);setError("");return}
    setSaving(true);setError("");
    try{await onRename(next);setEditing(false)}
    catch(e){setError(e instanceof Error?e.message:t("重命名失败，请重试"))}
    finally{setSaving(false)}
  }
  if(editing)return <div className="agent-history-row is-current"><div className="agent-history-edit">
    <input ref={input} value={value} maxLength={80} disabled={saving} aria-label={t("对话名称")} placeholder={t("对话名称（留空恢复默认）")}
      onChange={e=>setValue(e.target.value)} onBlur={()=>void commit()}
      onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();void commit()}else if(e.key==="Escape"){e.preventDefault();setValue(item.title);setError("");setEditing(false)}}} />
    <small className={error?"is-error":""}>{error||t("Enter 保存，Esc 取消")}</small>
  </div></div>;
  return <div className={`agent-history-row${current?" is-current":""}`}>
    <button type="button" className="agent-history-item" aria-current={current?"true":undefined} title={item.title} onClick={onOpen} onDoubleClick={()=>{setValue(item.title);setEditing(true)}}>
      <span className="agent-history-title">{item.title||t("新建对话")}</span>
      <span className="agent-history-time">{shortDate(item.updated_at,locale)}</span>
    </button>
    <span className="agent-history-actions">
      <button type="button" className="agent-history-rename" aria-label={t("重命名对话")} title={t("重命名")} onClick={()=>{setValue(item.title);setEditing(true)}}><span style={{display:"inline-flex",width:14,height:14}}><PencilIcon /></span></button>
      <button type="button" className="agent-history-archive" aria-label={t("归档对话")} title={t("归档（仅从列表移除，不删除内容）")} onClick={()=>{void onArchive()}}><span style={{display:"inline-flex",width:14,height:14}}><ArchiveIcon /></span></button>
    </span>
  </div>;
}
export function AgentChatProviderRoot({ children }: { children: ReactNode }) {
  const { t } = useLocale();
  const [profiles,setProfiles]=useState<ModelProfile[]>([]),[selectedId,setSelectedId]=useState<string|null>(null),[storage,setStorage]=useState(""),[storageError,setStorageError]=useState(""),[loadingProfiles,setLoadingProfiles]=useState(true),[mutationPending,setMutationPending]=useState(false),[profilePath,setProfilePath]=useState("");
  const active=profiles.find(p=>p.id===selectedId); const provider=active?.provider??"deepseek", model=active?.model??"", key=active?.api_key??"", baseUrl=active?.base_url??"";
  const [verified, setVerified] = useState(false), [state, setState] = useState<State>("idle"), [status, setStatus] = useState(""), [messages, setMessages] = useState<AgentChatMessage[]>([]), [draft, setDraft] = useState(""), [conversationId, setConversationId] = useState<string | null>(null);
  const [restoring,setRestoring]=useState(false);
  const [archive,setArchive]=useState<ChatArchive|null>(null),[archiveView,setArchiveView]=useState<ArchiveConversation|null>(null),[fileName,setFileName]=useState(""),[archiveStatus,setArchiveStatus]=useState(""),[importedReadOnly,setImportedReadOnly]=useState(false);
  const connectedArchive=useRef<ChatArchive|null>(null), archiveRef=useRef<ChatArchive|null>(null), createdAt=useRef(new Date().toISOString()), epoch=useRef(0);
  const handle=useRef<FileHandle|null>(null), localId=useRef(readLocalConversationId()), writer=useRef(createSerializedWriter(async (target:FileHandle,data:string)=>{if(!target)throw new Error("未连接归档文件");await writeAtomically(target,data);}));
  const lifecycle = useRef(new AgentChatLifecycle());
  useEffect(() => () => lifecycle.current.invalidate(), []);
  const profileMode=getChatMode();
  useEffect(()=>{void loadProfiles(globalThis,configuredApiBaseUrl,profileMode).then(({data,backend,path})=>{profilesRef.current=data.profiles;setProfiles(data.profiles);setSelectedId(data.selected_id);setStorage(backend);setProfilePath(path??"");setLoadingProfiles(false);const active=data.profiles.find(p=>p.id===data.selected_id);setVerified(Boolean(active?.api_key&&active.tested_at));}).catch(e=>{setStorageError(e instanceof Error?e.message:"本地配置读取失败");setLoadingProfiles(false);});},[]);
  useEffect(() => { const controller=new AbortController(), id=lifecycle.current.begin(controller); setRestoring(true); setState("restoring"); void restoreConversationSafely(lifecycle.current,id,controller.signal,signal=>restoreAgentChat(undefined,signal),saved=>{if(!saved.conversationId)clearAgentConversation();else{localId.current=saved.conversationId;createdAt.current=saved.createdAt??new Date().toISOString();try{window.localStorage.setItem(`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,localId.current)}catch{}}setConversationId(saved.conversationId);setMessages(saved.messages.filter(m=>m.role==="user"||m.role==="assistant"));setState("idle")}).catch(() => { if (lifecycle.current.isCurrent(id)) { setStatus(t("历史记录暂时无法加载；可稍后重试。"));setState("idle"); } }).finally(()=>{if(lifecycle.current.isCurrent(id))setRestoring(false)}); return () => { controller.abort(); lifecycle.current.finish(id); }; }, []);
  const latestMessages=useRef(messages);latestMessages.current=messages;
  useEffect(()=>{if(!handle.current||(state!=="ready"&&state!=="error")||archiveView||importedReadOnly||messages.length===0)return;const timer=window.setTimeout(()=>void persistConversation(messages),700);return()=>window.clearTimeout(timer);},[messages,state,archiveView,importedReadOnly]);
  async function persistConversation(items:AgentChatMessage[]){const target=handle.current;if(!target||items.length===0)return;try{const now=new Date().toISOString(),conversation:ArchiveConversation={id:localId.current,created_at:createdAt.current,updated_at:now,messages:archiveMessages(items)};const merged=mergeConversation(connectedArchive.current??createArchive(),conversation);connectedArchive.current=merged;const version=epoch.current;await writer.current(target,serializeArchive(merged));if(version===epoch.current&&!importedReadOnly){archiveRef.current=merged;setArchive(merged);setArchiveStatus(`${t("上次成功保存时间")}: ${new Date().toLocaleTimeString()}`);}}catch(e){setArchiveStatus(`${t("自动保存失败")}: ${e instanceof Error?e.message:""}`);}}
  async function connectFile(){try{const w=window as Window&{showSaveFilePicker?: (options:unknown)=>Promise<FileHandle>};if(!w.showSaveFilePicker){setArchiveStatus(t("当前浏览器不支持自动写入文件；请使用导出和导入。"));return;}const target=await w.showSaveFilePicker({suggestedName:"market-evidence-chat.json",types:[{description:"JSON chat archive",accept:{"application/json":[".json"]}}]});const file=await target.getFile();if(file.size>5_000_000)throw Error("归档文件超过 5 MB 限制");const loaded=file.size?validateArchive(await file.text()):createArchive();epoch.current++;handle.current=target;connectedArchive.current=loaded;archiveRef.current=loaded;setImportedReadOnly(false);setArchiveView(null);setArchive(loaded);setFileName(target.name);setArchiveStatus(t("本地归档已连接"));if(latestMessages.current.length)await persistConversation(latestMessages.current);}catch(e){if(isPickerCancelled(e))return;setArchiveStatus(`${t("无法连接归档文件")}: ${e instanceof Error?e.message:""}`);}}
  function disconnectFile(){epoch.current++;handle.current=null;connectedArchive.current=null;setFileName("");setArchiveStatus(t("已断开本地归档"));}
  function exportArchive(){const now=new Date().toISOString();let data=archive??createArchive();if(!archiveView&&messages.length){data=mergeConversation(data,{id:localId.current,created_at:now,updated_at:now,messages:archiveMessages(messages)});}const blob=new Blob([serializeArchive(data)],{type:"application/json;charset=utf-8"});const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="market-evidence-chat.json";a.click();URL.revokeObjectURL(url);}
  async function importArchive(file:File){try{if(file.size>5_000_000)throw new Error("归档文件超过 5 MB 限制");const loaded=validateArchive(await file.text());epoch.current++;setImportedReadOnly(true);setArchive(loaded);setArchiveView(loaded.conversations.at(-1)??null);setArchiveStatus(t("归档已打开，只读查看"));}catch(e){setArchiveStatus(e instanceof Error?e.message:t("归档文件无效"));}}
  function returnLive(){setArchiveView(null);if(importedReadOnly){setImportedReadOnly(false);setArchive(archiveRef.current);}setArchiveStatus(t("已返回当前对话"));}
  function selectArchive(id:string){const item=archive?.conversations.find(x=>x.id===id);if(item)setArchiveView(item);}
  const markSendingTurnCancelled = () => setMessages(items => {
    const last = items.at(-1);
    if (last?.role === "assistant") return [...items.slice(0, -1), { ...last, status: "cancelled", tool_trace: last.tool_trace?.map(x => x.status === "running" ? { ...x, status: "cancelled" } : x) }];
    if (last?.role === "user") return [...items, { role: "assistant", content: "", status: "cancelled" }];
    return items;
  });
  const invalidateSettings = () => { const wasSending = state === "sending"; lifecycle.current.invalidate(); if (wasSending) markSendingTurnCancelled(); setVerified(false); setState("idle"); setStatus(""); };
  const config: AgentChatConfig = { provider, base_url: baseUrl, model: model.trim(), api_key: key };
  const profileMutation=useRef(createMutationCoordinator((pending:boolean)=>setMutationPending(pending)));
  const profilesRef=useRef(profiles);profilesRef.current=profiles;
  function enqueueProfileMutation<T>(work:()=>Promise<T>):Promise<T>{if(loadingProfiles||state==="testing"||state==="sending")return Promise.reject(Error("请等待当前操作完成。"));return profileMutation.current.enqueue(work);}
  async function saveProfile(profile:ModelProfile){return enqueueProfileMutation(async()=>{const next=[...profilesRef.current.filter(p=>p.id!==profile.id),profile];const doc:ProfileDocument={version:1,profiles:next,selected_id:profile.id};const backend=await saveProfiles(doc,globalThis,storage,configuredApiBaseUrl,profileMode);profilesRef.current=next;setProfiles(next);setSelectedId(profile.id);setStorage(backend);setStorageError("");setVerified(true);setState("ready");});}
  async function selectProfile(id:string){return enqueueProfileMutation(async()=>{const p=profilesRef.current.find(x=>x.id===id);if(!p)return;const doc:ProfileDocument={version:1,profiles:profilesRef.current,selected_id:id};await saveProfiles(doc,globalThis,storage,configuredApiBaseUrl,profileMode);invalidateSettings();setSelectedId(id);setVerified(Boolean(p.api_key&&p.tested_at));setState(p.api_key&&p.tested_at?"ready":"idle");setStatus("");setStorageError("");});}
  async function importProfiles(file:File){return enqueueProfileMutation(async()=>{const imported=await parseProfileBackup(file);const merged=mergeImportedProfiles({version:1,profiles:profilesRef.current,selected_id:selectedId},imported);await saveProfiles(merged,globalThis,storage,configuredApiBaseUrl,profileMode);lifecycle.current.invalidate();profilesRef.current=merged.profiles;setProfiles(merged.profiles);setSelectedId(merged.selected_id);setVerified(false);setState("idle");setStatus("");setStorageError("");});}
  function exportProfiles(){if(loadingProfiles||mutationPending||state==="testing"||state==="sending")return;const data={version:1 as const,profiles:profilesRef.current.map(p=>({...p})),selected_id:selectedId};const blob=new Blob([JSON.stringify(data,null,2)+"\n"],{type:"application/json;charset=utf-8"});const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="market-evidence-model-profiles.json";a.click();URL.revokeObjectURL(url);}
  async function verifyModel(candidate: string) {
    const candidateModel = candidate.trim();
    if (!key || !candidateModel || state === "testing" || state === "sending") return;
    const tested = { ...config, model: candidateModel }, hadVerified = verified, controller = new AbortController(), id = lifecycle.current.begin(controller); setState("testing"); setStatus("");
    try { const result = await agentChatRequest("hello", tested, [], controller.signal); if (!lifecycle.current.isCurrent(id)) return; setVerified(true); setState("ready"); setStatus(translate("连接测试成功")); }
    catch (error) { if (!lifecycle.current.isCurrent(id)) return; setVerified(hadVerified); setState("error"); setStatus(error instanceof Error ? friendlyError(error.message, translate) : translate("连接失败，请重试")); }
    finally { lifecycle.current.finish(id); }
  }
  function translate(s: string) { return t(s); }
  async function send(e: FormEvent) {
    e.preventDefault();
    await sendContent(draft.trim());
  }
  async function choose(value:string,requestId:string) {
    const card=messages.at(-1)?.clarification;
    if (!card || card.request_id!==requestId) return;
    const selection=selectedClarification(card,value);
    const queued=queueAddTicker(value); if(queued) addToWatchlist(queued);
    await sendContent(selection.message,selection.pendingRequestId,card.choices.find(choice=>choice.value===value)?.label);
  }
  async function replyToCard(value:string,requestId:string) {
    const card=messages.at(-1)?.clarification;
    if (!card || card.request_id!==requestId || archiveView) return;
    const reply=manualClarification(card,value);
    await sendContent(reply.message,reply.pendingRequestId);
  }
  async function sendContent(content:string,pendingRequestId?:string,label?:string) {
    if(loadingProfiles||mutationPending)return; if (!verified || loadingProfiles || mutationPending || !content || state === "sending" || state === "testing") return;
    let context: AgentChatMessage[]; try { context = chatContext(messages, { role: "user", content }); } catch (error) { setStatus(t(error instanceof Error ? error.message : "消息过长，请缩短后重试。")); return; }
    const next = [...messages, { role: "user" as const, content: label||content }]; setMessages(next); setDraft(""); setState("sending"); setStatus(""); const tested = { ...config }, controller = new AbortController(), id = lifecycle.current.begin(controller);
    let localQueue = ""; let accumulated = ""; let turnId: string|undefined; let citations: NonNullable<AgentChatMessage["citations"]> = []; let clarification:AgentChatMessage["clarification"]; let terminalStatus="complete"; let reusedSources=false; const tools: NonNullable<AgentChatMessage["tool_trace"]> = [];
    try {
      await agentChatStream(tested, content, conversationId, controller.signal, (event: AgentChatEvent) => {
        if (!lifecycle.current.isCurrent(id)) return false;
        if (event.event === "conversation" && typeof event.data.conversation_id === "string") {if(typeof event.data.turn_id==="string")turnId=event.data.turn_id;const id=event.data.conversation_id;if(id!==conversationId){const created=new Date().toISOString();localId.current=id;createdAt.current=created;selectAgentConversation(id,created)}setConversationId(id);}
        if (event.event === "client_action") {
          const applied=dispatchAgentAction(event.data, window);
          if (applied && event.data.action === "show_queue") localQueue=`\n\n${t("研究队列")}: ${(watchlistSnapshot() ?? []).join(", ") || "—"}`;
        }
        if (event.event === "status") { const labels: Record<string,string>={resolving_reference:"正在理解问题…",recognizing_intent:"正在识别任务…",planning_tasks:"正在规划查询…",expanding_query:"正在扩展检索词…",expansion_degraded:"检索词扩展不可用，继续原查询…",reusing_sources:"正在复用已核实来源…",writing_answer:"正在组织回答…"}; setStatus(t(labels[String(event.data.status)]??"正在选择研究工具…")); }
        if (event.event === "tool_start") { tools.push({ call_id: String(event.data.call_id), name: String(event.data.name), arguments: event.data.arguments, status: "running" }); setStatus(t("正在查询研究数据…")); }
        if (event.event === "tool_result") { const item = tools.find(x => x.call_id === event.data.call_id); if (item) { item.result = event.data.result; const result = event.data.result as { ok?: boolean; data?: unknown };
          item.status = !result?.ok ? "failed" : Array.isArray(result.data) && result.data.length === 0 ? "not_found" : "complete"; } }
        if (event.event === "citations" && Array.isArray(event.data.items)) citations = event.data.items as NonNullable<AgentChatMessage["citations"]>;
        if (event.event === "clarification") clarification=normalizeClarification(event.data)??undefined;
        if (event.event === "done") {terminalStatus=String(event.data.status??"complete");reusedSources=(event.data.audit as {reused_sources?:boolean}|undefined)?.reused_sources===true;}
        if (event.event === "delta" && typeof event.data.text === "string") accumulated += event.data.text;
        if (event.event === "error") throw new Error(String(event.data.code ?? "chat_failed"));
        setMessages([...next, { role: "assistant", content: accumulated + localQueue, turn_id:turnId, tool_trace: [...tools], citations, clarification, status:terminalStatus, reused_sources:reusedSources }]);
        return true;
      },pendingRequestId,label);
      if (!lifecycle.current.isCurrent(id)) return; setMessages([...next, { role: "assistant", content: accumulated + localQueue, turn_id:turnId, tool_trace: [...tools], citations, clarification, status:terminalStatus, reused_sources:reusedSources }]); setStatus(""); setState("ready");
    }
    catch (error) { if (!lifecycle.current.isCurrent(id)) return; const stopped=error instanceof DOMException && error.name==="AbortError"; const stoppedTools=tools.map(x => x.status === "running" ? { ...x, status: "cancelled" } : x); setMessages([...next, { role:"assistant",content:accumulated,status:stopped?"cancelled":"failed",tool_trace:stoppedTools }]); setState(stopped?"ready":"error"); setStatus(stopped?translate("请求已取消；供应商可能仍会计费。"):error instanceof Error ? friendlyError(error.message, translate) : translate("发送失败，请重试")); }
    finally { lifecycle.current.finish(id); }
  }
  const cancel = () => { const wasSending = state === "sending"; lifecycle.current.invalidate(); if (wasSending) markSendingTurnCancelled(); setRestoring(false);setState(verified ? "ready" : "idle"); setStatus(translate("请求已取消；供应商可能仍会计费。")); };
  async function openConversation(id:string, created?:string){
    const previous=latestMessages.current;let lastUser=-1;for(let i=previous.length-1;i>=0;i--)if(previous[i].role==="user"){lastUser=i;break;}const completed=state==="sending"?previous.slice(0,Math.max(0,lastUser)):previous;
    const controller=new AbortController(), token=lifecycle.current.begin(controller);setRestoring(true);setState("restoring");setStatus("");
    try{await restoreConversationAfterPersistence(lifecycle.current,token,controller.signal,()=>handle.current&&completed.length?persistConversation(completed):Promise.resolve(),signal=>restoreAgentChat(id,signal,true),saved=>{const restoredId=saved.conversationId??id,chosenCreatedAt=created??saved.createdAt??new Date().toISOString();selectAgentConversation(restoredId,chosenCreatedAt);setConversationId(restoredId);const display=saved.messages.filter(m=>m.role==="user"||m.role==="assistant");setMessages(display);latestMessages.current=display;localId.current=restoredId;createdAt.current=chosenCreatedAt;try{window.localStorage.setItem(`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,restoredId)}catch{}setArchiveView(null);setImportedReadOnly(false);setDraft("");setState(verified?"ready":"idle")})}
    catch(error){if(lifecycle.current.isCurrent(token)&&!(error instanceof DOMException&&error.name==="AbortError")){setStatus(t("无法恢复服务端聊天记录。"));setState(verified?"ready":"idle");}}
    finally{if(lifecycle.current.isCurrent(token)){lifecycle.current.finish(token);setRestoring(false)}}
  }
  const clear = () => { const previous=latestMessages.current;let lastUser=-1;for(let i=previous.length-1;i>=0;i--)if(previous[i].role==="user"){lastUser=i;break;}const completed=state==="sending"?previous.slice(0,Math.max(0,lastUser)):previous;if(handle.current&&completed.length)void persistConversation(completed); lifecycle.current.invalidate();setRestoring(false);setArchiveView(null);if(importedReadOnly){setImportedReadOnly(false);setArchive(archiveRef.current);}clearAgentConversation(); setConversationId(null); setMessages([]); latestMessages.current=[]; setDraft(""); localId.current=crypto.randomUUID();createdAt.current=new Date().toISOString();try{window.localStorage.setItem(`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,localId.current);}catch{}setState(verified ? "ready" : "idle"); setStatus(""); };
  const visibleMessages=archiveView?archiveView.messages as AgentChatMessage[]:messages;
  return <Context.Provider value={{ conversationId,loadingProfiles,mutationPending,profilePath,importProfiles,exportProfiles,provider, model, key, baseUrl, displayName:active?.display_name??"",profiles,selectedId,storage,storageError,selectProfile,saveProfile,verified, state, status, restoring,messages:visibleMessages, draft, setDraft, send, choose, replyToCard, cancel, clear,openConversation,archive,archiveView,fileName,archiveStatus,connectFile,disconnectFile,exportArchive,importArchive,selectArchive,returnLive }}>{children}</Context.Provider>;
}
function readLocalConversationId(){try{const key=`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,existing=window.localStorage.getItem(key);if(existing&&/^[\w-]{1,80}$/.test(existing))return existing;const id=crypto.randomUUID();window.localStorage.setItem(key,id);return id;}catch{return crypto.randomUUID();}}
function archiveMessages(messages:AgentChatMessage[]){return messages.filter(m=>m.role==="user"||m.role==="assistant").map(m=>({role:m.role as "user"|"assistant",content:m.content,...(m.status?{status:m.status as "failed"|"cancelled"}:{}),...(m.role==="assistant"&&m.citations?{citations:m.citations}:{}),...(m.role==="assistant"&&m.tool_trace?{tool_trace:m.tool_trace.map(x=>({call_id:x.call_id,name:x.name,arguments:x.arguments,result:x.result,status:x.status as "running"|"complete"|"not_found"|"failed"|"cancelled"}))}:{})}));}
function useChat() { const value = useContext(Context); if (!value) throw new Error("Agent chat provider is missing"); return value; }

export function AgentChatPanel({ onSettings,onModeChange }: { onSettings: () => void; onModeChange:(mode:"guest"|"personal")=>void }) {
  const { t } = useLocale(), c = useChat(), transcriptRef = useRef<HTMLElement>(null);
  const turnPreviews=chatTurnPreviews(c.messages);
  const followTranscript=useRef(true);
  useEffect(()=>{const el=transcriptRef.current;if(!el)return;const update=()=>{followTranscript.current=el.scrollHeight-el.scrollTop-el.clientHeight<90};el.addEventListener("scroll",update,{passive:true});return()=>el.removeEventListener("scroll",update)},[]);
  const [history,setHistory]=useState<ConversationSummary[]>([]),[historyHasMore,setHistoryHasMore]=useState(false),[historyError,setHistoryError]=useState(""),[historyLoading,setHistoryLoading]=useState(false),[mode,setMode]=useState(getChatMode());
  const [copied,setCopied]=useState<number|null>(null),[copyStatus,setCopyStatus]=useState(""),[,setFeedbackTick]=useState(0),[citationDetails,setCitationDetails]=useState<Record<string,KnowledgeCitationDetail>>({});
  const [openCitationTurn,setOpenCitationTurn]=useState<number|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  // On a narrow screen the history drawer covers the header toggle, so Esc and a tap outside close it.
  useEffect(()=>{if(!historyOpen)return;const onKey=(e:KeyboardEvent)=>{if(e.key==="Escape")setHistoryOpen(false)};window.addEventListener("keydown",onKey);return()=>window.removeEventListener("keydown",onKey)},[historyOpen]);
  const [loginOpen,setLoginOpen]=useState(false),[loginCode,setLoginCode]=useState(""),[loginError,setLoginError]=useState("");
  const [activeTurn,setActiveTurn]=useState(0),[preview,setPreview]=useState<{index:number;left:number;top:number}|null>(null);const previewTimer=useRef<number|undefined>(undefined);
  const composerForm=useRef<HTMLFormElement>(null),draftBox=useRef<HTMLTextAreaElement>(null);
  useEffect(()=>{const el=transcriptRef.current;if(!el)return;const update=()=>{const top=el.getBoundingClientRect().top;const turns=el.querySelectorAll<HTMLElement>(".agent-message.is-user");let active=0;turns.forEach((item,i)=>{if(item.getBoundingClientRect().top<=top+100)active=i});setActiveTurn(active)};el.addEventListener("scroll",update,{passive:true});update();return()=>el.removeEventListener("scroll",update)},[c.messages]);
  // The composer grows with what is typed (up to its own cap) and shrinks back after sending.
  useLayoutEffect(()=>{const box=draftBox.current;if(!box)return;box.style.height="auto";box.style.height=`${Math.min(box.scrollHeight,220)}px`},[c.draft]);
  async function refreshHistory(){setHistoryLoading(true);setHistoryError("");try{const result=await listAgentConversations();setHistory(result.conversations);setHistoryHasMore(result.hasMore)}catch(e){setHistoryError(e instanceof Error?e.message:t("历史对话暂时无法加载。"))}finally{setHistoryLoading(false)}}
  useEffect(()=>{void refreshHistory()},[c.state,mode]);
  async function loadMoreHistory(){try{const result=await listAgentConversations(history.length);setHistory(items=>[...items,...result.conversations]);setHistoryHasMore(result.hasMore)}catch(e){setHistoryError(e instanceof Error?e.message:t("历史对话暂时无法加载。"))}}
  async function openHistory(item:ConversationSummary){await c.openConversation(item.id,item.created_at);setHistoryOpen(false)}
  async function renameHistory(item:ConversationSummary,title:string){
    const saved=await renameAgentConversation(item.id,title);
    if(saved===null)await refreshHistory();   // an empty name restores the default (the first question)
    else setHistory(items=>items.map(entry=>entry.id===item.id?{...entry,title:saved,custom_title:true}:entry));
  }
  async function archiveHistory(item:ConversationSummary){
    try{await archiveAgentConversation(item.id,true)}catch(e){setHistoryError(e instanceof Error?e.message:t("归档失败，请重试"));return}
    setHistory(rows=>rows.filter(row=>row.id!==item.id));
    // 归档的是当前打开的对话时，像"新建对话"一样清空，避免继续往已归档的对话里写
    if(item.id===currentConversation)c.clear();
  }
  function switchMode(){if(mode==="personal"){setChatMode("guest");setMode("guest");onModeChange("guest");window.location.hash="#agent"}else{setLoginOpen(true);setLoginError("")}}
  async function submitPersonalLogin(){try{const credential=await personalChatLogin(loginCode);setChatMode("personal",credential);setLoginCode("");setMode("personal");onModeChange("personal");setLoginOpen(false)}catch{setLoginError(t("个人登录失败，请稍后重试。"))}}
  async function copyAnswer(text:string,index:number){setCopied(null);setCopyStatus(t("正在复制…"));let timer:number|undefined;try{const clipboard=navigator.clipboard?.writeText(text);if(!clipboard)throw Error("clipboard_unavailable");await Promise.race([clipboard,new Promise<never>((_,reject)=>{timer=window.setTimeout(()=>reject(Error("clipboard_timeout")),1800)})]);setCopied(index);setCopyStatus(t("已复制"));}catch{setCopied(-index-1);setCopyStatus(t("复制失败；请选中回答后手动复制。"));const article=document.getElementById(`chat-message-${index}`);const range=document.createRange();if(article){range.selectNodeContents(article.querySelector(".markdown-body")??article);window.getSelection()?.removeAllRanges();window.getSelection()?.addRange(range);}}finally{if(timer)window.clearTimeout(timer);window.setTimeout(()=>{setCopied(null);setCopyStatus("")},3500)}}
  async function showCitation(chunkId:string, unavailable:boolean){if(unavailable)return;try{const value=await getKnowledgeCitation(chunkId);setCitationDetails(x=>({...x,[chunkId]:value}))}catch{setCitationDetails(x=>({...x,[chunkId]:{title:t("来源已撤回或不可访问"),section:"",excerpt:"",source_url:null,observed_at:null,coverage:"unavailable",start_offset:0,end_offset:0}}))}}
  function feedbackOf(turnId:string):string{try{return localStorage.getItem(`market-evidence-agent:feedback:${turnId}`)??""}catch{return ""}}
  function giveFeedback(turnId:string,value:"up"|"down"){const key=`market-evidence-agent:feedback:${turnId}`;try{if(localStorage.getItem(key)===value)localStorage.removeItem(key);else localStorage.setItem(key,value)}catch{}setFeedbackTick(x=>x+1)}
  function onComposerKey(event:ReactKeyboardEvent<HTMLTextAreaElement>){
    // Enter sends, Shift+Enter breaks the line; a key that confirms an IME composition is left alone.
    if(event.key!=="Enter"||event.shiftKey||event.nativeEvent.isComposing||event.keyCode===229)return;
    event.preventDefault();
    if(c.draft.trim())composerForm.current?.requestSubmit();
  }
  useEffect(() => {
    const transcript = transcriptRef.current;
    if (transcript&&followTranscript.current) transcript.scrollTop = transcript.scrollHeight;
  }, [c.messages, c.state]);
  const lastIndex=c.messages.length-1;
  const sendDisabled=Boolean(c.archiveView)||c.restoring||!c.verified||!c.draft.trim()||c.state==="sending"||c.state==="testing"||c.loadingProfiles||c.mutationPending;
  const busy=c.state==="sending"||c.state==="testing"||c.restoring||c.loadingProfiles||c.mutationPending;
  const currentConversation=window.sessionStorage.getItem("market-evidence-agent:chat-conversation:v1");
  return <main className="workspace agent-page" aria-labelledby="agent-chat-title">
    <header className="workspace-heading agent-page-heading">
      <div className="agent-title-row"><button type="button" className="mobile-history-toggle secondary-action" aria-expanded={historyOpen} onClick={()=>setHistoryOpen(x=>!x)}>{t("历史对话")}</button><h1 id="agent-chat-title">模型对话</h1></div>
      <div className="agent-header-actions"><span>{mode==="personal"?t("个人模式"):t("访客模式")}</span><button className="secondary-action" onClick={switchMode} type="button">{t("切换模式")}</button><button className="secondary-action" onClick={onSettings} type="button">{t("设置")}</button></div>
    </header>
    {loginOpen&&<div className="personal-login-panel"><label>{t("个人聊天登录码")}<input type="password" value={loginCode} onChange={e=>setLoginCode(e.target.value)} autoComplete="current-password" maxLength={256}/></label><button className="primary-action" onClick={()=>void submitPersonalLogin()} disabled={!loginCode}>{t("登录个人聊天")}</button><button className="secondary-action" onClick={()=>setLoginOpen(false)}>{t("取消")}</button>{loginError&&<p role="alert">{loginError}</p>}</div>}
    <div className="agent-layout">
      {historyOpen&&<div className="agent-history-scrim" aria-hidden="true" onClick={()=>setHistoryOpen(false)}/>}
      <aside className={`agent-history${historyOpen?" is-open":""}`} aria-label={t("历史对话")}>
        <button className="primary-action" type="button" onClick={()=>{c.clear();setHistoryOpen(false)}}>＋ {t("新建对话")}</button>
        <h2>{t("历史对话")}</h2>
        {historyLoading&&<p role="status">{t("正在读取…")}</p>}
        {historyError&&<p role="alert">{historyError} <button className="secondary-action" onClick={()=>void refreshHistory()}>{t("重试")}</button></p>}
        {!historyLoading&&!historyError&&!history.length&&<p>{t("还没有保存的对话")}</p>}
        {history.map(item=><HistoryRow key={item.id} item={item} current={Boolean(c.messages.length)&&item.id===currentConversation} onOpen={()=>void openHistory(item)} onRename={title=>renameHistory(item,title)} onArchive={()=>archiveHistory(item)} />)}
        {historyHasMore&&<button className="secondary-action" onClick={()=>void loadMoreHistory()}>{t("加载更多")}</button>}
      </aside>
      <div className="agent-main-column">
        {c.archiveView&&<div className="chat-archive-banner"><span>{t("本地归档只读查看")}</span><button className="secondary-action" onClick={c.returnLive}>{t("返回当前对话")}</button><button className="secondary-action" onClick={()=>{c.returnLive();c.clear()}}>{t("新建对话")}</button></div>}
        <div className="agent-capability"><span aria-hidden="true">✳</span><p>{t("消息和研究工具轨迹会保存到服务端；工具按权限读取资料并执行明确请求的系统任务。")}</p><span className={`agent-connection ${c.verified ? "is-ready" : ""}`}>{c.verified ? `${c.provider} · ${c.model}` : t("尚未验证模型")}</span></div>
        <section ref={transcriptRef} className="agent-transcript" aria-live="polite">
          {c.restoring&&<p role="status">{t("正在恢复对话…")}</p>}
          {copyStatus&&<p className="copy-status" role="status">{copyStatus}</p>}
          {c.messages.length === 0 ? <div className="agent-welcome"><h2>{t("从一个问题开始")}</h2><p>{t(c.verified ? "连接已就绪，可以输入问题或选择下方建议。" : "请先在模型设置中填写自己的 API key 并完成连接测试。")}</p><div>{["查看 AAPL 最近行情", "AAPL 已保存的最新财报有哪些", "可信度星级是什么"].map(x => <button type="button" key={x} onClick={() => c.setDraft(t(x))}>{t(x)} <span aria-hidden="true">↗</span></button>)}</div></div> :
          <div className="agent-thread-grid">
            <nav className="agent-question-rail" aria-label={t("问题导航")}>{turnPreviews.map((turn,i)=><button key={turn.userIndex} type="button" aria-label={`${t("问题导航")}: ${turn.question}`} aria-current={i===activeTurn?"location":undefined} onClick={()=>document.getElementById(`chat-message-${turn.userIndex}`)?.scrollIntoView({behavior:"smooth",block:"start"})} onFocus={e=>{const r=e.currentTarget.getBoundingClientRect();setPreview({index:i,left:r.right+10,top:r.top})}} onBlur={()=>setPreview(null)} onPointerEnter={e=>{if(previewTimer.current)window.clearTimeout(previewTimer.current);const r=e.currentTarget.getBoundingClientRect();setPreview({index:i,left:r.right+10,top:r.top})}} onPointerLeave={()=>{previewTimer.current=window.setTimeout(()=>setPreview(null),140)}} />)}</nav>
            <div className="agent-thread">{c.messages.map((m, i) => <article data-turn-index={m.role==="user"?turnPreviews.findIndex(turn=>turn.userIndex===i):undefined} id={`chat-message-${i}`} className={`agent-message is-${m.role}${i===lastIndex?" is-last":""}`} key={i}>
              {m.role !== "user"&&<div className="agent-tool-badges">{m.reused_sources&&<span className="tool-badge status-complete">{t("复用已核实来源")}</span>}{m.tool_trace?.map((tool,j)=><span key={`${tool.name}-${j}`} className={`tool-badge status-${tool.status}`}>{t(toolLabel(tool.name))} · {t(tool.status === "complete" ? "已完成" : tool.status === "not_found" ? "没有匹配记录" : tool.status === "failed" ? "查询失败" : tool.status === "cancelled" ? "已取消" : tool.status === "submitted" ? "已提交" : "执行中")}{toolResultSummary(tool.result)&&` · ${toolResultSummary(tool.result)}`}</span>)}</div>}
              {m.role==="user"?<div className="agent-user-bubble"><p>{m.content}</p><button type="button" className="agent-user-copy" aria-label={copied===i?t("已复制"):copied===-i-1?t("复制失败"):t("复制问题")} title={t("复制问题")} onClick={()=>void copyAnswer(m.content,i)}>{copied===i?<CheckIcon />:<CopyIcon />}</button></div>:<div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{table:({node,...props})=><div className="markdown-table-wrap"><table {...props}/></div>,a:({node,...props})=>{const href=String(props.href??"");const match=href.match(/^#citation-(\d+)$/);const citation=match?m.citations?.find(item=>(item.ordinal??m.citations!.indexOf(item)+1)===Number(match[1])):undefined;if(match&&!citation)return <span className="citation-unmatched">{props.children}</span>;return <a {...props} href={citation?href:props.href} onClick={event=>{if(!citation)return;event.preventDefault();setOpenCitationTurn(i);["web","web_news","official_filing","saved_filing","saved_ai_analysis"].includes(citation.source_type)&&citation.source_url?window.open(citation.source_url,"_blank","noopener,noreferrer"):citation.chunk_id?void showCitation(citation.chunk_id,citation.source_type==="unavailable"):undefined}} target={citation?undefined:"_blank"} rel={citation?undefined:"noopener noreferrer"}/>} }}>{m.content.replace(/\[(\d+)\]/g,"[$1](#citation-$1)")}</ReactMarkdown></div>}
              {m.role==="assistant"&&m.clarification&&<ClarificationCard clarification={m.clarification} active={isActiveClarification(c.messages,i,Boolean(c.archiveView))&&!c.restoring&&c.verified&&c.state!=="sending"&&c.state!=="testing"} onChoose={value=>c.choose(value,m.clarification!.request_id)} onReply={value=>c.replyToCard(value,m.clarification!.request_id)} />}
              {m.role === "assistant" && m.status && m.status !== "complete" && m.status !== "succeeded" && <small>{t(m.status === "cancelled" ? "本轮已取消，未生成完整回答。" : m.status === "waiting_for_input" ? "等待选择后继续" : m.status === "running" ? "任务运行中，可稍后查询状态" : m.status === "partially_succeeded" ? "部分来源可用；缺项已标出" : "本轮失败；已保留已生成内容。")}</small>}
              {m.role === "assistant"&&m.content&&<>
                <div className="agent-answer-actions">
                  <button type="button" className="agent-action-button" aria-label={copied===i?t("已复制"):copied===-i-1?t("复制失败"):t("复制回答")} title={t("复制回答")} onClick={()=>void copyAnswer(m.content,i)}>{copied===i?<CheckIcon />:<CopyIcon />}</button>
                  {m.turn_id&&(["up","down"] as const).map(value=>{const saved=feedbackOf(m.turn_id!);return <button type="button" key={value} className="agent-action-button" aria-pressed={saved===value} aria-label={value==="up"?t("有帮助"):t("没有帮助")} title={value==="up"?t("有帮助"):t("没有帮助")} onClick={()=>giveFeedback(m.turn_id!,value)}>{value==="up"?<ThumbUpIcon />:<ThumbDownIcon />}</button>})}
                  {copied===i&&<span className="agent-action-note" role="status">{t("已复制")}</span>}
                </div>
                {Boolean(m.citations?.length)&&<details className="citation-drawer" open={openCitationTurn===i}><summary>{t("引用来源")} · {m.citations!.length}</summary>{m.citations!.map((citation,j)=><div className="citation-item" key={citation.chunk_id??citation.source_url??citation.ordinal}><button type="button" onClick={()=>['web','web_news','official_filing','saved_filing','saved_ai_analysis'].includes(citation.source_type)&&citation.source_url?window.open(citation.source_url,"_blank","noopener,noreferrer"):citation.chunk_id?void showCitation(citation.chunk_id,citation.source_type==='unavailable'):undefined}><b>[{citation.ordinal??j+1}]</b><span>{citation.source_type==='ai_analysis'&&<strong>已保存 AI 分析（派生） · </strong>}{citation.title}{citation.section?` · ${citation.section}`:""}</span><small>{['web','web_news'].includes(citation.source_type)?`网页 · ${citation.published_at||"发布日期未知"} · 抓取 ${citation.observed_at||"时间未知"}`:(citation.observed_at||t("观察时间未知"))}{citation.truncated?` · ${t("保存内容可能不完整")}`:""}</small>{['web','web_news'].includes(citation.source_type)&&citation.source_url&&<span style={{gridColumn:2}}>打开网页来源 ↗</span>}</button>{!citation.chunk_id&&citation.quote&&<details><summary>{t("引用来源")}</summary><p>{citation.quote}</p></details>}{citation.chunk_id&&citationDetails[citation.chunk_id]&&<div className="citation-detail"><div className="citation-detail-content"><p>{citationDetails[citation.chunk_id].derived&&<strong>已保存 AI 分析（派生内容） · v{citationDetails[citation.chunk_id].version_no ?? "?"} · {citationDetails[citation.chunk_id].analysis_version} · {citationDetails[citation.chunk_id].model}</strong>}{citationDetails[citation.chunk_id].excerpt||t("来源已撤回或不可访问")}</p>{(citationDetails[citation.chunk_id].original_quotes??[]).length>0&&<><strong>{t("原文引文：")}</strong><ul>{(citationDetails[citation.chunk_id].original_quotes??[]).map((quote,index)=><li key={index}>{quote}</li>)}</ul></>}</div>{citationDetails[citation.chunk_id].source_url&&<a href={citationDetails[citation.chunk_id].source_url!} target="_blank" rel="noreferrer">打开原始来源 ↗</a>}</div>}</div>)}</details>}
              </>}
            </article>)}</div>
            {preview&&turnPreviews[preview.index]&&<div className="agent-turn-preview" style={{left:Math.min(preview.left,window.innerWidth-310),top:Math.min(preview.top,window.innerHeight-170)}} onPointerEnter={()=>{if(previewTimer.current)window.clearTimeout(previewTimer.current)}} onPointerLeave={()=>setPreview(null)}><strong>{plainChatPreview(turnPreviews[preview.index].question,100)}</strong><p>{plainChatPreview(turnPreviews[preview.index].answer,150)||t("尚无回答")}</p></div>}
          </div>}
        </section>
        {c.status && <p className={`agent-chat-status ${c.state === "error" ? "is-error" : ""}`} role={c.state === "error" ? "alert" : "status"}>{c.status}</p>}
        <form ref={composerForm} className="agent-composer" onSubmit={c.send}>
          <label htmlFor="agent-draft">{t("输入消息")}</label>
          <textarea id="agent-draft" ref={draftBox} rows={1} value={c.draft} onChange={e => c.setDraft(e.target.value)} onKeyDown={onComposerKey} maxLength={4000} disabled={Boolean(c.archiveView)||c.restoring||!c.verified || c.state === "sending" || c.state === "testing" || c.loadingProfiles || c.mutationPending} placeholder={t(c.verified?"给 Agent 发消息…":"请先在设置里填写并测试模型")} />
          <div className="agent-composer-bottom">
            <span className="agent-composer-hint" title={t(c.storage==="Local file"?"API key 保存在本机文件中，并随请求发送给所选服务方，不会写入聊天记录。":"API key 保存在当前浏览器的模型配置中，并随请求发送给所选服务方，不会写入聊天记录。")}>{t("Enter 发送，Shift+Enter 换行")} · {t(c.storage==="Local file"?"API key 保存在本机文件中，并随请求发送给所选服务方，不会写入聊天记录。":"API key 保存在当前浏览器的模型配置中，并随请求发送给所选服务方，不会写入聊天记录。")}</span>
            <div className="agent-composer-tools">
              <ContextMeter conversationId={c.conversationId} refreshKey={`${c.messages.length}:${c.state==="sending"?"sending":"settled"}`} />
              <div className="agent-model-switch"><select aria-label={t("选择模型")} value={c.selectedId??""} onChange={e=>c.selectProfile(e.target.value)}><option value="" disabled>{t("选择模型")}</option>{c.profiles.map(p=><option key={p.id} value={p.id}>{p.display_name}</option>)}</select></div>
              {busy&&<button className="agent-stop" type="button" onClick={c.cancel}>{t("取消请求")}</button>}
              <button className="agent-send" type="submit" aria-label={c.state === "sending" ? t("等待回复…") : t("发送")} title={t("发送")} disabled={sendDisabled}><SendIcon /></button>
            </div>
          </div>
        </form>
      </div>
    </div>
  </main>;
}
function ChatArchiveSettings(){
 const {t}=useLocale(),c=useChat(),input=useRef<HTMLInputElement>(null);
 return <section className="chat-archive-settings"><h2>{t("本地对话归档")}</h2><p>{t("归档仅供本地只读查看，不会作为模型上下文发送。")}</p><div className="chat-archive-tools"><button className="secondary-action" onClick={()=>void c.connectFile()}>{t("连接自动保存文件")}</button>{c.fileName&&<><span>{c.fileName}</span><button className="secondary-action" onClick={c.disconnectFile}>{t("断开")}</button></>}<button className="secondary-action" onClick={c.exportArchive}>{t("导出 JSON")}</button><button className="secondary-action" onClick={()=>input.current?.click()}>{t("导入查看")}</button><input ref={input} className="visually-hidden" type="file" accept="application/json,.json" onChange={e=>{const file=e.target.files?.[0];if(file)void c.importArchive(file);e.target.value=""}}/>{c.archiveStatus&&<span role="status">{c.archiveStatus}</span>}</div>{c.archiveView&&<div className="chat-archive-banner"><span>{t("本地归档只读查看；这些内容不会发送给模型，也不是已核验的服务端证据。")}</span><button className="secondary-action" onClick={c.returnLive}>{t("返回当前对话")}</button><label>{t("选择归档对话")}<select value={c.archiveView.id} onChange={e=>c.selectArchive(e.target.value)}>{(c.archive?.conversations??[]).map(item=><option key={item.id} value={item.id}>{new Date(item.updated_at).toLocaleString()} · {item.messages.length}</option>)}</select></label></div>}</section>;
}

export function AgentAdminPage({ onBack }: { onBack: () => void }) {
 const {t}=useLocale(),c=useChat();const [knowledgeTab,setKnowledgeTab]=useState(false);const profileImportRef=useRef<HTMLInputElement>(null);const [profileStatus,setProfileStatus]=useState(""); const [open,setOpen]=useState(false),[draft,setDraft]=useState<ModelProfile|null>(null),[busy,setBusy]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(""),[modelCandidates,setModelCandidates]=useState<Array<{id:string;name:string}>>([]),[discoveryStatus,setDiscoveryStatus]=useState("");const discoveryTimer=useRef(createModelDiscoveryScheduler()),discovery=useRef(createModelDiscovery((payload,options)=>agentChatModels(payload,options.signal))),draftRef=useRef<ModelProfile|null>(null);draftRef.current=draft;const modal=useRef<HTMLDivElement>(null),previous=useRef<HTMLElement|null>(null),testController=useRef<AbortController|null>(null);
 useEffect(()=>()=>{testController.current?.abort();discovery.current.cancel();discoveryTimer.current.cancel()},[]);
 function add(){previous.current=document.activeElement as HTMLElement;discovery.current.cancel();setModelCandidates([]);setDiscoveryStatus("");setDraft({id:crypto.randomUUID(),display_name:"",provider:"deepseek",base_url:CATALOG[0].baseUrl,model:"",api_key:"",tested_at:null});setError("");setOpen(true);}
 function edit(p:ModelProfile){previous.current=document.activeElement as HTMLElement;discovery.current.cancel();setModelCandidates([]);setDiscoveryStatus("");setDraft({...p,tested_at:null});setError("");setOpen(true);}
 function close(){if(saving)return;if(busy)testController.current?.abort();discoveryTimer.current.cancel();discovery.current.cancel();setModelCandidates([]);setDiscoveryStatus("");setBusy(false);setOpen(false);setDraft(null);previous.current?.focus();}
 useEffect(()=>{if(!open)return;const el=modal.current,main=el?.closest("main.agent-admin");const inerted=main?[...main.children].filter(x=>!x.classList.contains("profile-backdrop")):[];inerted.forEach(x=>x.setAttribute("inert",""));el?.querySelector<HTMLElement>("input")?.focus();const key=(e:KeyboardEvent)=>{if(e.key==="Escape"){e.preventDefault();close();}if(e.key==="Tab"&&el){const items=[...el.querySelectorAll<HTMLElement>('button,input,select')].filter(x=>!x.hasAttribute("disabled"));const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}};document.addEventListener("keydown",key);return()=>{document.removeEventListener("keydown",key);inerted.forEach(x=>x.removeAttribute("inert"))}},[open,busy]);
 async function fetchModels(current=draftRef.current){if(!current)return;if(!current.api_key&&current.provider!=="openrouter"){setDiscoveryStatus(t("填写 API key 后可获取模型列表"));return}setDiscoveryStatus(t("正在获取服务商模型…"));try{const result=await discovery.current.fetch({provider:current.provider,base_url:current.base_url,api_key:current.api_key});if(!result)return;setModelCandidates(result.models);setDiscoveryStatus(result.status==="unsupported"?t("该服务商没有可用的模型列表接口，可手动填写 Model ID"):result.models.length?result.ordering==="provider_timestamp_desc"?t("已按服务商提供的时间排序"):result.ordering==="provider_timestamp_desc_with_undated"?t("部分模型没有可验证的发布时间；已知时间优先排序"):t("服务商未提供可验证的发布时间，按原始顺序显示"):t("服务商暂未返回可用模型，可手动填写 Model ID"));}catch(e){setModelCandidates([]);setDiscoveryStatus(friendlyError(e instanceof Error?e.message:"model_discovery_unavailable",t));}}
 useEffect(()=>{if(open&&draft&&(draft.api_key||draft.provider==="openrouter"))void fetchModels()},[open,draft?.id]);
 function changeKey(value:string){update({api_key:value});discoveryTimer.current.setContext(`${draft?.provider}:${draft?.base_url}`);discovery.current.cancel();setModelCandidates([]);discoveryTimer.current.cancel();if(value.length>=8){setDiscoveryStatus(t("输入完成后将自动获取模型列表"));const current={...draft!,api_key:value};discoveryTimer.current.schedule(()=>void fetchModels(current))}else setDiscoveryStatus(t("填写 API key 后可获取模型列表"))}
 async function testSave(){if(!draft)return;if(!c.profiles.some(p=>p.id===draft.id)&&c.profiles.length>=20){setError(t("最多可保存 20 个模型配置。"));return}discoveryTimer.current.cancel();discovery.current.cancel();setBusy(true);setError("");const controller=new AbortController();testController.current=controller;try{await agentChatRequest("hello",{provider:draft.provider,base_url:draft.base_url,model:draft.model.trim(),api_key:draft.api_key},[],controller.signal);if(controller.signal.aborted)return;const p={...draft,model:draft.model.trim(),tested_at:new Date().toISOString()};setSaving(true);await c.saveProfile(p);if(controller.signal.aborted)return;discoveryTimer.current.cancel();discovery.current.cancel();setOpen(false);setDraft(null);previous.current?.focus();}catch(e){if(!controller.signal.aborted)setError(e instanceof Error?friendlyError(e.message,t):t("连接失败，请重试"));}finally{if(testController.current===controller)testController.current=null;setBusy(false);setSaving(false)}}
 function cancelTest(){if(saving)return;testController.current?.abort();discovery.current.cancel();discoveryTimer.current.cancel();setBusy(false);setSaving(false);setOpen(false);setDraft(null);previous.current?.focus()}
 const update=(patch:Partial<ModelProfile>)=>setDraft(x=>x?{...x,...patch}:x);
 if(knowledgeTab)return <KnowledgeAdminPage onBack={()=>setKnowledgeTab(false)}/>;
 return <main className="workspace agent-admin" aria-labelledby="agent-admin-title"><header className="workspace-heading"><div><p className="eyebrow">Market Evidence Agent / {t("后台设置")}</p><h1 id="agent-admin-title">{t("模型连接设置")}</h1></div><div className="agent-header-actions"><button type="button" className="secondary-action" onClick={()=>setKnowledgeTab(true)}>研究知识库</button><button type="button" className="secondary-action" onClick={onBack}>{t("返回 Agent 对话")}</button></div></header><ChatArchiveSettings/><section className="agent-settings-card"><p>{t(c.storage==="Local file"?"本地文件模式：配置保存在本机后端的普通文件中；清除浏览器站点数据不会删除该文件。":"浏览器模式：配置保存在当前浏览器中；清除站点数据会删除这些配置。")}{c.storageError&&<span role="alert"> {c.storageError}</span>}{c.storageError&&!c.loadingProfiles&&<button type="button" className="secondary-action" onClick={()=>window.location.reload()}>{t("Retry model profiles")}</button>}</p><div className="chat-archive-tools"><button type="button" className="secondary-action" disabled={c.loadingProfiles||c.mutationPending||busy||c.state==="testing"||c.state==="sending"} onClick={c.exportProfiles}>{t("导出模型配置")}</button><button type="button" className="secondary-action" disabled={c.loadingProfiles||c.mutationPending||busy||c.state==="testing"||c.state==="sending"} onClick={()=>profileImportRef.current?.click()}>{t("导入模型配置")}</button><input ref={profileImportRef} className="visually-hidden" type="file" accept="application/json,.json" aria-label={t("导入模型配置文件")} onChange={e=>{const file=e.target.files?.[0];if(file)void c.importProfiles(file).then(()=>setProfileStatus(t("导入的模型需重新测试"))).catch(err=>setProfileStatus(err instanceof Error?err.message:t("模型配置导入失败")));e.target.value="";}}/>{profileStatus&&<span role="status">{profileStatus}</span>}<small>{t("备份文件包含 API keys，请妥善保管并仅在可信设备导入。")}</small></div><div className="profile-grid">{c.profiles.map(p=><button disabled={c.loadingProfiles||c.mutationPending||busy||c.state==="testing"||c.state==="sending"} className={`profile-card ${p.id===c.selectedId?"is-active":""}`} key={p.id} onClick={()=>edit(p)}><span>{t(p.id===c.selectedId?"当前使用":"已保存")}</span><strong>{p.display_name}</strong><small>{t(CATALOG.find(x=>x.provider===p.provider)?.label??p.provider)} · {p.model}</small><small>{t(p.api_key?"API key 已保存":"未保存 key")} · {p.tested_at?`${t("上次测试 ")}${new Date(p.tested_at).toLocaleString()}`:t("尚未测试")}</small></button>)}<button disabled={c.loadingProfiles||c.mutationPending||busy||c.state==="testing"||c.state==="sending"} className="profile-card profile-add" onClick={add}><strong>{t("＋ 添加模型")}</strong><small>{t("添加一个已兼容的服务配置")}</small></button></div><small>{c.storage==="Local file"?`${t("本地文件")}: ${c.profilePath}`:c.storage==="OPFS"?t("本地浏览器私有文件：model-profiles.json (OPFS)"):c.storage==="IndexedDB"?t("本地浏览器存储：IndexedDB（当前浏览器不支持 OPFS）"):t("本地浏览器存储状态未知")} · {c.storage==="Local file"?t("本机文件权限保护配置。"):t("不加密；同源页面代码可读取。")}</small></section>{open&&draft&&<div className="profile-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)close()}}><section ref={modal} className="profile-modal" role="dialog" aria-modal="true" aria-labelledby="profile-title"><button className="profile-close" onClick={close} aria-label={t("关闭")} disabled={busy}>×</button><p className="eyebrow">{draft.id&&c.profiles.some(p=>p.id===draft.id)?t("编辑模型配置"):t("新建模型配置")}</p><h2 id="profile-title">{draft.id&&c.profiles.some(p=>p.id===draft.id)?t("模型配置"):t("添加模型配置")}</h2><label>{t("显示名称")}<input disabled={busy} value={draft.display_name} maxLength={100} onChange={e=>update({display_name:e.target.value})} placeholder={t("例如：财报研究助手")} /></label><label>{t("模型服务")}<select disabled={busy||saving||c.state==="testing"||c.state==="sending"||c.mutationPending} value={draft.provider} onChange={e=>{const p=CATALOG.find(x=>x.provider===e.target.value)!;discoveryTimer.current.cancel();discoveryTimer.current.setContext(`${p.provider}:${p.baseUrl}`);discovery.current.cancel();setModelCandidates([]);setDiscoveryStatus("");update({provider:p.provider,base_url:p.baseUrl,model:"",api_key:""})}}>{CATALOG.map(x=><option key={x.provider} value={x.provider}>{t(x.label)}</option>)}</select></label><label>{t("Base URL")}<input disabled={busy||saving||c.state==="testing"||c.state==="sending"||c.mutationPending} value={draft.base_url} maxLength={2048} onChange={e=>{discoveryTimer.current.cancel();discoveryTimer.current.setContext(`${draft?.provider}:${e.target.value}`);discovery.current.cancel();setModelCandidates([]);setDiscoveryStatus("");update({base_url:e.target.value})}} autoComplete="url" /></label><label>{t("Model ID")}<input disabled={busy} value={draft.model} maxLength={160} onChange={e=>update({model:e.target.value})} placeholder={t(CATALOG.find(x=>x.provider===draft.provider)?.suggestion??"请输入 Model ID")} /></label><label>{t("服务商可用模型")}<span className="model-discovery-row"><select disabled={busy||saving||c.state==="testing"||c.state==="sending"||c.mutationPending} aria-label={t("服务商可用模型")} value="" onChange={e=>{const item=modelCandidates.find(x=>x.id===e.target.value);if(item)update({model:item.id})}}><option value="" disabled>{t("选择列表中的模型填入 Model ID")}</option>{modelCandidates.map(item=><option key={item.id} value={item.id}>{item.name===item.id?item.id:`${item.name} · ${item.id}`}</option>)}</select><button type="button" className="secondary-action" disabled={busy||saving||c.state==="testing"||c.state==="sending"||c.mutationPending} onClick={()=>void fetchModels()}>{t("刷新")}</button></span>{discoveryStatus&&<small role="status">{discoveryStatus}</small>}</label><label>{t("API key")}<input disabled={busy} type="password" value={draft.api_key} maxLength={2048} onChange={e=>changeKey(e.target.value)} autoComplete="new-password" /></label><p className="profile-note">{t("测试并保存会使用当前填写的 endpoint、Model ID 和 API key 进行验证。测试成功且完成本地保存后，修改才会生效。")}</p>{error&&<p className="agent-chat-status is-error" role="alert">{error}</p>}{c.storageError&&<p role="alert">{c.storageError}</p>}<button type="button" className="secondary-action" onClick={cancelTest} disabled={!busy||saving}>{t("取消测试")}</button><button className="primary-action" disabled={busy||!draft.display_name.trim()||!draft.model.trim()||!draft.api_key||!draft.base_url} onClick={()=>void testSave()}>{busy?t("正在测试…"):t("测试并保存")}</button></section></div>}</main>;
}

function friendlyError(message: string, t: (text: string) => string): string {
  const code = message.replaceAll('"', ""); const labels: Record<string, string> = { model_discovery_invalid_key:"API key 长度应为 8 到 2048 个字符。", model_discovery_auth_required:"请填写此服务商的 API key 后重试。", model_discovery_access_denied:"当前 API key 无权读取模型列表。", model_discovery_rate_limited:"模型目录请求被限流，请稍后重试。", model_discovery_timeout:"获取模型列表超时，请检查服务状态。", model_discovery_redirect_rejected:"服务端点拒绝了目录重定向请求。", model_discovery_response_too_large:"服务商模型目录超过 8 MB 安全上限。", model_discovery_unavailable:"暂时无法读取服务商模型列表。", model_discovery_network_error:"获取模型列表时网络连接失败。", model_discovery_incompatible_response:"服务商返回的模型目录格式不兼容。", model_discovery_capacity_reached:"当前目录请求较多，请稍后重试。", provider_invalid_key: "API key 无效，请检查后重新测试。", provider_model_access_denied: "模型不可用或当前 key 无权访问。", provider_rate_or_quota_error: "额度不足或请求被限流，请稍后检查账户。", provider_timeout: "请求超时，请检查服务状态后重试。", provider_redirect_rejected: "服务端点拒绝了重定向请求。", provider_response_too_large: "服务响应超过 1 MB 安全上限。", provider_error: "模型服务暂不可用，请稍后重试。", provider_network_error: "网络连接失败，请稍后重试。", provider_incompatible_response: "服务返回格式不兼容。", provider_empty_or_invalid_answer: "服务未返回有效回答。", provider_model_unavailable_or_invalid_request: "模型不可用或请求参数不受支持。", chat_capacity_reached: "当前对话请求较多，请稍后重试。" };
  return t(labels[code] ?? message);
}
