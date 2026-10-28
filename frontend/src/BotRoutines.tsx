import { useCallback, useEffect, useState } from "react";
import { createAgentTask, deleteAgentTask, listAgentTasks, listTaskRuns, updateAgentTask, type AgentTask, type AgentTaskInput, type TaskRun } from "./api";
import { translatePhrase, useLocale } from "./i18n";
import { formatPriceLines, parsePriceLines, parseSymbols, scheduleLabel } from "./inbox-items.mjs";

// A bot's routines (personal agent, second edition): its scheduled tasks and its last 20 runs.  A brief task's
// result is posted into the bot's line; whether it notifies the owner is written in the bot's or the task's instruction.

const message = (error: unknown, fallback: string) => translatePhrase(error instanceof Error ? error.message : fallback);

export function TaskManager({ botId }: { botId: string }) {
  const { t } = useLocale();
  const [tasks, setTasks] = useState<AgentTask[] | null>(null), [runs, setRuns] = useState<TaskRun[] | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false), [editingTask, setEditingTask] = useState<AgentTask | null>(null), [formError, setFormError] = useState(""), [formBusy, setFormBusy] = useState(false);
  const [draftName, setDraftName] = useState(""), [draftKind, setDraftKind] = useState<AgentTask["kind"]>("brief");
  const [draftSchedule, setDraftSchedule] = useState<AgentTask["schedule_kind"]>("weekdays"), [draftTime, setDraftTime] = useState("08:00"), [draftTimezone, setDraftTimezone] = useState("Asia/Shanghai");
  const [draftScope, setDraftScope] = useState<AgentTask["scope"]>("research_queue"), [draftSymbols, setDraftSymbols] = useState(""), [draftInstruction, setDraftInstruction] = useState("");
  const [draftFilings, setDraftFilings] = useState(true), [draftNews, setDraftNews] = useState(true), [draftPriceLines, setDraftPriceLines] = useState("");
  const [draftChannel, setDraftChannel] = useState<AgentTask["channel"]>("inbox"), [draftRunCap, setDraftRunCap] = useState(60000), [draftDailyCap, setDraftDailyCap] = useState(200000);

  const loadTasks = useCallback((signal?: AbortSignal) => {
    listAgentTasks(signal, botId).then(setTasks).catch(e => { if (!signal?.aborted) setError(message(e, "任务暂时无法读取。")); });
  }, [botId]);
  const loadRuns = useCallback((signal?: AbortSignal) => {
    listTaskRuns(botId, signal).then(setRuns).catch(e => { if (!signal?.aborted) setError(message(e, "最近运行暂时无法读取。")); });
  }, [botId]);
  useEffect(() => { const ctrl = new AbortController(); loadTasks(ctrl.signal); loadRuns(ctrl.signal); return () => ctrl.abort(); }, [loadTasks, loadRuns]);

  function openForm(task?: AgentTask) {
    setFormError("");
    setEditingTask(task ?? null);
    setDraftName(task?.name ?? "研究队列简报"); setDraftKind(task?.kind ?? "brief"); setDraftSchedule(task?.schedule_kind ?? "weekdays"); setDraftTime(task?.schedule_time ?? "08:00");
    setDraftTimezone(task?.timezone ?? "Asia/Shanghai"); setDraftScope(task?.scope ?? "research_queue"); setDraftSymbols(task?.symbols.join(", ") ?? ""); setDraftInstruction(task?.instruction ?? "");
    setDraftFilings(task?.config.filings ?? true); setDraftNews(task?.config.news ?? true); setDraftPriceLines(formatPriceLines(task?.config.price_lines ?? []));
    setDraftChannel(task?.channel ?? "inbox"); setDraftRunCap(task?.run_token_cap ?? 60000); setDraftDailyCap(task?.daily_token_cap ?? 200000);
    setFormOpen(true);
  }

  async function submitForm(e: React.FormEvent) {
    e.preventDefault();
    if (!draftName.trim() || draftName.length > 80) { setFormError(t("请填任务名称（最多 80 字）。")); return; }
    const symbols = draftScope === "symbols" ? parseSymbols(draftSymbols) : [];
    if (draftScope === "symbols" && symbols.length === 0) { setFormError(t("股票代码无效，或指定公司时没有填代码。")); return; }
    if (draftKind === "alerts" && draftPriceLines.trim() && parsePriceLines(draftPriceLines) === null) { setFormError(t("价格线格式：代码>价格 或 代码<价格，用逗号分隔。")); return; }
    setFormBusy(true); setFormError("");
    try {
      const input: AgentTaskInput = {
        name: draftName.trim(), kind: draftKind, schedule_kind: draftSchedule, schedule_time: draftTime, timezone: draftTimezone, scope: draftScope, symbols,
        channel: draftKind === "alerts" ? draftChannel : "inbox", run_token_cap: draftRunCap, daily_token_cap: draftDailyCap, bot_id: botId,
        instruction: draftKind === "brief" ? draftInstruction : null,
        config: draftKind === "alerts" ? { filings: draftFilings, news: draftNews, price_lines: parsePriceLines(draftPriceLines) ?? [] } : {},
      };
      if (editingTask) await updateAgentTask(editingTask.id, input); else await createAgentTask(input);
      setFormOpen(false); loadTasks(); loadRuns();
    } catch (e) { setFormError(message(e, "保存失败，请重试。")); } finally { setFormBusy(false); }
  }

  async function act(id: string, action: () => Promise<unknown>) {
    setBusy(id); setError("");
    try { await action(); } catch (e) { setError(message(e, "操作失败，请重试。")); } finally { setBusy(null); }
  }
  const toggleTask = (task: AgentTask) => act(task.id, async () => { await updateAgentTask(task.id, { enabled: !task.enabled }); loadTasks(); });
  async function removeTask(task: AgentTask) {
    if (!window.confirm(t("删除这个任务？已生成的简报会保留。"))) return;
    await act(task.id, async () => { await deleteAgentTask(task.id); loadTasks(); loadRuns(); });
  }

  return <section className="bot-routines">
    <h3>{t("例行任务和最近运行")}</h3>
    <p className="fine-print">{t("任务按时运行（Mac 每 5 分钟检查一次；睡眠期间错过的会在醒来后补跑一次）。简报类任务需要在设置里配置后台任务模型；提醒类任务不调用模型。")}</p>
    {error && <p role="alert">{error}</p>}
    {tasks === null && !error ? <p>{t("正在加载…")}</p> : (tasks ?? []).length === 0 ? <p>{t("还没有任务。")}</p> :
      <ul className="memory-list">{(tasks ?? []).map(task => <li key={task.id}>
        <div className="memory-line"><strong>{task.name}</strong><span className="personal-badge">{t(task.kind === "brief" ? "简报" : "提醒")}</span><span className="fine-print">{scheduleLabel(task)} · {task.timezone}</span></div>
        <p className="fine-print">{task.last_status_label ?? t("尚未运行")}{task.enabled && task.next_run_at ? ` · ${t("下次：")}${new Date(task.next_run_at).toLocaleString()}` : ""}<span className="num"> · {t("今日用量：")}{`${task.tokens_today} / ${task.daily_token_cap} tokens`}</span></p>
        {task.instruction && <blockquote className="memory-quote">“{task.instruction}”</blockquote>}
        <div className="memory-actions">
          <button type="button" className="secondary-action" disabled={busy === task.id} onClick={() => void toggleTask(task)}>{t(task.enabled ? "暂停" : "启用")}</button>
          <button type="button" className="secondary-action" disabled={busy === task.id} onClick={() => openForm(task)}>{t("编辑")}</button>
          <button type="button" className="secondary-action" disabled={busy === task.id} onClick={() => void removeTask(task)}>{t("删除")}</button>
        </div>
      </li>)}</ul>}
    <div><button type="button" className="secondary-action" onClick={() => openForm()}>{t("新建任务")}</button></div>
    {formOpen && <form className="task-form" onSubmit={submitForm}>
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
        <label><input type="checkbox" checked={draftChannel === "telegram"} onChange={e => setDraftChannel(e.target.checked ? "telegram" : "inbox")} /> {t("也发 Telegram")}</label>
      </>}
      <label>{t("单次 token 上限")}<input type="number" value={draftRunCap} onChange={e => setDraftRunCap(Number(e.target.value))} /></label>
      <label>{t("每日 token 上限")}<input type="number" value={draftDailyCap} onChange={e => setDraftDailyCap(Number(e.target.value))} /></label>
      <p className="fine-print">{t("要不要通知你，写在 bot 的说明或任务说明里，比如「只有重要程度为高才发 Telegram」。没新东西时不会往对话里发消息。")}</p>
      {formError && <p role="alert">{formError}</p>}
      <div className="memory-actions">
        <button type="submit" className="primary-action" disabled={formBusy}>{t("保存任务")}</button>
        <button type="button" className="secondary-action" onClick={() => setFormOpen(false)}>{t("取消")}</button>
      </div>
    </form>}
    <h4>{t("最近 20 次运行")}</h4>
    {runs === null && !error ? <p>{t("正在加载…")}</p> : (runs ?? []).length === 0 ? <p className="fine-print">{t("还没有运行记录。")}</p> :
      <ul className="memory-list">{(runs ?? []).map(run => <li key={run.id}>
        <div className="memory-line"><strong>{run.task_name}</strong><span className="num fine-print">{run.created_at ? new Date(run.created_at).toLocaleString() : ""}</span></div>
        <p className="fine-print">{run.status_label}{run.posted > 0 ? ` · ${t("已贴进对话")}` : ""}{run.conversation_id && <> · <a href={`#agent/${run.conversation_id}`}>{t("打开过程")}</a></>}</p>
      </li>)}</ul>}
  </section>;
}
