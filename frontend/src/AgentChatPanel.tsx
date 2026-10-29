import { BotOrb } from "./BotOrb";
import { dispatchAgentAction } from "./agent-client-actions.mjs";
import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { listSkills, type Skill, agentChatStream, restoreAgentChat, clearAgentConversation, getChatMode, setChatMode, listAgentConversations, renameAgentConversation, archiveAgentConversation, selectAgentConversation, personalChatLogin, getKnowledgeCitation, getMaterialAnalysisSettings, saveMaterialAnalysisSettings, resetMaterialAnalysisSettings, getModelCatalog, getTelegramBind, saveTelegramToken, startTelegramBind, unbindTelegram, type CatalogModel, type Bot, type TelegramBindStatus, type KnowledgeCitationDetail, type ConversationSummary, type AgentChatMessage, type AgentChatEvent, type MaterialAnalysisSettings } from "./api";
import { isTaskPostHead, parseTaskPostHead } from "./task-posts.mjs";
import { chatTurnPreviews } from "./chat-thread.mjs";
import { QuestionRail } from "./QuestionRail";
import { ModelCatalogCard } from "./BotPages";
import { slashMatches } from "./skill-items.mjs";
import { citationOrdinal, linkCitationMarkers } from "./chat-citations.mjs";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Tx, useLocale } from "./i18n";
import { AgentChatLifecycle, chatContext } from "./agentChatLifecycle.js";
import { KnowledgeAdminPage } from './KnowledgeAdminPage';
import { ContextMeter } from "./ContextMeter";
import { restoreConversationAfterPersistence, restoreConversationSafely } from "./chat-session.mjs";
import { normalizeClarification, selectedClarification, selectedOptions, manualClarification, clarificationTitle, isActiveClarification } from "./chat-clarification.mjs";
import { toolLabel } from "./agent-tool-labels.mjs";
import { ThinkingPanel } from "./ThinkingPanel";
import { cancelRunning, upsertNode, type FlowNode } from "./turn-flow.mjs";
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
type ChatSession = { conversationId:string|null; verified: boolean; state: State; status: string; restoring:boolean; messages: AgentChatMessage[]; historyHasMore:boolean; loadingEarlier:boolean; loadEarlier:()=>Promise<void>; draft: string; setDraft: (v: string) => void; send: (e: FormEvent) => Promise<void>; choose:(value:string,requestId:string)=>Promise<void>; chooseOptions:(values:string[],requestId:string)=>Promise<void>; replyToCard:(value:string,requestId:string)=>Promise<void>; cancel: () => void; clear: () => void; openConversation:(id:string,created?:string)=>Promise<void>; archive: ChatArchive | null; archiveView: ArchiveConversation | null; fileName: string; archiveStatus: string; connectFile: () => Promise<void>; disconnectFile: () => void; exportArchive: () => void; importArchive: (file: File) => Promise<void>; selectArchive: (id:string) => void; returnLive: () => void; bot: Bot | null; selectBot: (bot: Bot | null) => Promise<void> };
const Context = createContext<ChatSession | null>(null);
const WRITE_CARDS = new Set(["write_confirmation", "confirm_write", "choose_write_action", "choose_revision_sources"]);
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

function ClarificationCard({clarification,active,onChoose,onChooseMany,onReply}:{clarification:NonNullable<AgentChatMessage["clarification"]>;active:boolean;onChoose:(value:string)=>Promise<void>;onChooseMany:(values:string[])=>Promise<void>;onReply:(value:string)=>Promise<void>}) {
  const { t } = useLocale();
  const [reply,setReply]=useState("");
  const [picked,setPicked]=useState<string[]>([]);
  const options=clarification.options??[], limits=clarification.select??{min:1,max:options.length};
  const write=WRITE_CARDS.has(clarification.reason), confirm=["write_confirmation","confirm_write","choose_revision_sources"].includes(clarification.reason);
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
    {options.length>0&&<fieldset className="agent-clarification-picks" disabled={!active}>
      {options.map(option=><label key={option.value}>
        <input type="checkbox" checked={picked.includes(option.value)}
          onChange={event=>setPicked(current=>event.target.checked?(current.length<limits.max?[...current,option.value]:current):current.filter(value=>value!==option.value))} />
        <span><span className="agent-pick-title">{option.label}</span>{option.detail&&<small>{option.detail}</small>}</span>
      </label>)}
      {active&&picked.length<limits.min&&<small className="agent-pick-hint">{t("至少勾选一份材料")}</small>}
    </fieldset>}
    <div className="agent-clarification-options">{clarification.choices.map((choice,index)=>{
      const cancel=/^cancel:/.test(choice.value);
      const ticks=options.length>0&&/^confirm:/.test(choice.value);
      const kind=write&&confirm&&index===0?"is-primary":write&&(cancel||(confirm&&index===clarification.choices.length-1))?"is-quiet":"";
      return <button type="button" className={kind} key={choice.value} disabled={!active||(ticks&&picked.length<limits.min)}
        onClick={()=>void (ticks?onChooseMany(picked):onChoose(choice.value))}>{t(choice.label)}</button>;
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
  // Models come only from the server catalog (UX batch 3): the owner's turns name a bot or the catalog default,
  // never a key.  A guest has no catalog, so a guest cannot send until they log in.
  const verified = getChatMode() === "personal", [state, setState] = useState<State>("idle"), [status, setStatus] = useState(""), [messages, setMessages] = useState<AgentChatMessage[]>([]), [draft, setDraft] = useState(""), [conversationId, setConversationId] = useState<string | null>(null);
  const [historyHasMore,setHistoryHasMore]=useState(false),[historyBefore,setHistoryBefore]=useState<number|null>(null),[loadingEarlier,setLoadingEarlier]=useState(false);
  const conversationIdRef=useRef<string|null>(null); conversationIdRef.current=conversationId;
  // The bot whose line is open.  Its turns send only bot_id: the server takes the model and key from its catalog.
  const [bot,setBot]=useState<Bot|null>(null), botRef=useRef<Bot|null>(null);
  const earlierController=useRef<AbortController|null>(null),loadingEarlierRef=useRef(false);
  const [restoring,setRestoring]=useState(false);
  const [archive,setArchive]=useState<ChatArchive|null>(null),[archiveView,setArchiveView]=useState<ArchiveConversation|null>(null),[fileName,setFileName]=useState(""),[archiveStatus,setArchiveStatus]=useState(""),[importedReadOnly,setImportedReadOnly]=useState(false);
  const connectedArchive=useRef<ChatArchive|null>(null), archiveRef=useRef<ChatArchive|null>(null), createdAt=useRef(new Date().toISOString()), epoch=useRef(0);
  const handle=useRef<FileHandle|null>(null), localId=useRef(readLocalConversationId()), writer=useRef(createSerializedWriter(async (target:FileHandle,data:string)=>{if(!target)throw new Error("未连接归档文件");await writeAtomically(target,data);}));
  const lifecycle = useRef(new AgentChatLifecycle());
  useEffect(() => () => lifecycle.current.invalidate(), []);
  useEffect(() => { const controller=new AbortController(), id=lifecycle.current.begin(controller); setRestoring(true); setState("restoring"); void restoreConversationSafely(lifecycle.current,id,controller.signal,signal=>restoreAgentChat(undefined,signal),saved=>{if(!saved.conversationId)clearAgentConversation();else{localId.current=saved.conversationId;createdAt.current=saved.createdAt??new Date().toISOString();try{window.localStorage.setItem(`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,localId.current)}catch{}}setConversationId(saved.conversationId);setMessages(saved.messages.filter(m=>m.role==="user"||m.role==="assistant"));setHistoryHasMore(saved.hasMore);setHistoryBefore(saved.nextBefore);setState("idle")}).catch(() => { if (lifecycle.current.isCurrent(id)) { setStatus(t("历史记录暂时无法加载；可稍后重试。"));setState("idle"); } }).finally(()=>{if(lifecycle.current.isCurrent(id))setRestoring(false)}); return () => { controller.abort(); earlierController.current?.abort(); lifecycle.current.finish(id); }; }, []);
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
    if (last?.role === "assistant") return [...items.slice(0, -1), { ...last, status: "cancelled", tool_trace: last.tool_trace?.map(x => x.status === "running" ? { ...x, status: "cancelled" } : x), trace: last.trace ? cancelRunning(last.trace, "cancelled") : last.trace }];
    if (last?.role === "user") return [...items, { role: "assistant", content: "", status: "cancelled" }];
    return items;
  });
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
  async function chooseOptions(values:string[],requestId:string) {
    const card=messages.at(-1)?.clarification;
    if (!card || card.request_id!==requestId) return;
    const selection=selectedOptions(card,values);
    await sendContent(selection.message,selection.pendingRequestId,selection.label);
  }
  async function replyToCard(value:string,requestId:string) {
    const card=messages.at(-1)?.clarification;
    if (!card || card.request_id!==requestId || archiveView) return;
    const reply=manualClarification(card,value);
    await sendContent(reply.message,reply.pendingRequestId);
  }
  async function sendContent(content:string,pendingRequestId?:string,label?:string) {
    if (!(verified||botRef.current) || loadingEarlierRef.current || !content || state === "sending" || state === "testing") return;
    let context: AgentChatMessage[]; try { context = chatContext(messages, { role: "user", content }); } catch (error) { setStatus(t(error instanceof Error ? error.message : "消息过长，请缩短后重试。")); return; }
    const next = [...messages, { role: "user" as const, content: label||content }]; setMessages(next); setDraft(""); setState("sending"); setStatus(""); const turnBot = botRef.current, tested = turnBot ? { bot_id: turnBot.id } : { catalog_model: "" }, controller = new AbortController(), id = lifecycle.current.begin(controller);
    let localQueue = ""; let accumulated = ""; let turnId: string|undefined; let citations: NonNullable<AgentChatMessage["citations"]> = []; let clarification:AgentChatMessage["clarification"]; let terminalStatus="complete"; let reusedSources=false; const tools: NonNullable<AgentChatMessage["tool_trace"]> = []; let trace: FlowNode[] = []; let thinkingMs: number|undefined; const startedAt = Date.now();
    try {
      await agentChatStream(tested, content, turnBot ? null : conversationId, controller.signal, (event: AgentChatEvent) => {
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
        if (event.event === "trace") trace = upsertNode(trace, event.data);
        if (event.event === "done") {terminalStatus=String(event.data.status??"complete");reusedSources=(event.data.audit as {reused_sources?:boolean}|undefined)?.reused_sources===true;const thinking=Number(event.data.thinking_ms);if(Number.isFinite(thinking))thinkingMs=thinking;}
        if (event.event === "delta" && typeof event.data.text === "string") accumulated += event.data.text;
        if (event.event === "error") throw new Error(String(event.data.code ?? "chat_failed"));
        setMessages([...next, { role: "assistant", content: accumulated + localQueue, turn_id:turnId, tool_trace: [...tools], citations, clarification, trace, thinking_ms: thinkingMs, started_at: startedAt, status:terminalStatus, reused_sources:reusedSources }]);
        return true;
      },pendingRequestId,label);
      if (!lifecycle.current.isCurrent(id)) return; setMessages([...next, { role: "assistant", content: accumulated + localQueue, turn_id:turnId, tool_trace: [...tools], citations, clarification, trace, thinking_ms: thinkingMs, started_at: startedAt, status:terminalStatus, reused_sources:reusedSources }]); setStatus(""); setState("ready");
    }
    catch (error) { if (!lifecycle.current.isCurrent(id)) return; const stopped=error instanceof DOMException && error.name==="AbortError"; const stoppedTools=tools.map(x => x.status === "running" ? { ...x, status: "cancelled" } : x); setMessages([...next, { role:"assistant",content:accumulated,status:stopped?"cancelled":"failed",tool_trace:stoppedTools,trace:cancelRunning(trace,stopped?"cancelled":"failed"),thinking_ms:thinkingMs??Date.now()-startedAt }]); setState(stopped?"ready":"error"); setStatus(stopped?translate("请求已取消；供应商可能仍会计费。"):error instanceof Error ? friendlyError(error.message, translate) : translate("发送失败，请重试")); }
    finally { lifecycle.current.finish(id); }
  }
  const cancel = () => { const wasSending = state === "sending"; lifecycle.current.invalidate(); if (wasSending) markSendingTurnCancelled(); setRestoring(false);setState(verified ? "ready" : "idle"); setStatus(translate("请求已取消；供应商可能仍会计费。")); };
  async function openConversation(id:string, created?:string){
    earlierController.current?.abort();loadingEarlierRef.current=false;setLoadingEarlier(false);
    const previous=latestMessages.current;let lastUser=-1;for(let i=previous.length-1;i>=0;i--)if(previous[i].role==="user"){lastUser=i;break;}const completed=state==="sending"?previous.slice(0,Math.max(0,lastUser)):previous;
    const controller=new AbortController(), token=lifecycle.current.begin(controller);setRestoring(true);setState("restoring");setStatus("");
    try{await restoreConversationAfterPersistence(lifecycle.current,token,controller.signal,()=>handle.current&&completed.length?persistConversation(completed):Promise.resolve(),signal=>restoreAgentChat(id,signal,true),saved=>{const restoredId=saved.conversationId??id,chosenCreatedAt=created??saved.createdAt??new Date().toISOString();selectAgentConversation(restoredId,chosenCreatedAt);setConversationId(restoredId);const display=saved.messages.filter(m=>m.role==="user"||m.role==="assistant");setMessages(display);setHistoryHasMore(saved.hasMore);setHistoryBefore(saved.nextBefore);latestMessages.current=display;localId.current=restoredId;createdAt.current=chosenCreatedAt;try{window.localStorage.setItem(`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,restoredId)}catch{}setArchiveView(null);setImportedReadOnly(false);setDraft("");setState(verified?"ready":"idle")})}
    catch(error){if(lifecycle.current.isCurrent(token)&&!(error instanceof DOMException&&error.name==="AbortError")){setStatus(t("无法恢复服务端聊天记录。"));setState(verified?"ready":"idle");}}
    finally{if(lifecycle.current.isCurrent(token)){lifecycle.current.finish(token);setRestoring(false)}}
  }
  async function loadEarlier(){
    if(!conversationId||!historyHasMore||historyBefore===null||loadingEarlierRef.current||restoring||state==="sending"||state==="testing"||archiveView||importedReadOnly)return;
    const target=conversationId,cursor=historyBefore;
    earlierController.current?.abort();
    const controller=new AbortController();earlierController.current=controller;
    loadingEarlierRef.current=true;setLoadingEarlier(true);
    try{
      const page=await restoreAgentChat(target,controller.signal,true,cursor,20);
      if(controller.signal.aborted||conversationIdRef.current!==target)return;
      const older=page.messages.filter(m=>m.role==="user"||m.role==="assistant");
      if(older.length)setMessages(previous=>[...older,...previous]);
      setHistoryBefore(page.nextBefore);setHistoryHasMore(page.hasMore);
    }catch(error){
      if(!(error instanceof DOMException&&error.name==="AbortError")&&conversationIdRef.current===target)setStatus(t("更早的聊天记录暂时无法加载。"));
    }finally{
      if(earlierController.current===controller){earlierController.current=null;loadingEarlierRef.current=false;setLoadingEarlier(false);}
    }
  }
  const clear = () => { earlierController.current?.abort();loadingEarlierRef.current=false;setLoadingEarlier(false); const previous=latestMessages.current;let lastUser=-1;for(let i=previous.length-1;i>=0;i--)if(previous[i].role==="user"){lastUser=i;break;}const completed=state==="sending"?previous.slice(0,Math.max(0,lastUser)):previous;if(handle.current&&completed.length)void persistConversation(completed); lifecycle.current.invalidate();setRestoring(false);setArchiveView(null);if(importedReadOnly){setImportedReadOnly(false);setArchive(archiveRef.current);}clearAgentConversation(); setConversationId(null); setMessages([]); setHistoryHasMore(false); setHistoryBefore(null); latestMessages.current=[]; setDraft(""); localId.current=crypto.randomUUID();createdAt.current=new Date().toISOString();try{window.localStorage.setItem(`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,localId.current);}catch{}setState(verified ? "ready" : "idle"); setStatus(""); };
  async function selectBot(next:Bot|null){
    const switched=botRef.current?.id!==next?.id;botRef.current=next;setBot(next);
    if(!next)return;
    // Each bot has one line: open it, or start empty when the bot has not spoken yet.
    if(next.conversation_id){if(next.conversation_id!==conversationIdRef.current)await openConversation(next.conversation_id);}
    else if(switched)clear();
  }
  const visibleMessages=archiveView?archiveView.messages as AgentChatMessage[]:messages;
  return <Context.Provider value={{ conversationId,verified:verified||Boolean(bot), state, status, restoring,messages:visibleMessages, historyHasMore, loadingEarlier, loadEarlier, draft, setDraft, send, choose, chooseOptions, replyToCard, cancel, clear,openConversation,archive,archiveView,fileName,archiveStatus,connectFile,disconnectFile,exportArchive,importArchive,selectArchive,returnLive,bot,selectBot }}>{children}</Context.Provider>;
}
function readLocalConversationId(){try{const key=`market-evidence-agent:local-chat-id:${getChatMode()}:v1`,existing=window.localStorage.getItem(key);if(existing&&/^[\w-]{1,80}$/.test(existing))return existing;const id=crypto.randomUUID();window.localStorage.setItem(key,id);return id;}catch{return crypto.randomUUID();}}
function archiveMessages(messages:AgentChatMessage[]){return messages.filter(m=>m.role==="user"||m.role==="assistant").map(m=>({role:m.role as "user"|"assistant",content:m.content,...(m.status?{status:m.status as "failed"|"cancelled"}:{}),...(m.role==="assistant"&&m.citations?{citations:m.citations}:{}),...(m.role==="assistant"&&m.tool_trace?{tool_trace:m.tool_trace.map(x=>({call_id:x.call_id,name:x.name,arguments:x.arguments,result:x.result,status:x.status as "running"|"complete"|"not_found"|"failed"|"cancelled"}))}:{})}));}
function useChat() { const value = useContext(Context); if (!value) throw new Error("Agent chat provider is missing"); return value; }
/** Open one of the caller's stored conversations (memory page, #agent/<id> links). */
export function useOpenConversation() { return useChat().openConversation; }
/** Open a bot's line (its turns then carry only bot_id), or leave every bot's line with null. */
export function useSelectBot() { return useChat().selectBot; }

const SIDE_OPEN_KEY="market-evidence-agent:bot-side-open:v1";
function PanelIcon(){return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M15 4v16"/></svg>}
export function AgentChatPanel({ onSettings,onModeChange,bot,side,onTurnSettled }: { onSettings: () => void; onModeChange:(mode:"guest"|"personal")=>void; bot?: Bot | null; side?: ReactNode; onTurnSettled?: () => void }) {
  const { t } = useLocale(), c = useChat(), transcriptRef = useRef<HTMLElement>(null);
  // A finished turn changes the bot's last line (and gives a new bot its conversation): tell the list.
  const wasSending=useRef(false);
  useEffect(()=>{if(c.state==="sending"){wasSending.current=true;return;}if(wasSending.current){wasSending.current=false;onTurnSettled?.();}},[c.state]); // eslint-disable-line react-hooks/exhaustive-deps
  const turnPreviews=chatTurnPreviews(c.messages);
  const railTurns=turnPreviews.map(turn=>({...turn,turnId:c.messages[turn.userIndex]?.turn_id}));
  const followTranscript=useRef(true);
  useEffect(()=>{const el=transcriptRef.current;if(!el)return;const update=()=>{followTranscript.current=el.scrollHeight-el.scrollTop-el.clientHeight<90};el.addEventListener("scroll",update,{passive:true});return()=>el.removeEventListener("scroll",update)},[]);
  const [history,setHistory]=useState<ConversationSummary[]>([]),[historyHasMore,setHistoryHasMore]=useState(false),[historyError,setHistoryError]=useState(""),[historyLoading,setHistoryLoading]=useState(false),[mode,setMode]=useState(getChatMode());
  const [copied,setCopied]=useState<number|null>(null),[copyStatus,setCopyStatus]=useState(""),[,setFeedbackTick]=useState(0),[citationDetails,setCitationDetails]=useState<Record<string,KnowledgeCitationDetail>>({});
  const [openCitationTurn,setOpenCitationTurn]=useState<number|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  // A bot's right rail (工作区 / 档案) folds away so the thread can use the full width; the choice is kept.
  const [sideOpen,setSideOpen]=useState(()=>{try{return localStorage.getItem(SIDE_OPEN_KEY)!=="0"}catch{return true}});
  const toggleSide=()=>setSideOpen(open=>{const next=!open;try{localStorage.setItem(SIDE_OPEN_KEY,next?"1":"0")}catch{/* optional preference */}return next});
  // On a narrow screen the history drawer covers the header toggle, so Esc and a tap outside close it.
  useEffect(()=>{if(!historyOpen)return;const onKey=(e:KeyboardEvent)=>{if(e.key==="Escape")setHistoryOpen(false)};window.addEventListener("keydown",onKey);return()=>window.removeEventListener("keydown",onKey)},[historyOpen]);
  const [loginOpen,setLoginOpen]=useState(false),[loginCode,setLoginCode]=useState(""),[loginError,setLoginError]=useState("");
  const [activeTurn,setActiveTurn]=useState(0);
  const composerForm=useRef<HTMLFormElement>(null),draftBox=useRef<HTMLTextAreaElement>(null);
  useEffect(()=>{const el=transcriptRef.current;if(!el)return;const update=()=>{const top=el.getBoundingClientRect().top;const turns=el.querySelectorAll<HTMLElement>(".agent-message.is-user");let active=0;turns.forEach((item,i)=>{if(item.getBoundingClientRect().top<=top+100)active=i});setActiveTurn(active)};el.addEventListener("scroll",update,{passive:true});update();return()=>el.removeEventListener("scroll",update)},[c.messages]);
  // The composer grows with what is typed (up to its own cap) and shrinks back after sending.
  useLayoutEffect(()=>{const box=draftBox.current;if(!box)return;box.style.height="auto";box.style.height=`${Math.min(box.scrollHeight,220)}px`},[c.draft]);
  async function refreshHistory(){setHistoryLoading(true);setHistoryError("");try{const result=await listAgentConversations();setHistory(result.conversations);setHistoryHasMore(result.hasMore)}catch(e){setHistoryError(e instanceof Error?e.message:t("历史对话暂时无法加载。"))}finally{setHistoryLoading(false)}}
  useEffect(()=>{void refreshHistory()},[c.state,mode]);
  async function loadMoreHistory(){try{const result=await listAgentConversations(history.length);setHistory(items=>[...items,...result.conversations]);setHistoryHasMore(result.hasMore)}catch(e){setHistoryError(e instanceof Error?e.message:t("历史对话暂时无法加载。"))}}
  async function loadEarlierAtTop(){
    const el=transcriptRef.current; if(!el||!c.historyHasMore||c.loadingEarlier||c.archiveView)return;
    const height=el.scrollHeight, top=el.scrollTop;
    await c.loadEarlier();
    requestAnimationFrame(()=>{if(el)el.scrollTop=el.scrollHeight-height+top;});
  }
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
  // The "/" picker: a draft like "/财" (no space yet) lists the bot's matching enabled skills, its usual ones first.
  const [skillList,setSkillList]=useState<Skill[]|null>(null),[pickIndex,setPickIndex]=useState(0),[pickClosed,setPickClosed]=useState(false);
  const slashActive=Boolean(bot)&&mode==="personal"&&c.draft.startsWith("/")&&!/\s/.test(c.draft);
  useEffect(()=>{if(!slashActive||skillList)return;let live=true;listSkills().then(d=>{if(live)setSkillList(d.skills)}).catch(()=>{if(live)setSkillList([])});return()=>{live=false}},[slashActive,skillList]);
  const slashItems=slashActive&&!pickClosed?slashMatches(skillList??[],c.draft,bot?.skills??[]):[];
  useEffect(()=>{setPickIndex(0);setPickClosed(false)},[c.draft]);
  function pickSkill(skill:Skill){c.setDraft(`/${skill.name} `);draftBox.current?.focus()}
  function onComposerKey(event:ReactKeyboardEvent<HTMLTextAreaElement>){
    if(slashItems.length){
      if(event.key==="ArrowDown"||event.key==="ArrowUp"){event.preventDefault();setPickIndex(i=>(i+(event.key==="ArrowDown"?1:-1)+slashItems.length)%slashItems.length);return}
      if((event.key==="Enter"||event.key==="Tab")&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();pickSkill(slashItems[Math.min(pickIndex,slashItems.length-1)]);return}
      if(event.key==="Escape"){event.preventDefault();setPickClosed(true);return}
    }
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
  const sendDisabled=Boolean(c.archiveView)||c.restoring||!c.verified||!c.draft.trim()||c.state==="sending"||c.state==="testing";
  const busy=c.state==="sending"||c.state==="testing"||c.restoring;
  const currentConversation=window.sessionStorage.getItem("market-evidence-agent:chat-conversation:v1");
  return <main className="workspace agent-page" aria-labelledby="agent-chat-title">
    <header className="workspace-heading agent-page-heading">
      {bot?<div className="agent-title-row"><BotOrb avatar={bot.avatar} name={bot.name} size={30} track /><h1 id="agent-chat-title">{bot.name}</h1>{bot.title&&<small className="bot-title">{bot.title}</small>}</div>
        :<div className="agent-title-row"><button type="button" className="mobile-history-toggle secondary-action" aria-expanded={historyOpen} onClick={()=>setHistoryOpen(x=>!x)}>{t("历史对话")}</button><h1 id="agent-chat-title">模型对话</h1></div>}
      <div className="agent-header-actions">{side&&<button type="button" className={`side-toggle${sideOpen?" is-open":""}`} aria-expanded={sideOpen} aria-controls="bot-side" aria-label={sideOpen?t("收起工作区"):t("打开工作区")} title={sideOpen?t("收起工作区"):t("打开工作区")} onClick={toggleSide}><PanelIcon /><span>{t("工作区")}</span></button>}<span>{mode==="personal"?t("个人模式"):t("访客模式")}</span><button className="secondary-action" onClick={switchMode} type="button">{t("切换模式")}</button><button className="secondary-action" onClick={onSettings} type="button">{t("设置")}</button></div>
    </header>
    {loginOpen&&<div className="personal-login-panel"><label>{t("个人聊天登录码")}<input type="password" value={loginCode} onChange={e=>setLoginCode(e.target.value)} autoComplete="current-password" maxLength={256}/></label><button className="primary-action" onClick={()=>void submitPersonalLogin()} disabled={!loginCode}>{t("登录个人聊天")}</button><button className="secondary-action" onClick={()=>setLoginOpen(false)}>{t("取消")}</button>{loginError&&<p role="alert">{loginError}</p>}</div>}
    <div className={`agent-layout${bot?" is-bot":""}${side&&!sideOpen?" is-side-closed":""}`}>
      {!bot&&<>{historyOpen&&<div className="agent-history-scrim" aria-hidden="true" onClick={()=>setHistoryOpen(false)}/>}
      <aside className={`agent-history${historyOpen?" is-open":""}`} aria-label={t("历史对话")}>
        <button className="primary-action" type="button" onClick={()=>{c.clear();setHistoryOpen(false)}}>＋ {t("新建对话")}</button>
        <h2>{t("历史对话")}</h2>
        {historyLoading&&<p role="status">{t("正在读取…")}</p>}
        {historyError&&<p role="alert">{historyError} <button className="secondary-action" onClick={()=>void refreshHistory()}>{t("重试")}</button></p>}
        {!historyLoading&&!historyError&&!history.length&&<p>{t("还没有保存的对话")}</p>}
        {history.map(item=><HistoryRow key={item.id} item={item} current={Boolean(c.messages.length)&&item.id===currentConversation} onOpen={()=>void openHistory(item)} onRename={title=>renameHistory(item,title)} onArchive={()=>archiveHistory(item)} />)}
        {historyHasMore&&<button className="secondary-action" onClick={()=>void loadMoreHistory()}>{t("加载更多")}</button>}
      </aside></>}
      <div className="agent-main-column">
        {c.archiveView&&<div className="chat-archive-banner"><span>{t("本地归档只读查看")}</span><button className="secondary-action" onClick={c.returnLive}>{t("返回当前对话")}</button><button className="secondary-action" onClick={()=>{c.returnLive();c.clear()}}>{t("新建对话")}</button></div>}
        <div className="agent-capability"><span aria-hidden="true">✳</span><p>{t("消息和研究工具轨迹会保存到服务端；工具按权限读取资料并执行明确请求的系统任务。")}</p><span className={`agent-connection ${c.verified ? "is-ready" : ""}`}>{bot ? `${t("模型")} · ${bot.model ?? t("服务器默认模型")}` : c.verified ? `${t("模型")} · ${t("服务器默认模型")}` : t("登录后可用")}</span></div>
        <section ref={transcriptRef} className="agent-transcript" aria-live="polite" onScroll={e=>{if(e.currentTarget.scrollTop<32)void loadEarlierAtTop();}}>
          {!c.archiveView&&c.historyHasMore&&<div className="agent-earlier"><button type="button" className="secondary-action" onClick={()=>void loadEarlierAtTop()} disabled={c.loadingEarlier}>{c.loadingEarlier?t("正在加载更早消息…"):t("加载更早消息")}</button></div>}
          {c.restoring&&<p role="status">{t("正在恢复对话…")}</p>}
          {copyStatus&&<p className="copy-status" role="status">{copyStatus}</p>}
          {c.messages.length === 0 ? <div className="agent-welcome"><h2>{t("从一个问题开始")}</h2><p>{t(c.verified ? "连接已就绪，可以输入问题或选择下方建议。" : "登录个人聊天后才能对话：模型由服务器模型目录提供。")}</p><div>{["查看 AAPL 最近行情", "AAPL 已保存的最新财报有哪些", "可信度星级是什么"].map(x => <button type="button" key={x} onClick={() => c.setDraft(t(x))}>{t(x)} <span aria-hidden="true">↗</span></button>)}</div></div> :
          <div className="agent-thread-grid">
            <QuestionRail turns={railTurns} activeTurn={activeTurn} transcriptRef={transcriptRef} conversationId={c.conversationId} />
            <div className="agent-thread">{c.messages.map((m, i) => <article data-turn-index={m.role==="user"?turnPreviews.findIndex(turn=>turn.userIndex===i):undefined} id={`chat-message-${i}`} className={`agent-message is-${m.role}${m.role==="user"&&isTaskPostHead(m.content)?" is-task-post":""}${i===lastIndex?" is-last":""}`} key={i}>
              {m.role === "assistant"&&<ThinkingPanel trace={m.trace} thinkingMs={m.thinking_ms} startedAt={m.started_at} running={i === lastIndex && c.state === "sending"} />}
              {m.role !== "user"&&<div className="agent-tool-badges">{m.reused_sources&&<span className="tool-badge status-complete">{t("复用已核实来源")}</span>}{!m.trace?.length&&m.tool_trace?.map((tool,j)=><span key={`${tool.name}-${j}`} className={`tool-badge status-${tool.status}`}>{t(toolLabel(tool.name))} · {t(tool.status === "complete" ? (tool.card ? "执行完成" : "已完成") : tool.status === "not_found" ? "没有匹配记录" : tool.status === "failed" ? "查询失败" : tool.status === "cancelled" ? "已取消" : tool.status === "submitted" ? "已提交" : "执行中")}{toolResultSummary(tool.result)&&` · ${toolResultSummary(tool.result)}`}</span>)}</div>}
              {m.role==="user"&&isTaskPostHead(m.content)?<TaskPostHead content={m.content} />:m.role==="user"?<div className="agent-user-bubble"><p>{m.content}</p><button type="button" className="agent-user-copy" aria-label={copied===i?t("已复制"):copied===-i-1?t("复制失败"):t("复制问题")} title={t("复制问题")} onClick={()=>void copyAnswer(m.content,i)}>{copied===i?<CheckIcon />:<CopyIcon />}</button></div>:<div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{table:({node,...props})=><div className="markdown-table-wrap"><table {...props}/></div>,a:({node,...props})=>{const href=String(props.href??"");const match=href.match(/^#citation-(\d+)$/);const ordinal=match?Number(match[1]):null;const citation=ordinal?m.citations?.find((item,index)=>citationOrdinal(item,index)===ordinal):undefined;if(match&&!citation)return <span className="citation-unmatched">[{props.children}]</span>;const citationLabel=citation?t("引用来源")+" "+ordinal+" · "+citation.title:undefined;return <a {...props} className={citation?"citation-ref":undefined} aria-label={citationLabel} title={citationLabel} href={citation?href:props.href} onClick={event=>{if(!citation)return;event.preventDefault();setOpenCitationTurn(i);["web","web_news","official_filing","saved_filing","saved_ai_analysis","reported_figure"].includes(citation.source_type)&&citation.source_url?window.open(citation.source_url,"_blank","noopener,noreferrer"):citation.chunk_id?void showCitation(citation.chunk_id,citation.source_type==="unavailable"):undefined}} target={citation?undefined:"_blank"} rel={citation?undefined:"noopener noreferrer"}/>} }}>{linkCitationMarkers(m.content,m.citations)}</ReactMarkdown></div>}
              {m.role==="assistant"&&m.clarification&&<ClarificationCard clarification={m.clarification} active={isActiveClarification(c.messages,i,Boolean(c.archiveView))&&!c.restoring&&c.verified&&c.state!=="sending"&&c.state!=="testing"} onChoose={value=>c.choose(value,m.clarification!.request_id)} onChooseMany={values=>c.chooseOptions(values,m.clarification!.request_id)} onReply={value=>c.replyToCard(value,m.clarification!.request_id)} />}
              {m.role === "assistant" && m.status && m.status !== "complete" && m.status !== "succeeded" && <small>{t(m.status === "cancelled" ? "本轮已取消，未生成完整回答。" : m.status === "waiting_for_input" ? "等待选择后继续" : m.status === "running" ? "任务运行中，可稍后查询状态" : m.status === "partially_succeeded" ? "部分来源可用；缺项已标出" : "本轮失败；已保留已生成内容。")}</small>}
              {m.role === "assistant"&&m.content&&<>
                <div className="agent-answer-actions">
                  <button type="button" className="agent-action-button" aria-label={copied===i?t("已复制"):copied===-i-1?t("复制失败"):t("复制回答")} title={t("复制回答")} onClick={()=>void copyAnswer(m.content,i)}>{copied===i?<CheckIcon />:<CopyIcon />}</button>
                  {m.turn_id&&(["up","down"] as const).map(value=>{const saved=feedbackOf(m.turn_id!);return <button type="button" key={value} className="agent-action-button" aria-pressed={saved===value} aria-label={value==="up"?t("有帮助"):t("没有帮助")} title={value==="up"?t("有帮助"):t("没有帮助")} onClick={()=>giveFeedback(m.turn_id!,value)}>{value==="up"?<ThumbUpIcon />:<ThumbDownIcon />}</button>})}
                  {copied===i&&<span className="agent-action-note" role="status">{t("已复制")}</span>}
                </div>
                {Boolean(m.citations?.length)&&<details className="citation-drawer" open={openCitationTurn===i}><summary>{t("引用来源")} · {m.citations!.length}</summary>{m.citations!.map((citation,j)=><div className="citation-item" key={citation.chunk_id??citation.source_url??citation.ordinal}><button type="button" onClick={()=>['web','web_news','official_filing','saved_filing','saved_ai_analysis','reported_figure'].includes(citation.source_type)&&citation.source_url?window.open(citation.source_url,"_blank","noopener,noreferrer"):citation.chunk_id?void showCitation(citation.chunk_id,citation.source_type==='unavailable'):undefined}><b>[{citation.ordinal??j+1}]</b><span>{citation.source_type==='ai_analysis'&&<strong>已保存 AI 分析（派生） · </strong>}{citation.source_type==='reported_figure'&&<strong>{t("SEC XBRL 披露值")} · </strong>}{citation.title}{citation.section?` · ${citation.section}`:""}</span><small>{['web','web_news'].includes(citation.source_type)?`网页 · ${citation.published_at||"发布日期未知"} · 抓取 ${citation.observed_at||"时间未知"}`:(citation.observed_at||t("观察时间未知"))}{citation.truncated?` · ${t("保存内容可能不完整")}`:""}</small>{['web','web_news'].includes(citation.source_type)&&citation.source_url&&<span style={{gridColumn:2}}>打开网页来源 ↗</span>}</button>{!citation.chunk_id&&citation.quote&&<details><summary>{t("引用来源")}</summary><p>{citation.quote}</p></details>}{citation.chunk_id&&citationDetails[citation.chunk_id]&&<div className="citation-detail"><div className="citation-detail-content"><p>{citationDetails[citation.chunk_id].derived&&<strong>已保存 AI 分析（派生内容） · v{citationDetails[citation.chunk_id].version_no ?? "?"} · {citationDetails[citation.chunk_id].analysis_version} · {citationDetails[citation.chunk_id].model}</strong>}{citationDetails[citation.chunk_id].excerpt||t("来源已撤回或不可访问")}</p>{(citationDetails[citation.chunk_id].original_quotes??[]).length>0&&<><strong>{t("原文引文：")}</strong><ul>{(citationDetails[citation.chunk_id].original_quotes??[]).map((quote,index)=><li key={index}>{quote}</li>)}</ul></>}</div>{citationDetails[citation.chunk_id].source_url&&<a href={citationDetails[citation.chunk_id].source_url!} target="_blank" rel="noreferrer">打开原始来源 ↗</a>}</div>}</div>)}</details>}
              </>}
            </article>)}</div>
          </div>}
        </section>
        {c.status && <p className={`agent-chat-status ${c.state === "error" ? "is-error" : ""}`} role={c.state === "error" ? "alert" : "status"}>{c.status}</p>}
        <form ref={composerForm} className="agent-composer" onSubmit={c.send}>
          <label htmlFor="agent-draft">{t("输入消息")}</label>
          {slashItems.length>0&&<ul className="skill-picker" id="skill-picker" role="listbox" aria-label={t("技能")}>{slashItems.map((skill,i)=><li key={skill.id} id={`skill-option-${i}`} role="option" aria-selected={i===Math.min(pickIndex,slashItems.length-1)} onMouseDown={e=>{e.preventDefault();pickSkill(skill)}}><strong>/{skill.name}</strong><small>{skill.description}</small></li>)}</ul>}
          <textarea id="agent-draft" ref={draftBox} {...(slashItems.length>0?{role:"combobox","aria-expanded":true,"aria-controls":"skill-picker","aria-activedescendant":`skill-option-${Math.min(pickIndex,slashItems.length-1)}`}:{})} rows={1} value={c.draft} onChange={e => c.setDraft(e.target.value)} onKeyDown={onComposerKey} maxLength={4000} disabled={Boolean(c.archiveView)||c.restoring||!c.verified || c.state === "sending" || c.state === "testing"} placeholder={bot?`${t("给")} ${bot.name} ${t("发消息…")}`:t(c.verified?"给 Agent 发消息…":"登录个人聊天后才能对话")} />
          <div className="agent-composer-bottom">
            <span className="agent-composer-hint">{t("Enter 发送，Shift+Enter 换行")} · {t("模型和 key 只在服务器上，浏览器不保存 key。")}</span>
            <div className="agent-composer-tools">
              <ContextMeter conversationId={c.conversationId} refreshKey={`${c.messages.length}:${c.state==="sending"?"sending":"settled"}`} />
              
              {busy&&<button className="agent-stop" type="button" onClick={c.cancel}>{t("取消请求")}</button>}
              <button className="agent-send" type="submit" aria-label={c.state === "sending" ? t("等待回复…") : t("发送")} title={t("发送")} disabled={sendDisabled}><SendIcon /></button>
            </div>
          </div>
        </form>
      </div>
      {side}
    </div>
  </main>;
}
function ChatArchiveSettings(){
 const {t}=useLocale(),c=useChat(),input=useRef<HTMLInputElement>(null);
 return <section className="chat-archive-settings"><h2>{t("本地对话归档")}</h2><p>{t("归档仅供本地只读查看，不会作为模型上下文发送。")}</p><div className="chat-archive-tools"><button className="secondary-action" onClick={()=>void c.connectFile()}>{t("连接自动保存文件")}</button>{c.fileName&&<><span>{c.fileName}</span><button className="secondary-action" onClick={c.disconnectFile}>{t("断开")}</button></>}<button className="secondary-action" onClick={c.exportArchive}>{t("导出 JSON")}</button><button className="secondary-action" onClick={()=>input.current?.click()}>{t("导入查看")}</button><input ref={input} className="visually-hidden" type="file" accept="application/json,.json" onChange={e=>{const file=e.target.files?.[0];if(file)void c.importArchive(file);e.target.value=""}}/>{c.archiveStatus&&<span role="status">{c.archiveStatus}</span>}</div>{c.archiveView&&<div className="chat-archive-banner"><span>{t("本地归档只读查看；这些内容不会发送给模型，也不是已核验的服务端证据。")}</span><button className="secondary-action" onClick={c.returnLive}>{t("返回当前对话")}</button><label>{t("选择归档对话")}<select value={c.archiveView.id} onChange={e=>c.selectArchive(e.target.value)}>{(c.archive?.conversations??[]).map(item=><option key={item.id} value={item.id}>{new Date(item.updated_at).toLocaleString()} · {item.messages.length}</option>)}</select></label></div>}</section>;
}

export function AgentAdminPage({ onBack }: { onBack: () => void }) {
 const {t}=useLocale();const [knowledgeTab,setKnowledgeTab]=useState(false);
 if(knowledgeTab)return <KnowledgeAdminPage onBack={()=>setKnowledgeTab(false)}/>;
 // UX batch 3: models are configured only in the server catalog (ModelCatalogCard); the browser model profiles,
 // their import/export and the separate 后台任务模型 card are gone (tasks use the bot's catalog model or the default).
 return <main className="workspace agent-admin" aria-labelledby="agent-admin-title"><header className="workspace-heading"><div><p className="eyebrow">FinBot / {t("后台设置")}</p><h1 id="agent-admin-title">{t("模型连接设置")}</h1></div><div className="agent-header-actions"><button type="button" className="secondary-action" onClick={()=>setKnowledgeTab(true)}>研究知识库</button><button type="button" className="secondary-action" onClick={onBack}>{t("返回 Agent 对话")}</button></div></header>
  {getChatMode()!=="personal"&&<section className="agent-settings-card"><p>{t("登录个人聊天后才能配置模型：对话、bot 和定时任务都用服务器模型目录里的模型。")}</p></section>}
  <ModelCatalogCard/><MaterialAnalysisDefaultCard/><ChatArchiveSettings/><TelegramBindCard/></main>;
}

function MaterialAnalysisDefaultCard() {
  const { t } = useLocale();
  const [settings, setSettings] = useState<MaterialAnalysisSettings | null>(null);
  const [models, setModels] = useState<CatalogModel[]>([]);
  const [selected, setSelected] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const personal = getChatMode() === "personal";
  useEffect(() => {
    let cancelled = false;
    getMaterialAnalysisSettings()
      .then((value) => { if (!cancelled) setSettings(value); })
      .catch((error: unknown) => { if (!cancelled) setStatus(error instanceof Error ? error.message : t("无法读取材料分析默认模型。")); });
    if (personal) getModelCatalog()
      .then((value) => { if (!cancelled) setModels(value.models.filter((item) => item.key_status === "configured")); })
      .catch(() => { /* the catalog card above reports it */ });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    if (settings?.source !== "settings") { setSelected(""); return; }
    const match = models.find((item) => item.provider === settings.provider && item.model === settings.model && (item.base_url || "") === (settings.base_url || ""));
    setSelected(match?.name ?? "");
  }, [settings, models]);
  if (!personal) return null;
  const current = settings?.source === "settings"
    ? `${settings.provider} · ${settings.model}`
    : `${t("系统 DeepSeek")} · ${settings?.model || "…"}`;
  async function apply() {
    setBusy(true);
    setStatus("");
    try {
      const next = selected ? await saveMaterialAnalysisSettings({ catalog_model: selected }) : await resetMaterialAnalysisSettings();
      setSettings(next);
      setStatus(t("已保存。下一次材料分析将使用这个默认模型。"));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : t("保存失败，请重试。"));
    } finally {
      setBusy(false);
    }
  }
  return <section className="agent-settings-card" aria-labelledby="material-analysis-default-title">
    <h2 id="material-analysis-default-title">{t("材料分析默认模型")}</h2>
    <p>{t("AI 材料分析默认使用这里选的目录模型；在材料详情页也可以只针对一次分析临时选择其他模型。")}</p>
    <p><strong>{t("当前默认")}：</strong>{current}{settings?.has_api_key ? ` · ${t("API key 已保存")}${settings.api_key_last4 ? `（…${settings.api_key_last4}）` : ""}` : ` · ${t("使用服务端环境变量")}`}</p>
    <label>{t("默认模型")}<select value={selected} onChange={(event) => { setSelected(event.target.value); setStatus(""); }} disabled={busy}>
      <option value="">{t("系统默认（DeepSeek 环境配置）")}</option>
      {models.map((item) => <option key={item.name} value={item.name}>{item.display_name || item.name} · {item.model}</option>)}
    </select></label>
    <div className="chat-archive-tools">
      <button type="button" className="primary-action" disabled={busy} onClick={() => void apply()}>{busy ? t("正在保存…") : selected ? t("设为默认") : t("恢复系统默认")}</button>
    </div>
    {models.length === 0 && <small>{t("模型目录里还没有可用的模型；请先在上方添加并测试一个。")}</small>}
    {status && <p role="status">{status}</p>}
  </section>;
}

function TelegramBindCard() {
  const { t } = useLocale();
  const [tg, setTg] = useState<TelegramBindStatus | null>(null);
  const [token, setToken] = useState("");
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    getTelegramBind()
      .then((value) => { if (!cancelled) setTg(value); })
      .catch((error: unknown) => { if (!cancelled) setStatus(error instanceof Error ? error.message : t("Telegram 设置暂时无法读取。")); });
    return () => { cancelled = true; };
  }, []);
  if (getChatMode() !== "personal") return null;

  async function saveToken() {
    if (!token.trim()) return;
    setBusy(true);
    setStatus("");
    try {
      const next = await saveTelegramToken(token.trim());
      setTg(next);
      setToken("");
      setStatus(t("Bot Token 已保存。"));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : t("保存失败，请重试。"));
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    setBusy(true);
    setStatus("");
    try {
      const next = await startTelegramBind();
      setCode(next.code);
      setStatus(t("绑定码已生成，30 分钟内有效。"));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : t("操作失败，请重试。"));
    } finally {
      setBusy(false);
    }
  }
  async function doUnbind() {
    if (!window.confirm(t("解除绑定后，Telegram 消息不再被处理。确定解除？"))) return;
    setBusy(true);
    setStatus("");
    try {
      const next = await unbindTelegram();
      setTg(next);
      setCode("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : t("操作失败，请重试。"));
    } finally {
      setBusy(false);
    }
  }

  let summary = t("尚未配置");
  if (tg?.configured && tg.bound) {
    summary = `${t("已绑定")}${tg.bot_username ? ` @${tg.bot_username}` : ""}${tg.chat_id ? ` (chat ${tg.chat_id})` : ""}`;
  } else if (tg?.configured && tg.has_token) {
    summary = `${t("Token 已保存")}${tg.bot_username ? ` @${tg.bot_username}` : ""}${tg.bind_code_active ? ` · ${t("正在等待绑定")}` : ""}`;
  }

  return <section className="agent-settings-card" aria-labelledby="telegram-bind-title">
    <h2 id="telegram-bind-title">{t("Telegram 随身入口")}</h2>
    <p>{t("绑定 Telegram 账号后：直接发的消息进默认 bot「助理」的那条线，回复哪个 bot 的消息就进哪个 bot，也可以用「@名字」点名。bot 按自己的说明调用「发 Telegram 通知」，每个 bot 每天有上限（在 bot 档案里设）。确认卡变成按钮。")}</p>
    <p><strong>{t("状态")}：</strong>{summary}</p>
    {(!tg?.configured || !tg.has_token) && <><label>{t("Bot Token（从 @BotFather 获取）")}<input type="password" value={token} maxLength={256} placeholder="1234567890:AA..." onChange={e => setToken(e.target.value)} /></label>
    <div className="chat-archive-tools"><button type="button" className="primary-action" disabled={busy || !token.trim()} onClick={() => void saveToken()}>{busy ? t("正在保存…") : t("保存 Token")}</button></div></>}
    {tg?.configured && tg.has_token && !tg.bound && <><p>{t("请先生成一个绑定码，然后在 Telegram 上向你的 Bot 发送 /bind 命令加这个码。")}</p>
    {code && <p className="bind-code">{t("绑定码")}: <code>{code}</code><br/>{t("在 Telegram 上发送")}: <code>/bind {code}</code></p>}
    <div className="chat-archive-tools"><button type="button" className="primary-action" disabled={busy} onClick={() => void start()}>{busy ? t("正在生成…") : code ? t("重新生成") : t("生成绑定码")}</button></div></>}
    {tg?.configured && tg.bound && <div className="chat-archive-tools"><button type="button" className="secondary-action" disabled={busy} onClick={() => void doUnbind()}>{t("解除绑定")}</button></div>}
    {status && <p role="status">{status}</p>}
  </section>;
}

function friendlyError(message: string, t: (text: string) => string): string {
  const code = message.replaceAll('"', ""); const labels: Record<string, string> = { model_not_configured:"模型目录是空的：先在设置里添加一个模型。", model_key_missing:"服务器上没有找到这个 key：检查环境变量名，或重新填写 key。", model_not_found:"模型不存在。", model_discovery_invalid_key:"API key 长度应为 8 到 2048 个字符。", model_discovery_auth_required:"请填写此服务商的 API key 后重试。", model_discovery_access_denied:"当前 API key 无权读取模型列表。", model_discovery_rate_limited:"模型目录请求被限流，请稍后重试。", model_discovery_timeout:"获取模型列表超时，请检查服务状态。", model_discovery_redirect_rejected:"服务端点拒绝了目录重定向请求。", model_discovery_response_too_large:"服务商模型目录超过 8 MB 安全上限。", model_discovery_unavailable:"暂时无法读取服务商模型列表。", model_discovery_network_error:"获取模型列表时网络连接失败。", model_discovery_incompatible_response:"服务商返回的模型目录格式不兼容。", model_discovery_capacity_reached:"当前目录请求较多，请稍后重试。", provider_invalid_key: "API key 无效，请检查后重新测试。", provider_model_access_denied: "模型不可用或当前 key 无权访问。", provider_rate_or_quota_error: "额度不足或请求被限流，请稍后检查账户。", provider_timeout: "请求超时，请检查服务状态后重试。", provider_redirect_rejected: "服务端点拒绝了重定向请求。", provider_response_too_large: "服务响应超过 1 MB 安全上限。", provider_error: "模型服务暂不可用，请稍后重试。", provider_network_error: "网络连接失败，请稍后重试。", provider_incompatible_response: "服务返回格式不兼容。", provider_empty_or_invalid_answer: "服务未返回有效回答。", provider_model_unavailable_or_invalid_request: "模型不可用或请求参数不受支持。", chat_capacity_reached: "当前对话请求较多，请稍后重试。" };
  return t(labels[code] ?? message);
}

/** The system head of a routine task's result: a small label above the answer, never the owner's bubble. */
function TaskPostHead({ content }: { content: string }) {
  const { t } = useLocale();
  const head = parseTaskPostHead(content);
  return <p className="task-post-head">{head.name ? <>{t("例行任务")}「{head.name}」{head.time ? ` · ${head.time}` : ""}</> : head.raw}</p>;
}
