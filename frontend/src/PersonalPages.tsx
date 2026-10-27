import { useCallback, useEffect, useState } from "react";
import { useOpenConversation } from "./AgentChatPanel";
import { confirmProfileItem, createAgentTask, deleteAgentTask, deleteProfileItem, getChatMode, listAgentTasks, listInbox, listProfileItems, listRecall, mintInboxCard, setInboxStatus, updateAgentTask, type AgentTask, type AgentTaskInput, type InboxItem, type ProfileItem, type RecallItem } from "./api";
import { useLocale } from "./i18n";
import { formatPriceLines, inboxKindLabel, inboxOrder, numberedSources, parsePriceLines, parseSymbols, scheduleLabel } from "./inbox-items.mjs";
import { originLabel, profileGroups, recallKindLabel } from "./memory-items.mjs";

// Pages of the personal agent.  Skills is a placeholder until its phase.  The briefs page shows the owner's
// inbox (简报 with numbered sources, 待你确认 suggestions of unattended runs) and scheduled tasks; the memory
// page lists profile items and past conversations.  All through the owner-only /agent-chat API.

type Go = (section: "agent" | "briefs" | "skills" | "memory" | "admin" | "materials") => void;

export function BriefsPage({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  const openConversation = useOpenConversation();
  const [tab, setTab] = useState<"brief" | "confirm" | "tasks">("brief");
  const [briefs, setBriefs] = useState<InboxItem[] | null>(null);
  const [confirms, setConfirms] = useState<InboxItem[] | null>(null);
  const [tasks, setTasks] = useState<AgentTask[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<AgentTask | null>(null);
  const [formError, setFormError] = useState("");
  const [formBusy, setFormBusy] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftKind, setDraftKind] = useState<AgentTask["kind"]>("brief");
  const [draftSchedule, setDraftSchedule] = useState<AgentTask["schedule_kind"]>("weekdays");
  const [draftTime, setDraftTime] = useState("08:00");
  const [draftTimezone, setDraftTimezone] = useState("Asia/Shanghai");
  const [draftScope, setDraftScope] = useState<AgentTask["scope"]>("research_queue");
  const [draftSymbols, setDraftSymbols] = useState("");
  const [draftInstruction, setDraftInstruction] = useState("");
  const [draftFilings, setDraftFilings] = useState(true);
  const [draftNews, setDraftNews] = useState(true);
  const [draftPriceLines, setDraftPriceLines] = useState("");
  const [draftChannel, setDraftChannel] = useState<AgentTask["channel"]>("inbox");
  const [draftRunCap, setDraftRunCap] = useState(60000);
  const [draftDailyCap, setDraftDailyCap] = useState(200000);
  const personal = getChatMode() === "personal";

  const loadBriefs = useCallback((signal?: AbortSignal) => {
    listInbox("brief", signal).then(setBriefs).catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "简报暂时无法读取。"); });
  }, []);
  const loadConfirms = useCallback((signal?: AbortSignal) => {
    listInbox("confirm", signal).then(setConfirms).catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "简报暂时无法读取。"); });
  }, []);
  const loadTasks = useCallback((signal?: AbortSignal) => {
    listAgentTasks(signal).then(setTasks).catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "任务暂时无法读取。"); });
  }, []);
  const reload = useCallback(() => {
    const ctrl = new AbortController();
    loadBriefs(ctrl.signal);
    loadConfirms(ctrl.signal);
    loadTasks(ctrl.signal);
    return () => ctrl.abort();
  }, [loadBriefs, loadConfirms, loadTasks]);
  useEffect(() => { if (personal) return reload(); }, [personal, reload]);

  function openForm(task?: AgentTask) {
    setFormError("");
    if (task) {
      setEditingTask(task);
      setDraftName(task.name);
      setDraftKind(task.kind);
      setDraftSchedule(task.schedule_kind);
      setDraftTime(task.schedule_time);
      setDraftTimezone(task.timezone);
      setDraftScope(task.scope);
      setDraftSymbols(task.symbols.join(", "));
      setDraftInstruction(task.instruction ?? "");
      setDraftFilings(task.config.filings ?? true);
      setDraftNews(task.config.news ?? true);
      setDraftPriceLines(formatPriceLines(task.config.price_lines ?? []));
      setDraftChannel(task.channel);
      setDraftRunCap(task.run_token_cap);
      setDraftDailyCap(task.daily_token_cap);
    } else {
      setEditingTask(null);
      setDraftName("研究队列简报");
      setDraftKind("brief");
      setDraftSchedule("weekdays");
      setDraftTime("08:00");
      setDraftTimezone("Asia/Shanghai");
      setDraftScope("research_queue");
      setDraftSymbols("");
      setDraftInstruction("");
      setDraftFilings(true);
      setDraftNews(true);
      setDraftPriceLines("");
      setDraftChannel("inbox");
      setDraftRunCap(60000);
      setDraftDailyCap(200000);
    }
    setFormOpen(true);
  }

  async function submitForm(e: React.FormEvent) {
    e.preventDefault();
    if (!draftName.trim() || draftName.length > 80) { setFormError(t("请填任务名称（最多 80 字）。")); return; }
    const symbols = draftScope === "symbols" ? parseSymbols(draftSymbols) : [];
    if (draftScope === "symbols" && symbols.length === 0) { setFormError(t("股票代码无效，或指定公司时没有填代码。")); return; }
    if (draftKind === "alerts" && draftPriceLines.trim()) {
      const lines = parsePriceLines(draftPriceLines);
      if (lines === null) { setFormError(t("价格线格式：代码>价格 或 代码<价格，用逗号分隔。")); return; }
    }
    setFormBusy(true);
    setFormError("");
    try {
      const input: AgentTaskInput = {
        name: draftName.trim(),
        kind: draftKind,
        schedule_kind: draftSchedule,
        schedule_time: draftTime,
        timezone: draftTimezone,
        scope: draftScope,
        symbols,
        channel: draftChannel,
        run_token_cap: draftRunCap,
        daily_token_cap: draftDailyCap,
        instruction: draftKind === "brief" ? draftInstruction : null,
        config: draftKind === "alerts" ? { filings: draftFilings, news: draftNews, price_lines: parsePriceLines(draftPriceLines) ?? [] } : {},
      };
      if (editingTask) await updateAgentTask(editingTask.id, input);
      else await createAgentTask(input);
      setFormOpen(false);
      loadTasks();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "保存失败，请重试。");
    } finally {
      setFormBusy(false);
    }
  }

  // One busy marker and one error line for every action of the page.
  async function act(id: string, action: () => Promise<unknown>) {
    setBusy(id); setError("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败，请重试。"); } finally { setBusy(null); }
  }
  const markRead = (id: string) => act(id, async () => { await setInboxStatus(id, "read"); loadBriefs(); loadConfirms(); });
  const dismiss = (id: string) => act(id, async () => { await setInboxStatus(id, "dismissed"); loadBriefs(); loadConfirms(); });
  const toggleTask = (task: AgentTask) => act(task.id, async () => { await updateAgentTask(task.id, { enabled: !task.enabled }); loadTasks(); });
  async function removeTask(task: AgentTask) {
    if (!window.confirm(t("删除这个任务？已生成的简报会保留。"))) return;
    await act(task.id, async () => { await deleteAgentTask(task.id); loadTasks(); });
  }
  const openItem = (item: InboxItem) => act(item.id, async () => {
    await openConversation(item.conversation_id!);
    if (item.status === "unread") await setInboxStatus(item.id, "read");
    onNavigate("agent");
  });
  // The card is minted now, with its own ten-minute capability, in the conversation of the run.
  const mintCard = (item: InboxItem) => act(item.id, async () => {
    const minted = await mintInboxCard(item.id);
    await openConversation(minted.conversation_id);
    onNavigate("agent");
  });

  const unreadBriefs = (briefs ?? []).filter(i => i.status === "unread").length;
  const unreadConfirms = (confirms ?? []).filter(i => i.status === "unread").length;

  if (!personal) return <main className="personal-page" aria-labelledby="briefs-title">
    <h1 id="briefs-title">{t("简报")}</h1>
    <section className="personal-card"><p>{t("登录个人聊天后才能查看简报和任务。")}</p></section>
    <div><button className="primary-action" type="button" onClick={() => onNavigate("agent")}>{t("去对话")}</button></div>
  </main>;

  return <main className="personal-page" aria-labelledby="briefs-title">
    <h1 id="briefs-title">{t("简报")}</h1>
    <nav className="section-tabs" aria-label={t("简报")}>
      <button type="button" aria-current={tab === "brief" ? "page" : undefined} onClick={() => setTab("brief")}>{t("简报")}{unreadBriefs > 0 ? ` · ${unreadBriefs}` : ""}</button>
      <button type="button" aria-current={tab === "confirm" ? "page" : undefined} onClick={() => setTab("confirm")}>{t("待你确认")}{unreadConfirms > 0 ? ` · ${unreadConfirms}` : ""}</button>
      <button type="button" aria-current={tab === "tasks" ? "page" : undefined} onClick={() => setTab("tasks")}>{t("任务")}</button>
    </nav>
    {error && <p role="alert">{error}</p>}
    {tab === "brief" && <section className="personal-card memory-panel">
      {briefs === null && !error ? <p>{t("正在加载…")}</p> : inboxOrder(briefs ?? []).length === 0 ? <p>{t("还没有简报。设好任务后，简报和提醒会出现在这里，每条都带来源编号。")}</p> :
        <ul className="memory-list">{inboxOrder(briefs ?? []).map(item => <li key={item.id}>
          <div className="memory-line"><span className="personal-badge">{t(inboxKindLabel(item.kind))}</span><strong>{item.title}</strong><span className="num fine-print">{item.created_at ? new Date(item.created_at).toLocaleString() : ""}</span></div>
          <p style={{ whiteSpace: "pre-wrap" }}>{item.body}</p>
          {item.sources && item.sources.length > 0
            ? <ol className="brief-sources">{numberedSources(item).map(s => <li key={s.ordinal}>[{s.ordinal}] {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}</li>)}</ol>
            : <p className="fine-print">{t("没有来源，不含事实性结论。")}</p>}
          {item.payload.channel_pending === "telegram" && <p className="fine-print">{t("等待 Telegram 推送。")}</p>}
          {item.payload.missed_slots && item.payload.missed_slots > 1 ? <p className="fine-print">{t("Mac 睡眠期间错过的运行已合并为这一次。")}</p> : null}
          <div className="memory-actions">
            {item.conversation_id && <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void openItem(item)}>{t("打开对话")}</button>}
            {item.status === "unread" && <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void markRead(item.id)}>{t("标为已读")}</button>}
            <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void dismiss(item.id)}>{t("移除")}</button>
          </div>
        </li>)}</ul>}
    </section>}
    {tab === "confirm" && <section className="personal-card memory-panel">
      <p>{t("定时任务在无人值守时只读不写。它想做的写操作会放在这里，不会自动执行；点「生成确认卡」后到对话里核对，点「同意」才执行。")}</p>
      {confirms === null && !error ? <p>{t("正在加载…")}</p> : inboxOrder(confirms ?? []).length === 0 ? <p>{t("没有待你确认的操作。")}</p> :
        <ul className="memory-list">{inboxOrder(confirms ?? []).map(item => <li key={item.id}>
          <div className="memory-line"><span className="personal-badge">{t("Agent 的建议")}</span><strong>{item.title}</strong></div>
          {item.body && <blockquote className="memory-quote">“{item.body}”</blockquote>}
          {item.payload.task_name && <p className="fine-print">{t("来自任务：")}{item.payload.task_name}</p>}
          <div className="memory-actions">
            <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void mintCard(item)}>{t("生成确认卡")}</button>
            <button type="button" className="secondary-action" disabled={busy === item.id} onClick={() => void dismiss(item.id)}>{t("忽略")}</button>
          </div>
        </li>)}</ul>}
    </section>}
    {tab === "tasks" && <section className="personal-card memory-panel">
      <p>{t("任务按时运行（Mac 每 5 分钟检查一次；睡眠期间错过的会在醒来后补跑一次）。简报类任务需要在设置里配置后台任务模型；提醒类任务不调用模型。")}</p>
      {tasks === null && !error ? <p>{t("正在加载…")}</p> : (tasks ?? []).length === 0 ? <p>{t("还没有任务。")}</p> :
        <ul className="memory-list">{(tasks ?? []).map(task => <li key={task.id}>
          <div className="memory-line"><strong>{task.name}</strong><span className="personal-badge">{t(task.kind === "brief" ? "简报" : "提醒")}</span><span className="fine-print">{scheduleLabel(task)} · {task.timezone}</span></div>
          <p className="fine-print">{task.last_status_label ?? t("尚未运行")}{task.enabled && task.next_run_at ? ` · ${t("下次：")}${new Date(task.next_run_at).toLocaleString()}` : ""}<span className="num"> · {t("今日用量：")}{`${task.tokens_today} / ${task.daily_token_cap} tokens`}</span></p>
          {task.instruction && <blockquote className="memory-quote">“{task.instruction}”</blockquote>}
          <div className="memory-actions">
            <button type="button" className="secondary-action" disabled={busy === task.id} onClick={() => void toggleTask(task)}>{t(task.enabled ? "暂停" : "启用")}</button>
            <button type="button" className="secondary-action" disabled={busy === task.id} onClick={() => openForm(task)}>{t("编辑")}</button>
            <button type="button" className="secondary-action" disabled={busy === task.id} onClick={() => void removeTask(task)}>{t("删除")}</button>
            {task.last_conversation_id && <button type="button" className="secondary-action" disabled={busy === task.id} onClick={() => void act(task.id, async () => { await openConversation(task.last_conversation_id!); onNavigate("agent"); })}>{t("打开上次对话")}</button>}
          </div>
        </li>)}</ul>}
      <div><button type="button" className="secondary-action" onClick={() => openForm()}>{t("新建任务")}</button></div>
      {formOpen && <section className="personal-card">
        <form className="task-form" onSubmit={submitForm}>
          <label>{t("名称")}<input type="text" value={draftName} maxLength={80} onChange={e => setDraftName(e.target.value)} /></label>
          <label>{t("类型")}<select value={draftKind} onChange={e => setDraftKind(e.target.value as AgentTask["kind"])}>
            <option value="brief">{t("简报（Agent 回答一段说明）")}</option>
            <option value="alerts">{t("提醒（新申报、新闻、价格线）")}</option>
          </select></label>
          <label>{t("频率")}<select value={draftSchedule} onChange={e => setDraftSchedule(e.target.value as AgentTask["schedule_kind"])}>
            <option value="daily">{t("每天")}</option>
            <option value="weekdays">{t("工作日")}</option>
            <option value="hourly">{t("每小时")}</option>
          </select></label>
          <label>{t("时间")}<input type="time" value={draftTime} onChange={e => setDraftTime(e.target.value)} />
            {draftSchedule === "hourly" && <span className="fine-print">{t("每小时按这里的分钟运行。")}</span>}</label>
          <label>{t("时区")}<input type="text" value={draftTimezone} onChange={e => setDraftTimezone(e.target.value)} placeholder="Asia/Shanghai" /></label>
          <label>{t("范围")}<select value={draftScope} onChange={e => setDraftScope(e.target.value as AgentTask["scope"])}>
            <option value="research_queue">{t("研究队列")}</option>
            <option value="symbols">{t("指定公司")}</option>
          </select></label>
          {draftScope === "symbols" && <label>{t("股票代码")}<input type="text" value={draftSymbols} onChange={e => setDraftSymbols(e.target.value)} placeholder="AAPL, MSFT" /></label>}
          {draftKind === "brief" && <label>{t("任务说明")}<textarea value={draftInstruction} maxLength={2000} onChange={e => setDraftInstruction(e.target.value)} placeholder={t("例如：总结研究队列公司过去一天的新申报和新闻，列出值得注意的变化。")} /></label>}
          {draftKind === "alerts" && <>
            <div className="task-form-row">
              <label><input type="checkbox" checked={draftFilings} onChange={e => setDraftFilings(e.target.checked)} /> {t("新申报")}</label>
              <label><input type="checkbox" checked={draftNews} onChange={e => setDraftNews(e.target.checked)} /> {t("新闻线索")}</label>
            </div>
            <label>{t("价格线")}<input type="text" value={draftPriceLines} onChange={e => setDraftPriceLines(e.target.value)} placeholder="NVDA&gt;150, AAPL&lt;180" /></label>
          </>}
          <label>{t("推送")}<select value={draftChannel} onChange={e => setDraftChannel(e.target.value as AgentTask["channel"])}>
            <option value="inbox">{t("收件箱")}</option>
            <option value="telegram">{t("Telegram")}</option>
          </select></label>
          <label>{t("单次 token 上限")}<input type="number" value={draftRunCap} onChange={e => setDraftRunCap(Number(e.target.value))} /></label>
          <label>{t("每日 token 上限")}<input type="number" value={draftDailyCap} onChange={e => setDraftDailyCap(Number(e.target.value))} /></label>
          {formError && <p role="alert">{formError}</p>}
          <div className="memory-actions">
            <button type="submit" className="primary-action" disabled={formBusy}>{t("保存任务")}</button>
            <button type="button" className="secondary-action" onClick={() => setFormOpen(false)}>{t("取消")}</button>
          </div>
        </form>
      </section>}
    </section>}
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
