import { useRef, useState, type FormEvent } from "react";
import { agentChatRequest, type AgentChatConfig, type AgentChatMessage, type AgentChatProvider } from "./api";
import { Tx, useLocale } from "./i18n";
import { AgentChatLifecycle, chatContext } from "./agentChatLifecycle.js";

type State = "idle" | "testing" | "ready" | "sending" | "error";
const SAVED = "market-evidence-agent:chat-settings:v1";
function savedSettings(): { provider: AgentChatProvider; model: string } {
  try { const parsed = JSON.parse(localStorage.getItem(SAVED) ?? "{}"); return { provider: parsed.provider === "openrouter" ? "openrouter" : "deepseek", model: typeof parsed.model === "string" ? parsed.model : "" }; } catch { return { provider: "deepseek", model: "" }; }
}
export function AgentChatPanel() {
  const { t } = useLocale();
  const initial = savedSettings();
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<AgentChatProvider>(initial.provider);
  const [model, setModel] = useState(initial.model);
  const [key, setKey] = useState("");
  const [verified, setVerified] = useState(false);
  const [state, setState] = useState<State>("idle");
  const [status, setStatus] = useState("");
  const [messages, setMessages] = useState<AgentChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const lifecycle = useRef(new AgentChatLifecycle());
  const config: AgentChatConfig = { provider, model: model.trim(), api_key: key };
  function changed(fn: () => void) { lifecycle.current.invalidate(); setVerified(false); setMessages([]); setDraft(""); setState("idle"); setStatus(""); fn(); }
  async function test() {
    if (!key || !model.trim()) return;
    if (state === "testing" || state === "sending") return;
    const requestConfig = { ...config }; const controller = new AbortController(); const id = lifecycle.current.begin(controller); setState("testing"); setStatus("");
    try {
      const result = await agentChatRequest("hello", requestConfig, [], controller.signal);
      if (!lifecycle.current.isCurrent(id)) return;
      setVerified(true); setState("ready"); setStatus(`${t("连接测试成功")} · ${result.content}`);
      try { localStorage.setItem(SAVED, JSON.stringify({ provider, model: model.trim() })); } catch { /* optional */ }
    } catch (error) { if (!lifecycle.current.isCurrent(id)) return; setVerified(false); setState("error"); setStatus(error instanceof Error ? friendlyError(error.message, t) : t("连接失败，请重试")); }
    finally { lifecycle.current.finish(id); }
  }
  async function send(event: FormEvent) {
    event.preventDefault(); const content = draft.trim(); if (!verified || !content || state === "sending" || state === "testing") return;
    let context: AgentChatMessage[];
    try { context = chatContext(messages, { role: "user", content }); }
    catch (error) { setStatus(error instanceof Error ? t(error.message) : t("消息过长，请缩短后重试。")); return; }
    const next = [...messages, { role: "user" as const, content }]; setMessages(next); setDraft(""); setState("sending"); setStatus("");
    const requestConfig = { ...config }; const controller = new AbortController(); const id = lifecycle.current.begin(controller);
    try { const result = await agentChatRequest("messages", requestConfig, context, controller.signal); if (!lifecycle.current.isCurrent(id)) return; setMessages([...next, { role: "assistant", content: result.content }]); setState("ready"); }
    catch (error) { if (!lifecycle.current.isCurrent(id)) return; setState("error"); setStatus(error instanceof Error ? friendlyError(error.message, t) : t("发送失败，请重试")); }
    finally { lifecycle.current.finish(id); }
  }
  return <>
    <button className="agent-chat-launch" onClick={() => { lifecycle.current.invalidate(); setVerified(false); setState("idle"); setStatus(""); setOpen(true); }} type="button"><span aria-hidden="true">✳</span> <Tx text="Agent 对话" /></button>
    {open && <div className="media-dialog-backdrop agent-chat-backdrop" role="presentation"><section className="media-dialog agent-chat-panel" role="dialog" aria-modal="true" aria-labelledby="agent-chat-title">
      <header className="media-dialog-header"><div><p className="section-label">Market Evidence Agent</p><h3 id="agent-chat-title"><Tx text="Agent 对话" /></h3><p><Tx text="通用模型对话；不会读取项目材料库、预测或调用工具。" /></p></div><button className="media-dialog-close" onClick={() => { lifecycle.current.invalidate(); setVerified(false); setMessages([]); setDraft(""); setState("idle"); setStatus(""); setOpen(false); }} aria-label={t("关闭")}>×</button></header>
      <div className="agent-chat-body">
        <div className="agent-chat-config"><label><Tx text="模型服务" /><select value={provider} onChange={e => changed(() => setProvider(e.target.value as AgentChatProvider))}><option value="deepseek">DeepSeek</option><option value="openrouter">OpenRouter</option></select></label>
          <label><Tx text="Model ID" /><input value={model} onChange={e => changed(() => setModel(e.target.value))} placeholder={provider === "deepseek" ? "deepseek-chat" : "provider/model"} autoComplete="off" /></label>
          <label><Tx text="API key" /><input type="password" value={key} onChange={e => changed(() => setKey(e.target.value))} autoComplete="off" placeholder={t("仅保存在当前页面内存，刷新后需重填")} /></label>
          <button className="primary-action" disabled={!key || !model.trim() || state === "testing" || state === "sending"} onClick={test} type="button">{state === "testing" ? t("正在发送 hello 测试…") : t("测试并启用")}</button>
          {status && <p className={`agent-chat-status ${state === "error" ? "is-error" : ""}`} role="status">{status}</p>}
        </div>
        <div className="agent-chat-transcript" aria-live="polite">{messages.length === 0 ? <p className="agent-chat-empty"><Tx text="通过 hello 测试后即可开始对话。" /></p> : messages.map((message, index) => <article className={`agent-chat-message is-${message.role}`} key={index}><strong>{message.role === "user" ? t("你") : "Agent"}</strong><p>{message.content}</p></article>)}</div>
        <form className="agent-chat-compose" onSubmit={send}><textarea value={draft} onChange={e => setDraft(e.target.value)} maxLength={4000} disabled={!verified || state === "sending" || state === "testing"} placeholder={t("输入消息…")} /><div><button className="secondary-action" type="button" disabled={state !== "sending" && state !== "testing"} onClick={() => { lifecycle.current.invalidate(); setMessages(items => items.filter((item, i) => !(i === items.length - 1 && item.role === "user"))); setState(verified ? "ready" : "idle"); setStatus(t("请求已取消；供应商可能仍会计费。")); }}>{t("取消请求")}</button><button className="secondary-action" type="button" onClick={() => { lifecycle.current.invalidate(); setMessages([]); setVerified(false); setState("idle"); setStatus(""); }}>{t("清空对话")}</button><button className="primary-action" type="submit" disabled={!verified || !draft.trim() || state === "sending" || state === "testing"}>{state === "sending" ? t("等待回复…") : t("发送")}</button></div></form>
      </div>
    </section></div>}
  </>;
}

function friendlyError(message: string, t: (text: string) => string): string {
  const code = message.replaceAll('"', "");
  const labels: Record<string, string> = {
    provider_invalid_key: "API key 无效，请检查后重新测试。",
    provider_model_access_denied: "模型不可用或当前 key 无权访问。",
    provider_rate_or_quota_error: "额度不足或请求被限流，请稍后检查账户。",
    provider_timeout: "请求超时，请检查服务状态后重试。",
    provider_redirect_rejected: "服务端点拒绝了重定向请求。",
    provider_error: "模型服务暂不可用，请稍后重试。",
    provider_network_error: "网络连接失败，请稍后重试。",
    provider_incompatible_response: "服务返回格式不兼容。",
    provider_empty_or_invalid_answer: "服务未返回有效回答。",
    provider_model_unavailable_or_invalid_request: "模型不可用或请求参数不受支持。",
    chat_capacity_reached: "当前对话请求较多，请稍后重试。",
  };
  return t(labels[code] ?? message);
}
