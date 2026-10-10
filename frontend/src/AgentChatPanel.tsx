import { createContext, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { agentChatRequest, agentChatStream, restoreAgentChat, clearAgentConversation, saveOwnerAccessKey, type AgentChatConfig, type AgentChatMessage, type AgentChatProvider, type AgentChatEvent } from "./api";
import { Tx, useLocale } from "./i18n";
import { AgentChatLifecycle, chatContext } from "./agentChatLifecycle.js";

type State = "idle" | "testing" | "ready" | "sending" | "error";
const SAVED = "market-evidence-agent:chat-settings:v1";
function savedSettings(): { provider: AgentChatProvider; model: string } { try { const p = JSON.parse(localStorage.getItem(SAVED) ?? "{}"); return { provider: p.provider === "openrouter" ? "openrouter" : "deepseek", model: typeof p.model === "string" ? p.model : "" }; } catch { return { provider: "deepseek", model: "" }; } }
type ChatSession = { provider: AgentChatProvider; model: string; key: string; verified: boolean; state: State; status: string; messages: AgentChatMessage[]; draft: string; setProvider: (v: AgentChatProvider) => void; setModel: (v: string) => void; setKey: (v: string) => void; setDraft: (v: string) => void; test: () => Promise<void>; verifyModel: (v: string) => Promise<void>; send: (e: FormEvent) => Promise<void>; cancel: () => void; clear: () => void };
const Context = createContext<ChatSession | null>(null);
export function AgentChatProviderRoot({ children }: { children: ReactNode }) {
  const { t } = useLocale();
  const saved = useMemo(savedSettings, []), [provider, setProviderRaw] = useState(saved.provider), [model, setModelRaw] = useState(saved.model), [key, setKeyRaw] = useState("");
  const [verified, setVerified] = useState(false), [state, setState] = useState<State>("idle"), [status, setStatus] = useState(""), [messages, setMessages] = useState<AgentChatMessage[]>([]), [draft, setDraft] = useState(""), [conversationId, setConversationId] = useState<string | null>(null);
  const lifecycle = useRef(new AgentChatLifecycle());
  useEffect(() => () => lifecycle.current.invalidate(), []);
  useEffect(() => { const controller=new AbortController(), id=lifecycle.current.begin(controller); setConversationId(window.sessionStorage.getItem("market-evidence-agent:chat-conversation:v1")); void restoreAgentChat().then(saved => { if (lifecycle.current.isCurrent(id)) { if(!saved.conversationId) clearAgentConversation(); setConversationId(saved.conversationId); setMessages(saved.messages.filter(m => m.role === "user" || m.role === "assistant")); } }).catch(() => { if (lifecycle.current.isCurrent(id)) setStatus(t("历史记录暂时无法加载；可稍后重试。")); }); return () => { controller.abort(); lifecycle.current.finish(id); }; }, []);
  const markSendingTurnCancelled = () => setMessages(items => {
    const last = items.at(-1);
    if (last?.role === "assistant") return [...items.slice(0, -1), { ...last, status: "cancelled", tool_trace: last.tool_trace?.map(x => x.status === "running" ? { ...x, status: "cancelled" } : x) }];
    if (last?.role === "user") return [...items, { role: "assistant", content: "", status: "cancelled" }];
    return items;
  });
  const invalidateSettings = () => { const wasSending = state === "sending"; lifecycle.current.invalidate(); if (wasSending) markSendingTurnCancelled(); setVerified(false); setState("idle"); setStatus(""); };
  const setProvider = (v: AgentChatProvider) => { if (v !== provider) { invalidateSettings(); setProviderRaw(v); } };
  const setModel = (v: string) => { if (v !== model) { invalidateSettings(); setModelRaw(v); } };
  const setKey = (v: string) => { if (v !== key) { invalidateSettings(); setKeyRaw(v); } };
  const config: AgentChatConfig = { provider, model: model.trim(), api_key: key };
  async function verifyModel(candidate: string) {
    const candidateModel = candidate.trim();
    if (!key || !candidateModel || state === "testing" || state === "sending") return;
    const tested = { ...config, model: candidateModel }, hadVerified = verified, controller = new AbortController(), id = lifecycle.current.begin(controller); setState("testing"); setStatus("");
    try { const result = await agentChatRequest("hello", tested, [], controller.signal); if (!lifecycle.current.isCurrent(id)) return; setModelRaw(candidateModel); setVerified(true); setState("ready"); setStatus(`${translate("连接测试成功")} · ${result.content}`); try { localStorage.setItem(SAVED, JSON.stringify({ provider: tested.provider, model: tested.model })); } catch { /* optional preference */ } }
    catch (error) { if (!lifecycle.current.isCurrent(id)) return; setVerified(hadVerified); setState("error"); setStatus(error instanceof Error ? friendlyError(error.message, translate) : translate("连接失败，请重试")); }
    finally { lifecycle.current.finish(id); }
  }
  const test = () => verifyModel(model);
  function translate(s: string) { return t(s); }
  async function send(e: FormEvent) {
    e.preventDefault(); const content = draft.trim(); if (!verified || !content || state === "sending" || state === "testing") return;
    let context: AgentChatMessage[]; try { context = chatContext(messages, { role: "user", content }); } catch (error) { setStatus(t(error instanceof Error ? error.message : "消息过长，请缩短后重试。")); return; }
    const next = [...messages, { role: "user" as const, content }]; setMessages(next); setDraft(""); setState("sending"); setStatus(""); const tested = { ...config }, controller = new AbortController(), id = lifecycle.current.begin(controller);
    let accumulated = ""; const tools: NonNullable<AgentChatMessage["tool_trace"]> = [];
    try {
      await agentChatStream(tested, content, conversationId, controller.signal, (event: AgentChatEvent) => {
        if (!lifecycle.current.isCurrent(id)) return false;
        if (event.event === "conversation" && typeof event.data.conversation_id === "string") setConversationId(event.data.conversation_id);
        if (event.event === "status") setStatus(t(String(event.data.status === "writing_answer" ? "正在组织回答…" : "正在选择研究工具…")));
        if (event.event === "tool_start") { tools.push({ call_id: String(event.data.call_id), name: String(event.data.name), arguments: event.data.arguments, status: "running" }); setStatus(t("正在查询研究数据…")); }
        if (event.event === "tool_result") { const item = tools.find(x => x.call_id === event.data.call_id); if (item) { item.result = event.data.result; const result = event.data.result as { ok?: boolean; data?: unknown };
          item.status = !result?.ok ? "failed" : Array.isArray(result.data) && result.data.length === 0 ? "not_found" : "complete"; } }
        if (event.event === "delta" && typeof event.data.text === "string") accumulated += event.data.text;
        if (event.event === "error") throw new Error(String(event.data.code ?? "chat_failed"));
        setMessages([...next, { role: "assistant", content: accumulated, tool_trace: [...tools] }]);
        return true;
      });
      if (!lifecycle.current.isCurrent(id)) return; setMessages([...next, { role: "assistant", content: accumulated, tool_trace: [...tools] }]); setStatus(""); setState("ready");
    }
    catch (error) { if (!lifecycle.current.isCurrent(id)) return; const stopped=error instanceof DOMException && error.name==="AbortError"; const stoppedTools=tools.map(x => x.status === "running" ? { ...x, status: "cancelled" } : x); setMessages([...next, { role:"assistant",content:accumulated,status:stopped?"cancelled":"failed",tool_trace:stoppedTools }]); setState(stopped?"ready":"error"); setStatus(stopped?translate("请求已取消；供应商可能仍会计费。"):error instanceof Error ? friendlyError(error.message, translate) : translate("发送失败，请重试")); }
    finally { lifecycle.current.finish(id); }
  }
  const cancel = () => { const wasSending = state === "sending"; lifecycle.current.invalidate(); if (wasSending) markSendingTurnCancelled(); setState(verified ? "ready" : "idle"); setStatus(translate("请求已取消；供应商可能仍会计费。")); };
  const clear = () => { lifecycle.current.invalidate(); clearAgentConversation(); setConversationId(null); setMessages([]); setDraft(""); setState(verified ? "ready" : "idle"); setStatus(""); };
  return <Context.Provider value={{ provider, model, key, verified, state, status, messages, draft, setProvider, setModel, setKey, setDraft, test, verifyModel, send, cancel, clear }}>{children}</Context.Provider>;
}
function useChat() { const value = useContext(Context); if (!value) throw new Error("Agent chat provider is missing"); return value; }

export function AgentChatPanel({ onSettings }: { onSettings: () => void }) {
  const { t } = useLocale(), c = useChat(), transcriptRef = useRef<HTMLElement>(null);
  const [modelChoice, setModelChoice] = useState(c.model);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  useEffect(() => setModelChoice(c.model), [c.model]);
  useEffect(() => {
    const transcript = transcriptRef.current;
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }, [c.messages, c.state]);
  return <main className="workspace agent-page" aria-labelledby="agent-chat-title">
    <header className="workspace-heading agent-page-heading"><div><p className="eyebrow">Market Evidence Agent / {t("研究工具对话")}</p><h1 id="agent-chat-title">{t("Agent 对话")}</h1></div><button className="secondary-action" onClick={onSettings} type="button">{t("模型设置")}</button></header>
    <div className="agent-capability"><span aria-hidden="true">✳</span><p>{t("消息和研究工具轨迹会保存到服务端；工具只读取已保存的研究数据。")}</p><span className={`agent-connection ${c.verified ? "is-ready" : ""}`}>{c.verified ? `${c.provider} · ${c.model}` : t("尚未验证模型")}</span></div>
    <section ref={transcriptRef} className="agent-transcript" aria-live="polite">{c.messages.length === 0 ? <div className="agent-welcome"><p className="eyebrow">{t("研究助手")}</p><h2>{t("从一个问题开始")}</h2><p>{t(c.verified ? "连接已就绪，可以输入问题或选择下方建议。" : "请先在模型设置中填写自己的 API key 并完成连接测试。")}</p><div>{["查看 AAPL 最近行情", "AAPL 已保存的最新财报有哪些", "可信度星级是什么"].map(x => <button type="button" key={x} onClick={() => c.setDraft(t(x))}>{t(x)} <span aria-hidden="true">↗</span></button>)}</div></div> : c.messages.map((m, i) => <article className={`agent-message is-${m.role}`} key={i}><strong>{m.role === "user" ? t("你") : "Agent"}</strong><p>{m.content}</p>{m.role === "assistant" && m.status && m.status !== "complete" && <small>{t(m.status === "cancelled" ? "本轮已取消，未生成完整回答。" : "本轮失败；已保留已生成内容。")}</small>}{m.role === "assistant" && m.tool_trace?.map((tool, j) => <details className="agent-tool-trace" key={`${tool.name}-${j}`}><summary>{t(tool.name)} · {t(tool.status === "complete" ? "已完成" : tool.status === "not_found" ? "没有匹配记录" : tool.status === "failed" ? "查询失败" : tool.status === "cancelled" ? "已取消" : "执行中")}</summary><pre>{JSON.stringify({ arguments: tool.arguments, result: tool.result }, null, 2)}</pre></details>)}</article>)}</section>
    {c.status && <p className={`agent-chat-status ${c.state === "error" ? "is-error" : ""}`} role={c.state === "error" ? "alert" : "status"}>{c.status}</p>}
    <form className="agent-composer" onSubmit={c.send}><label htmlFor="agent-draft">{t("输入消息")}</label><textarea id="agent-draft" value={c.draft} onChange={e => c.setDraft(e.target.value)} maxLength={4000} disabled={!c.verified || c.state === "sending" || c.state === "testing"} placeholder={t("输入消息…")} /><div className="agent-composer-bottom"><small>{t("个人 API key 仅保存在当前页面内存中")}</small><div className="agent-composer-tools">{(c.state === "sending" || c.state === "testing") && <button className="secondary-action" type="button" onClick={c.cancel}>{t("取消请求")}</button>}<button className="secondary-action" type="button" onClick={c.clear}>{t("新建对话")}</button><div className="agent-model-switch"><button type="button" className="agent-model-trigger" aria-haspopup="dialog" aria-expanded={modelMenuOpen} onClick={() => setModelMenuOpen(open => !open)} disabled={c.state === "sending" || c.state === "testing"}>{c.model || t("选择模型")} <span aria-hidden="true">⌄</span></button>{modelMenuOpen && <div className="agent-model-popup" role="dialog" aria-label={t("选择模型")}><strong>{t("选择模型")}</strong>{(c.provider === "deepseek" ? ["deepseek-chat", "deepseek-reasoner"] : ["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet", "google/gemini-2.5-flash"]).map(choice => <button type="button" key={choice} onClick={() => { setModelChoice(choice); void c.verifyModel(choice); }}>{choice}</button>)}<label>{t("自定义模型 ID")}<input value={modelChoice} onChange={e => setModelChoice(e.target.value)} placeholder={c.provider === "deepseek" ? "deepseek-chat" : "provider/model"} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void c.verifyModel(modelChoice); } }} /></label><button type="button" className="primary-action" disabled={!modelChoice.trim() || !c.key || c.state === "testing"} onClick={() => { void c.verifyModel(modelChoice); }}>{c.state === "testing" ? t("验证中…") : t("验证并切换")}</button></div>}</div><button className="primary-action" type="submit" disabled={!c.verified || !c.draft.trim() || c.state === "sending" || c.state === "testing"}>{c.state === "sending" ? t("等待回复…") : t("发送")}</button></div></div></form>
  </main>;
}
export function AgentAdminPage({ onBack }: { onBack: () => void }) {
  const { t } = useLocale(), c = useChat();
  const [ownerKey, setOwnerKey] = useState("");
  const [ownerStatus, setOwnerStatus] = useState("");
  const [modelDraft, setModelDraft] = useState(c.model);
  useEffect(() => setModelDraft(c.model), [c.model]);
  return <main className="workspace agent-admin" aria-labelledby="agent-admin-title"><header className="workspace-heading"><div><p className="eyebrow">Market Evidence Agent / {t("后台设置")}</p><h1 id="agent-admin-title">{t("模型连接设置")}</h1></div><button type="button" className="secondary-action" onClick={onBack}>{t("返回 Agent 对话")}</button></header><section className="agent-settings-card"><p>{t("设置仅用于当前浏览器页面会话。API key 不会保存到浏览器存储或服务器；刷新页面后需要重新输入并测试。")}</p><label>{t("模型服务")}<select value={c.provider} onChange={e => c.setProvider(e.target.value as AgentChatProvider)}><option value="deepseek">DeepSeek</option><option value="openrouter">OpenRouter</option></select></label><label>Model ID<input value={modelDraft} onChange={e => setModelDraft(e.target.value)} placeholder={c.provider === "deepseek" ? "deepseek-chat" : "provider/model"} autoComplete="off" /></label><label>API key<input type="password" value={c.key} onChange={e => c.setKey(e.target.value)} autoComplete="off" placeholder={t("仅保存在当前页面会话，刷新后需重填")} /></label><div className="agent-settings-actions"><button type="button" className="primary-action" disabled={!c.key || !modelDraft.trim() || c.state === "testing" || c.state === "sending"} onClick={() => c.verifyModel(modelDraft)}>{c.state === "testing" ? t("正在发送 hello 测试…") : t("测试并启用")}</button><span className={`agent-connection ${c.verified ? "is-ready" : ""}`}>{c.verified ? `${t("连接已验证")} · ${c.provider} / ${c.model}` : t("尚未验证模型")}</span></div>{c.status && <p className={`agent-chat-status ${c.state === "error" ? "is-error" : ""}`} role={c.state === "error" ? "alert" : "status"}>{c.status}</p>}<small>{t("取消请求只会停止浏览器等待；服务方可能仍会计费。")}</small><label>{t("操作口令（可选）")}<input type="password" value={ownerKey} onChange={e => setOwnerKey(e.target.value)} autoComplete="off" placeholder={t("仅保存在当前浏览器会话，用于研究数据写操作")} /></label><button type="button" className="secondary-action" onClick={() => { saveOwnerAccessKey(ownerKey); setOwnerStatus(ownerKey ? "操作口令已保存到当前会话。" : "已清除当前会话中的操作口令。"); }}>{t("保存操作口令到当前会话")}</button>{ownerStatus && <p role="status">{t(ownerStatus)}</p>}</section></main>;
}

function friendlyError(message: string, t: (text: string) => string): string {
  const code = message.replaceAll('"', ""); const labels: Record<string, string> = { provider_invalid_key: "API key 无效，请检查后重新测试。", provider_model_access_denied: "模型不可用或当前 key 无权访问。", provider_rate_or_quota_error: "额度不足或请求被限流，请稍后检查账户。", provider_timeout: "请求超时，请检查服务状态后重试。", provider_redirect_rejected: "服务端点拒绝了重定向请求。", provider_error: "模型服务暂不可用，请稍后重试。", provider_network_error: "网络连接失败，请稍后重试。", provider_incompatible_response: "服务返回格式不兼容。", provider_empty_or_invalid_answer: "服务未返回有效回答。", provider_model_unavailable_or_invalid_request: "模型不可用或请求参数不受支持。", chat_capacity_reached: "当前对话请求较多，请稍后重试。" };
  return t(labels[code] ?? message);
}
