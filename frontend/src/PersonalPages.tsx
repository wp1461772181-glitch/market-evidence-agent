import { useCallback, useEffect, useState } from "react";
import { useOpenConversation } from "./AgentChatPanel";
import { confirmProfileItem, deleteProfileItem, getChatMode, listProfileItems, listRecall, type ProfileItem, type RecallItem } from "./api";
import { useLocale } from "./i18n";
import { originLabel, profileGroups, recallKindLabel } from "./memory-items.mjs";

// Pages of the personal agent.  Briefs and skills are placeholders until their phases; the memory page
// lists the owner's profile items and past conversations through the owner-only /agent-chat API.

type Go = (section: "agent" | "briefs" | "skills" | "memory" | "admin" | "materials") => void;

export function BriefsPage({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  return <main className="personal-page" aria-labelledby="briefs-title">
    <h1 id="briefs-title">{t("简报")}</h1>
    <section className="personal-card">
      <span className="personal-badge">{t("即将推出")}</span>
      <h2>{t("还没有简报")}</h2>
      <p>{t("设好主动任务后，研究队列的每日简报、新申报和新闻提醒会出现在这里，每条都带来源编号；点进去就是普通对话，可以接着追问。")}</p>
    </section>
    <section className="personal-card">
      <h2>{t("待你确认")}</h2>
      <p>{t("后台任务只读不写。它想做的写操作会以确认卡的形式放在这里，你确认后才执行。")}</p>
    </section>
    <div><button className="primary-action" type="button" onClick={() => onNavigate("agent")}>{t("去对话")}</button></div>
  </main>;
}

const BUILTIN_SKILLS = [
  { name: "财报速读", text: "读最新一份 10-K 或 10-Q，列出收入、利润、现金流的变化和管理层提到的风险，每条带出处。" },
  { name: "同行对比", text: "把几家公司同一期的关键指标放在一张表里，数字都取自本轮读取的申报。" },
  { name: "新闻摘要及对研究队列的影响", text: "汇总近期新闻，标出和研究队列里哪家公司有关、可能影响什么。" },
] as const;

export function SkillsPage() {
  const { t } = useLocale();
  return <main className="personal-page" aria-labelledby="skills-title">
    <h1 id="skills-title">{t("技能")}</h1>
    <p className="fine-print">{t("技能是一份做事的步骤和允许用的工具清单，不是代码。技能只能缩小可用的工具，写操作仍然要你在确认卡上同意。")}</p>
    <div className="personal-grid">
      {BUILTIN_SKILLS.map(skill => <article className="personal-card" key={skill.name}>
        <span className="personal-badge">{t("内置 · 即将推出")}</span>
        <h2>{t(skill.name)}</h2>
        <p>{t(skill.text)}</p>
      </article>)}
    </div>
  </main>;
}

const KIND_LABELS: Record<ProfileItem["kind"], string> = { preference: "偏好", about_me: "关于我", instruction: "长期指令" };

export function MemoryPage({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  const [tab, setTab] = useState<"profile" | "recall">("profile");
  const personal = getChatMode() === "personal";
  return <main className="personal-page" aria-labelledby="memory-title">
    <h1 id="memory-title">{t("记忆")}</h1>
    <nav className="section-tabs memory-tabs" aria-label={t("记忆")}>
      <button type="button" aria-current={tab === "profile" ? "page" : undefined} onClick={() => setTab("profile")}>{t("画像")}</button>
      <button type="button" aria-current={tab === "recall" ? "page" : undefined} onClick={() => setTab("recall")}>{t("往事")}</button>
    </nav>
    {!personal ? <section className="personal-card"><p>{t("登录个人聊天后才能查看记忆。")}</p></section>
      : tab === "profile" ? <ProfilePanel /> : <RecallPanel onNavigate={onNavigate} />}
    <div><button className="secondary-action" type="button" onClick={() => onNavigate("agent")}>{t("去对话")}</button></div>
  </main>;
}

function ProfilePanel() {
  const { t } = useLocale();
  const [items, setItems] = useState<ProfileItem[] | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState<string | null>(null);
  const load = useCallback((signal?: AbortSignal) => listProfileItems(signal).then(setItems).catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "画像暂时无法读取。"); }), []);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  async function act(id: string, action: "confirm" | "delete") {
    if (action === "delete" && !window.confirm(t("删除这条后，之后的对话不再按它回答。确定删除？"))) return;
    setBusy(id); setError("");
    try { if (action === "confirm") await confirmProfileItem(id); else await deleteProfileItem(id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败，请重试。"); }
    finally { setBusy(null); }
  }
  const groups = profileGroups(items ?? []);
  return <section className="personal-card memory-panel">
    <p>{t("对 Agent 说「记住……」「以后都……」，确认后它会在之后的每次对话里按你的偏好回答；说「忘掉……」即可撤回。Agent 也会从你自己说的话里推断偏好，标为“推断”，你可以在这里确认或删除。画像只是偏好，不是证据，也不授权任何操作。")}</p>
    {error && <p role="alert">{t(error)}</p>}
    {items === null && !error ? <p>{t("正在加载…")}</p> : groups.active.length === 0 ? <p>{t("还没有画像条目。")}</p> :
      <ul className="memory-list">{groups.active.map(item => <li key={item.id}>
        <div className="memory-line"><span className="personal-badge">{t(KIND_LABELS[item.kind] ?? item.kind)}</span>
          <span className={`personal-badge origin-${item.origin}`}>{t(originLabel(item.origin))}</span>
          <strong>{item.text}</strong></div>
        <blockquote className="memory-quote">“{item.quote}”</blockquote>
        <div className="memory-actions">
          {item.origin === "inferred" && <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void act(item.id, "confirm")}>{t("确认")}</button>}
          <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void act(item.id, "delete")}>{t("删除")}</button>
        </div>
      </li>)}</ul>}
    {groups.archived.length > 0 && <details className="memory-archived"><summary>{t("已归档")} · {groups.archived.length}</summary>
      <ul className="memory-list">{groups.archived.map(item => <li key={item.id}><div className="memory-line"><span className="personal-badge">{t(KIND_LABELS[item.kind] ?? item.kind)}</span><span>{item.text}</span></div>
        <div className="memory-actions"><button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void act(item.id, "delete")}>{t("删除")}</button></div></li>)}</ul>
    </details>}
  </section>;
}

function RecallPanel({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  const openConversation = useOpenConversation();
  const [query, setQuery] = useState(""), [submitted, setSubmitted] = useState(""), [items, setItems] = useState<RecallItem[] | null>(null), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController(); setItems(null); setError("");
    listRecall(submitted, controller.signal).then(setItems).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "往事暂时无法读取。"); });
    return () => controller.abort();
  }, [submitted]);
  async function open(item: RecallItem) { await openConversation(item.conversation_id); onNavigate("agent"); }
  return <section className="personal-card memory-panel">
    <p>{t("说「上次我们聊过……」时，Agent 会在这里找是哪一次对话、聊了什么。往事只是线索，不是证据：回答里的数字仍要重新取证。")}</p>
    <form className="memory-search" onSubmit={e => { e.preventDefault(); setSubmitted(query); }}>
      <input type="search" value={query} maxLength={200} placeholder={t("搜索往事，例如 英伟达")} aria-label={t("搜索往事")} onChange={e => setQuery(e.target.value)} />
      <button type="submit" className="secondary-action">{t("搜索")}</button>
    </form>
    {error && <p role="alert">{t(error)}</p>}
    {items === null && !error ? <p>{t("正在加载…")}</p> : items && items.length === 0 ? <p>{t(submitted ? "没有找到相关的往事。" : "还没有往事。聊过几次之后，这里会列出最近的对话摘要。")}</p> :
      <ul className="memory-list">{(items ?? []).map(item => <li key={item.conversation_id}>
        <div className="memory-line"><span className="personal-badge">{t(recallKindLabel(item.kind))}</span><strong>{item.title}</strong>{item.date && <span className="num fine-print">{item.date}</span>}</div>
        <p>{item.summary}</p>
        <div className="memory-actions"><button type="button" className="secondary-action" onClick={() => void open(item)}>{t("打开对话")}</button></div>
      </li>)}</ul>}
  </section>;
}

export function MePage({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  const links: Array<{ id: Parameters<Go>[0]; label: string; hint: string }> = [
    { id: "memory", label: "记忆", hint: "画像和往事" },
    { id: "skills", label: "技能", hint: "做事的步骤" },
    { id: "materials", label: "资料库", hint: "证据与材料" },
    { id: "admin", label: "设置", hint: "模型与账户" },
  ];
  return <main className="personal-page" aria-labelledby="me-title">
    <h1 id="me-title">{t("我")}</h1>
    <nav className="personal-links" aria-label={t("我")}>
      {links.map(link => <button key={link.id} type="button" onClick={() => onNavigate(link.id)}>{t(link.label)}<small>{t(link.hint)}</small></button>)}
    </nav>
  </main>;
}
