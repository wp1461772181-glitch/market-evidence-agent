import { useCallback, useEffect, useState } from "react";
import { useOpenConversation } from "./AgentChatPanel";
import { confirmInboxItem, confirmProfileItem, createProfileItem, deleteProfileItem, getChatMode, getNewsSourcesConfig, listBots, listInbox, listProfileItems, listRecall, listTaskProposals, mintInboxCard, saveNewsPushSettings, setInboxStatus, updateProfileItem, type Bot, type InboxItem, type ProfileBot, type ProfileItem, type RecallItem, type TaskProposal } from "./api";
import { RoutineIcon } from "./BotPages";
import { TaskManager } from "./BotRoutines";
import { useLocale } from "./i18n";
export { SkillsPage } from "./SkillsPage";
import { inboxOrder } from "./inbox-items.mjs";
import { layerLabel, manualItemDraft, originLabel, profileGroups, profileLayers, recallGroups, recallKindLabel } from "./memory-items.mjs";

// Pages of the personal agent (the skills page lives in SkillsPage.tsx).  The 例行任务 page (#routines; #briefs, its
// old 待你确认 address, still opens it) holds every bot's routines, the routine cards a bot proposed that wait for the
// owner's press, and the writes unattended runs suggested; the memory page lists profile items and past conversations.
// All through the owner-only /agent-chat API.

type Go = (section: "agent" | "routines" | "skills" | "memory" | "admin" | "materials") => void;

export function RoutinesPage({ bots, onNavigate, onChanged }: { bots: Bot[]; onNavigate: Go; onChanged?: () => void }) {
  const { t } = useLocale();
  const openConversation = useOpenConversation();
  const [proposals, setProposals] = useState<TaskProposal[] | null>(null);
  const [confirms, setConfirms] = useState<InboxItem[] | null>(null);
  const [summaryMaxTokens, setSummaryMaxTokens] = useState<number>(256);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const personal = getChatMode() === "personal";

  const load = useCallback((signal?: AbortSignal) => {
    const fail = (e: unknown) => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "任务暂时无法读取。"); };
    listTaskProposals(signal).then(setProposals).catch(fail);
    listInbox("confirm", signal).then(setConfirms).catch(fail);
    getNewsSourcesConfig(signal).then(cfg => {
      if (cfg?.push?.summary_max_tokens) setSummaryMaxTokens(cfg.push.summary_max_tokens);
    }).catch(() => { /* optional */ });
  }, []);
  useEffect(() => {
    if (!personal) return;
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [personal, load]);

  // One busy marker and one error line for every action of the page.
  async function act(id: string, action: () => Promise<unknown>) {
    setBusy(id); setError("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败，请重试。"); } finally { setBusy(null); }
  }
  const dismiss = (id: string) => act(id, async () => { await setInboxStatus(id, "dismissed"); load(); onChanged?.(); });
  const inlineConfirm = (item: InboxItem, action: "confirm" | "skip" | "later") => act(item.id, async () => {
    await confirmInboxItem(item.id, action, item.action_token);
    load();
    onChanged?.();
  });
  const updateSummaryMaxTokens = (val: number) => act("push-tokens", async () => {
    const res = await saveNewsPushSettings(val);
    setSummaryMaxTokens(res.push.summary_max_tokens);
  });
  // The card is minted now, with its own ten-minute capability, in the conversation of the run.
  const mintCard = (item: InboxItem) => act(item.id, async () => {
    const minted = await mintInboxCard(item.id);
    await openConversation(minted.conversation_id);
    onNavigate("agent");
  });
  // A proposed routine card waits in the conversation it was made in: open that conversation to press it.
  const openProposal = (proposal: TaskProposal) => act(proposal.id, async () => {
    await openConversation(proposal.conversation_id);
    onNavigate("agent");
  });
  const botLabel = (id: string | null) => bots.find(b => b.id === id)?.name ?? (bots.length ? t("已删除的 bot") : "");
  const waiting = inboxOrder(confirms ?? []);

  if (!personal) return <main className="personal-page" aria-labelledby="routines-title">
    <h1 id="routines-title"><RoutineIcon /> {t("例行任务")}</h1>
    <section className="personal-card"><p>{t("登录个人聊天后才能查看和管理例行任务。")}</p></section>
    <div><button className="primary-action" type="button" onClick={() => onNavigate("agent")}>{t("去对话")}</button></div>
  </main>;

  return <main className="personal-page" aria-labelledby="routines-title">
    <h1 id="routines-title"><RoutineIcon /> {t("例行任务")}</h1>
    {error && <p role="alert">{error}</p>}
    <section className="personal-card memory-panel" aria-labelledby="routine-proposals-title">
      <h3 id="routine-proposals-title">{t("bot 提议、等你确认")}</h3>
      <p className="fine-print">{t("bot 在对话里提出的新建、修改或删除例行任务，要你在确认卡上点「同意」才生效。确认卡十分钟内有效；过期了在对话里再说一次。")}</p>
      {proposals === null && !error ? <p>{t("正在加载…")}</p> : (proposals ?? []).length === 0 ? <p>{t("没有等你确认的例行任务提议。")}</p> :
        <ul className="memory-list">{(proposals ?? []).map(proposal => <li key={proposal.id}>
          <div className="memory-line">{botLabel(proposal.bot_id) && <span className="personal-badge">{botLabel(proposal.bot_id)}</span>}<strong>{proposal.title}</strong>
            {proposal.expired && <span className="personal-badge">{t("已过期")}</span>}</div>
          {proposal.targets.map((target, index) => <p key={index} className="fine-print">{Object.values(target).join(" · ")}</p>)}
          {proposal.detail && <blockquote className="memory-quote">{proposal.detail}</blockquote>}
          <div className="memory-actions">
            <button type="button" className="secondary-action" disabled={busy === proposal.id} onClick={() => void openProposal(proposal)}>{t(proposal.expired ? "打开对话" : "去对话确认")}</button>
          </div>
        </li>)}</ul>}
    </section>
    <section className="personal-card memory-panel">
      <div className="memory-line">
        <label className="memory-scope">新闻推送摘要长度上限
          <select value={summaryMaxTokens} disabled={busy === "push-tokens"} onChange={e => void updateSummaryMaxTokens(Number(e.target.value))}>
            <option value={128}>短（128 tokens）</option>
            <option value={256}>中（256 tokens）</option>
            <option value={512}>长（512 tokens）</option>
          </select>
        </label>
      </div>
      <TaskManager bots={bots} onChanged={() => { load(); onChanged?.(); }} />
    </section>
    {waiting.length > 0 && <section className="personal-card memory-panel" aria-labelledby="routine-confirms-title">
      <h3 id="routine-confirms-title">{t("例行任务运行时想做的写操作")}</h3>
      <p className="fine-print">{t("定时任务在无人值守时只读不写。它想做的写操作会放在这里，不会自动执行；点「生成确认卡」后到对话里核对，点「同意」才执行。")}</p>
      <ul className="memory-list">{waiting.map(item => <li key={item.id}>
        <div className="memory-line"><span className="personal-badge">{t("Agent 的建议")}</span>{botLabel(item.bot_id) && <span className="personal-badge">{botLabel(item.bot_id)}</span>}<strong>{item.title}</strong>
          {item.confirmation_state && <span className="personal-badge">{item.confirmation_state}</span>}</div>
        {item.body && <blockquote className="memory-quote">“{item.body}”</blockquote>}
        {item.payload.task_name && <p className="fine-print">{t("来自任务：")}{item.payload.task_name}</p>}
        <div className="memory-actions">
          <button type="button" className="primary-action" disabled={busy === item.id || item.confirmation_state === "confirmed"} onClick={() => void inlineConfirm(item, "confirm")}>{t("确认")}</button>
          <button type="button" className="secondary-action" disabled={busy === item.id || item.confirmation_state === "skipped"} onClick={() => void inlineConfirm(item, "skip")}>跳过</button>
          <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void inlineConfirm(item, "later")}>稍后</button>
          <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void mintCard(item)}>{t("生成确认卡")}</button>
          {item.conversation_id && <a className="secondary-action" href={`#agent/${item.conversation_id}`}>{t("打开对话")}</a>}
          <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void dismiss(item.id)}>{t("忽略")}</button>
        </div>
      </li>)}</ul>
    </section>}
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
  const [items, setItems] = useState<ProfileItem[] | null>(null), [bots, setBots] = useState<ProfileBot[]>([]), [error, setError] = useState(""), [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [draft, setDraft] = useState<{ kind: ProfileItem["kind"]; text: string; layer: string }>({ kind: "preference", text: "", layer: "" });
  const load = useCallback((signal?: AbortSignal) => listProfileItems(signal).then(data => { setItems(data.items); setBots(data.bots); }).catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "画像暂时无法读取。"); }), []);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  async function run(id: string, work: () => Promise<unknown>) {
    setBusy(id); setError("");
    try { await work(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败，请重试。"); }
    finally { setBusy(null); }
  }
  function remove(id: string) {
    if (!window.confirm(t("删除这条后，之后的对话不再按它回答。确定删除？"))) return;
    void run(id, () => deleteProfileItem(id));
  }
  function save() {
    if (!editing || !editing.text.trim()) return;
    const { id, text } = editing;
    void run(id, () => updateProfileItem(id, { text: text.trim() })).then(() => setEditing(null));
  }
  function add() {
    const checked = manualItemDraft(draft);
    if (!checked.fields) { setError(checked.error); return; }
    const fields = checked.fields;
    void run("new", () => createProfileItem(fields)).then(() => setDraft(d => ({ ...d, text: "" })));
  }
  const layers = profileLayers(items ?? [], bots), archived = profileGroups(items ?? []).archived;
  const empty = (items ?? []).every(i => i.status !== "active");
  return <section className="personal-card memory-panel">
    <p>{t("在这里直接写下要 Agent 记住的事，或对 Agent 说「记住……」「以后都……」并在确认卡上点同意；说「忘掉……」即可撤回。Agent 也会从你自己说的话里推断偏好，标为“推断”，你可以在这里确认或删除。画像只是偏好，不是证据，也不授权任何操作。")}</p>
    <p className="fine-print">{t("全局：每个 bot 都用。本 bot：只有这个 bot 和默认 bot「助理」能读。可以在每条的“范围”里改。")}</p>
    <form className="memory-add" onSubmit={e => { e.preventDefault(); add(); }}>
      <label className="memory-scope">{t("范围")}
        <select value={draft.layer} onChange={e => setDraft(d => ({ ...d, layer: e.target.value }))}>
          <option value="">{t("全局（所有 bot）")}</option>
          {bots.map(bot => <option key={bot.id} value={bot.id}>{t("本 bot")} · {bot.name}</option>)}
        </select></label>
      <label className="memory-scope">{t("类型")}
        <select value={draft.kind} onChange={e => setDraft(d => ({ ...d, kind: e.target.value as ProfileItem["kind"] }))}>
          {(Object.keys(KIND_LABELS) as ProfileItem["kind"][]).map(kind => <option key={kind} value={kind}>{t(KIND_LABELS[kind])}</option>)}
        </select></label>
      <input className="memory-edit" value={draft.text} maxLength={200} aria-label={t("要记住的内容")} placeholder={t("例如：金额都用亿美元；我主要看半导体")}
        onChange={e => setDraft(d => ({ ...d, text: e.target.value }))} />
      <button type="submit" className="primary-action" disabled={busy === "new" || !draft.text.trim()}>{t("＋ 添加")}</button>
    </form>
    {error && <p role="alert">{t(error)}</p>}
    {items === null && !error ? <p>{t("正在加载…")}</p> : empty ? <p>{t("还没有画像条目。")}</p> :
      layers.filter(layer => layer.items.length > 0).map(layer => <div key={layer.key} className="memory-layer">
        <h2 className="memory-layer-title">{layer.key === "global" ? t("全局") : layer.key === "gone" ? t("已删除的 bot") : layer.label}<span className="num fine-print"> · {layer.items.length}</span></h2>
        <ul className="memory-list">{layer.items.map(item => <li key={item.id}>
          <div className="memory-line"><span className="personal-badge">{t(KIND_LABELS[item.kind] ?? item.kind)}</span>
            <span className={`personal-badge origin-${item.origin}`}>{t(originLabel(item.origin))}</span>
            {editing?.id === item.id
              ? <input className="memory-edit" value={editing.text} maxLength={200} aria-label={t("编辑")} autoFocus
                  onChange={e => setEditing({ id: item.id, text: e.target.value })}
                  onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(null); }} />
              : <strong>{item.text}</strong>}</div>
          <blockquote className="memory-quote">“{item.quote}”</blockquote>
          <div className="memory-actions">
            <label className="memory-scope">{t("范围")}
              <select value={item.bot_id ?? ""} disabled={busy === item.id} onChange={e => { const target = e.target.value || null; void run(item.id, () => updateProfileItem(item.id, { bot_id: target })); }}>
                <option value="">{t("全局")}</option>
                {bots.map(bot => <option key={bot.id} value={bot.id}>{bot.name}</option>)}
                {item.bot_id && !bots.some(b => b.id === item.bot_id) && <option value={item.bot_id}>{t("已删除的 bot")}</option>}
              </select></label>
            {editing?.id === item.id
              ? <><button type="button" className="secondary-action" disabled={busy === item.id || !editing.text.trim()} onClick={save}>{t("保存")}</button>
                  <button type="button" className="secondary-action" onClick={() => setEditing(null)}>{t("取消")}</button></>
              : <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => setEditing({ id: item.id, text: item.text })}>{t("编辑")}</button>}
            {item.origin === "inferred" && <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void run(item.id, () => confirmProfileItem(item.id))}>{t("确认")}</button>}
            <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => remove(item.id)}>{t("删除")}</button>
          </div>
        </li>)}</ul>
      </div>)}
    {archived.length > 0 && <details className="memory-archived"><summary>{t("已归档")} · {archived.length}</summary>
      <ul className="memory-list">{archived.map(item => <li key={item.id}><div className="memory-line"><span className="personal-badge">{t(KIND_LABELS[item.kind] ?? item.kind)}</span><span className="personal-badge">{item.bot_id ? (item.bot_name ?? t("已删除的 bot")) : t(layerLabel(item))}</span><span>{item.text}</span></div>
        <div className="memory-actions"><button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => remove(item.id)}>{t("删除")}</button></div></li>)}</ul>
    </details>}
  </section>;
}

function RecallPanel({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  const openConversation = useOpenConversation();
  const [query, setQuery] = useState(""), [submitted, setSubmitted] = useState(""), [items, setItems] = useState<RecallItem[] | null>(null), [error, setError] = useState("");
  const [bots, setBots] = useState<Bot[]>([]), [botFilter, setBotFilter] = useState("");
  useEffect(() => { const controller = new AbortController(); listBots(controller.signal).then(data => setBots(data.bots)).catch(() => { /* the filter is optional */ }); return () => controller.abort(); }, []);
  useEffect(() => {
    const controller = new AbortController(); setItems(null); setError("");
    listRecall(submitted, controller.signal, botFilter || null).then(setItems).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "往事暂时无法读取。"); });
    return () => controller.abort();
  }, [submitted, botFilter]);
  async function open(item: RecallItem) {
    // Another bot's line is that bot's page; the default bot's and older conversations open as before.
    const bot = bots.find(b => b.id === item.bot_id);
    if (bot && !bot.is_default) { window.location.hash = `bot/${bot.id}`; return; }
    await openConversation(item.conversation_id); onNavigate("agent");
  }
  return <section className="personal-card memory-panel">
    <p>{t("说「上次我们聊过……」时，Agent 会在这里找是哪一次对话、聊了什么。往事只是线索，不是证据：回答里的数字仍要重新取证。")}</p>
    <p className="fine-print">{t("每个 bot 只回想自己那条线；默认 bot「助理」能回想所有 bot 的。")}</p>
    <form className="memory-search" onSubmit={e => { e.preventDefault(); setSubmitted(query); }}>
      <input type="search" value={query} maxLength={200} placeholder={t("搜索往事，例如 英伟达")} aria-label={t("搜索往事")} onChange={e => setQuery(e.target.value)} />
      <select value={botFilter} aria-label={t("按 bot 筛选")} onChange={e => setBotFilter(e.target.value)}>
        <option value="">{t("全部 bot")}</option>
        {bots.map(bot => <option key={bot.id} value={bot.id}>{bot.name}</option>)}
      </select>
      <button type="submit" className="secondary-action">{t("搜索")}</button>
    </form>
    {error && <p role="alert">{t(error)}</p>}
    {items === null && !error ? <p>{t("正在加载…")}</p> : items && items.length === 0 ? <p>{t(submitted ? "没有找到相关的往事。" : "还没有往事。聊过几次之后，这里会列出最近的对话摘要。")}</p> :
      recallGroups(items ?? []).map(group => <div key={group.key} className="memory-layer">
        <h2 className="memory-layer-title">{group.key === "none" ? t("未归属") : group.label === "已删除的 bot" ? t(group.label) : group.label}<span className="num fine-print"> · {group.items.length}</span></h2>
        <ul className="memory-list">{group.items.map(item => <li key={item.conversation_id}>
          <div className="memory-line"><span className="personal-badge">{t(recallKindLabel(item.kind))}</span><strong>{item.title}</strong>{item.date && <span className="num fine-print">{item.date}</span>}</div>
          <p>{item.summary}</p>
          <div className="memory-actions"><button type="button" className="secondary-action" onClick={() => void open(item)}>{t("打开对话")}</button></div>
        </li>)}</ul>
      </div>)}
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
