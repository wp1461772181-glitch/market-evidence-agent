import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, createV2ForecastJob, createV2ManualRevisionJob, getUploadedEvidence, getV2Evaluations, getV2ForecastRoots, getV2ForecastVersion, getV2Job, getV2MonitorStatus, getV2Timeline, getV2Workspace } from "./api";
import type { FilingInventory, UploadedEvidence, V2EvaluationCohort, V2EvaluationResponse, V2EvaluationRoot, V2EvaluationVersion, V2ForecastJob, V2ForecastRoot, V2ForecastRoots, V2MonitorStatus, V2SourceRef, V2Timeline, V2TimelineEntry, V2VersionDetail, V2Workspace } from "./types";

type ResourceState<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "error"; message: string };

type SourceOption = V2SourceRef & {
  label: string;
  publishedAt: string | null;
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

function isActiveJob(job: V2ForecastJob | null) {
  return job?.status === "queued" || job?.status === "running";
}

function savedJobKey(symbol: string) {
  return `market-evidence-agent:v2-job:${symbol}`;
}

function saveJob(symbol: string, job: V2ForecastJob) {
  try { window.localStorage.setItem(savedJobKey(symbol), job.id); } catch { /* Storage is optional; the job itself remains durable. */ }
}

function savedJob(symbol: string) {
  try { return window.localStorage.getItem(savedJobKey(symbol)); } catch { return null; }
}

function clearSavedJob(symbol: string) {
  try { window.localStorage.removeItem(savedJobKey(symbol)); } catch { /* No storage access does not affect server state. */ }
}

function formatTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" });
}

function modelStatusText(status: V2Workspace["joint_model_status"] | V2TimelineEntry["model_status"]) {
  if (status === "experimental_joint") return "实验性联合模型";
  if (status === "baseline_only") return "纯行情基线";
  if (status === "research_only") return "仅研究模式";
  return "联合模型未验证";
}

function jobStatusText(job: V2ForecastJob) {
  if (job.status === "queued") return "已排队，尚未被 worker 领取";
  if (job.status === "running") return "worker 正在处理";
  if (job.status === "succeeded") return "已发布不可变版本";
  if (job.status === "succeeded_no_change") return "已完成，但输入没有产生新版本";
  if (job.status === "blocked_data") return "已阻塞：缺少已验证处理条件";
  return "处理失败";
}

export function V2ForecastWorkspace({ symbol, filings }: { symbol: string; filings: FilingInventory | null }) {
  const [workspace, setWorkspace] = useState<ResourceState<V2Workspace>>({ kind: "loading" });
  const [roots, setRoots] = useState<ResourceState<V2ForecastRoots>>({ kind: "loading" });
  const [monitor, setMonitor] = useState<ResourceState<V2MonitorStatus>>({ kind: "loading" });
  const [evaluations, setEvaluations] = useState<ResourceState<V2EvaluationResponse>>({ kind: "loading" });
  const [selectedRootId, setSelectedRootId] = useState("");
  const [timeline, setTimeline] = useState<ResourceState<V2Timeline> | null>(null);
  const [activeJob, setActiveJob] = useState<V2ForecastJob | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<ResourceState<UploadedEvidence[]>>({ kind: "loading" });
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [parentVersionId, setParentVersionId] = useState("");
  const [sourceKey, setSourceKey] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [versionDetails, setVersionDetails] = useState<ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const [next, rootList] = await Promise.all([getV2Workspace(symbol, signal), getV2ForecastRoots(symbol, signal)]);
      setWorkspace({ kind: "ready", data: next });
      setRoots({ kind: "ready", data: rootList });
      setSelectedRootId((previous) => rootList.roots.some((root) => root.id === previous) ? previous : rootList.roots[0]?.id ?? "");
    } catch (error) {
      if (signal?.aborted) return;
      setWorkspace({ kind: "error", message: errorMessage(error, "暂时无法读取 V2 工作台。") });
      setRoots({ kind: "error", message: errorMessage(error, "暂时无法读取预测日期列表。") });
      setTimeline(null);
    }
  }, [symbol]);

  const refreshMonitor = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await getV2MonitorStatus(signal);
      setMonitor({ kind: "ready", data: next });
    } catch (error) {
      if (!signal?.aborted) setMonitor({ kind: "error", message: errorMessage(error, "暂时无法读取自动监控记录。") });
    }
  }, []);

  const refreshEvaluations = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await getV2Evaluations(symbol, signal);
      setEvaluations({ kind: "ready", data: next });
    } catch (error) {
      if (!signal?.aborted) setEvaluations({ kind: "error", message: errorMessage(error, "暂时无法读取 V2 到期评估。") });
    }
  }, [symbol]);

  useEffect(() => {
    const controller = new AbortController();
    setActiveJob(null);
    setActionError(null);
    setRevisionOpen(false);
    setParentVersionId("");
    setSourceKey("");
    setSelectedRootId("");
    setWorkspace({ kind: "loading" });
    setRoots({ kind: "loading" });
    setMonitor({ kind: "loading" });
    setEvaluations({ kind: "loading" });
    setUploaded({ kind: "loading" });
    void refresh(controller.signal);
    void refreshMonitor(controller.signal);
    void refreshEvaluations(controller.signal);
    const previousJobId = savedJob(symbol);
    if (previousJobId) {
      getV2Job(previousJobId, controller.signal)
        .then((job) => {
          if (controller.signal.aborted) return;
          if (job.symbol === symbol) setActiveJob(job);
          else clearSavedJob(symbol);
        })
        .catch(() => clearSavedJob(symbol));
    }
    getUploadedEvidence(symbol, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setUploaded({ kind: "ready", data: result.items ?? [] }); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setUploaded({ kind: "error", message: errorMessage(error, "暂时无法读取已上传材料。") }); });
    return () => controller.abort();
  }, [symbol, refresh, refreshMonitor, refreshEvaluations]);

  useEffect(() => {
    if (activeJob === null || !isActiveJob(activeJob)) return;
    const jobId = activeJob.id;
    const timer = window.setInterval(() => {
      getV2Job(jobId)
        .then((next) => {
          setActiveJob(next);
          if (!isActiveJob(next)) {
            void refresh();
            void refreshMonitor();
            void refreshEvaluations();
          }
        })
        .catch((error: unknown) => setActionError(errorMessage(error, "无法刷新 V2 任务状态。")));
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [activeJob, refresh, refreshMonitor, refreshEvaluations, symbol]);

  useEffect(() => {
    if (!selectedRootId) {
      setTimeline(null);
      return;
    }
    const controller = new AbortController();
    setParentVersionId("");
    setRevisionOpen(false);
    setTimeline({ kind: "loading" });
    getV2Timeline(selectedRootId, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setTimeline({ kind: "ready", data: next }); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setTimeline({ kind: "error", message: errorMessage(error, "无法读取所选预测的版本链。") }); });
    return () => controller.abort();
  }, [selectedRootId]);

  const sourceOptions = useMemo<SourceOption[]>(() => {
    const official = (filings?.filings ?? [])
      .filter((filing) => Boolean(filing.id) && filing.content_status === "fetched" && Boolean(filing.accepted_at))
      .map((filing) => ({
        source_type: "official_filing" as const,
        source_id: filing.id!,
        publishedAt: filing.accepted_at ?? null,
        label: `SEC ${filing.form} · ${filing.accession_number}`,
      }));
    const media = uploaded.kind === "ready"
      ? uploaded.data.map((item) => ({
        source_type: "uploaded_media" as const,
        source_id: item.id,
        publishedAt: item.published_at,
        label: `媒体 · ${item.title}`,
      }))
      : [];
    return [...official, ...media];
  }, [filings, uploaded]);

  const versions = timeline?.kind === "ready" ? timeline.data.versions : [];
  const selectedRoot = roots.kind === "ready" ? roots.data.roots.find((root) => root.id === selectedRootId) ?? null : null;
  useEffect(() => {
    if (!versions.length) {
      setSelectedVersionId("");
      setVersionDetails(null);
      return;
    }
    if (!versions.some((version) => version.id === selectedVersionId)) setSelectedVersionId(versions.at(-1)!.id);
  }, [versions, selectedVersionId]);

  useEffect(() => {
    if (!selectedVersionId) return;
    const controller = new AbortController();
    setVersionDetails({ kind: "loading" });
    getV2ForecastVersion(selectedVersionId, controller.signal)
      .then(async (current) => {
        const parent = current.parent_version_id ? await getV2ForecastVersion(current.parent_version_id, controller.signal) : null;
        if (!controller.signal.aborted) setVersionDetails({ kind: "ready", data: { current, parent } });
      })
      .catch((error: unknown) => { if (!controller.signal.aborted) setVersionDetails({ kind: "error", message: errorMessage(error, "无法读取冻结版本详情。") }); });
    return () => controller.abort();
  }, [selectedVersionId]);

  const chosenVersion = versions.find((version) => version.id === parentVersionId) ?? versions.at(-1);
  const chosenSource = sourceOptions.find((source) => `${source.source_type}:${source.source_id}` === sourceKey);

  async function createForecast() {
    setActionError(null);
    try {
      const job = await createV2ForecastJob(symbol);
      setActiveJob(job);
      saveJob(symbol, job);
      void refresh();
    } catch (error) {
      setActionError(errorMessage(error, "无法创建 V2 预测任务。"));
    }
  }

  async function createRevision() {
    if (!chosenVersion || !chosenSource) return;
    setActionError(null);
    try {
      const job = await createV2ManualRevisionJob(chosenVersion.id, [chosenSource]);
      setActiveJob(job);
      saveJob(symbol, job);
      setRevisionOpen(false);
      void refresh();
    } catch (error) {
      setActionError(errorMessage(error, "无法创建手动修订任务。"));
    }
  }

  const modelStatus = selectedRoot?.model_status ?? (workspace.kind === "ready" ? workspace.data.joint_model_status : "unavailable");
  const rootIsRevisable = selectedRoot?.expired === false;
  const revisionDisabledReason = selectedRoot?.expired === true ? "该预测的目标已到期，只能查看历史版本。" : selectedRoot?.expired === null ? "该预测的目标合同无效，只能查看历史版本。" : null;
  const canCreateRevision = Boolean(chosenVersion && chosenSource && !isActiveJob(activeJob));
  const sourceDateInvalid = Boolean(chosenVersion && chosenSource?.publishedAt && new Date(chosenSource.publishedAt) <= new Date(chosenVersion.decision_at));

  return <section className="v2-desk" aria-labelledby="v2-desk-title">
    <header className="v2-desk-heading">
      <div>
        <p className="eyebrow">V2 durable workflow</p>
        <h2 id="v2-desk-title">主动预测与修订队列</h2>
        <p>这个工作台只展示 V2 的持久化任务与版本。旧档案仍可在下方回看，二者不会混为同一套概率。</p>
      </div>
      <button className="primary-action" type="button" onClick={createForecast} disabled={isActiveJob(activeJob) || workspace.kind === "loading"}>
        {isActiveJob(activeJob) ? "任务处理中…" : "创建 V2 预测任务"}
      </button>
    </header>

    <RootPicker roots={roots} selectedRootId={selectedRootId} onSelect={setSelectedRootId} />

    <div className="v2-status-grid">
      <article><span>联合数值模型</span><strong>{modelStatusText(modelStatus)}</strong><small>{modelStatus === "experimental_joint" ? "仅可作为实验性输出阅读" : "当前不会给出已验证的联合概率"}</small></article>
      <MonitorCard monitor={monitor} />
      <article><span>待处理任务</span><strong>{workspace.kind === "ready" ? workspace.data.pending_job_count : "—"}</strong><small>排队只表示服务已接收，不表示预测已生成。</small></article>
    </div>

    <MonitorDetail monitor={monitor} />

    {workspace.kind === "error" && <p className="v2-notice error" role="alert">{workspace.message}</p>}
    {activeJob && <JobStatus job={activeJob} />}
    {actionError && <p className="v2-notice error" role="alert">{actionError}</p>}

    <div className="v2-version-layout">
      <ForecastSummary root={selectedRoot} roots={roots} detail={versionDetails} />
      <RevisionComparison timeline={timeline} detail={versionDetails} />
    </div>

    <EvaluationReadout evaluations={evaluations} selectedRootId={selectedRootId} selectedVersionId={selectedVersionId} />

    <section className="v2-revision-entry" aria-labelledby="v2-revision-title">
      <div>
        <p className="section-label">人工核验入口</p>
        <h3 id="v2-revision-title">用一条新材料发起修订</h3>
        <p>系统会在服务端再次检查股票、公开时间、观察时间与固定目标窗口。提交成功只代表已入队；原版本保持不变。</p>
      </div>
      <button className="secondary-action" type="button" onClick={() => setRevisionOpen((open) => !open)} disabled={!versions.length || !rootIsRevisable || isActiveJob(activeJob)}>
        {revisionOpen ? "收起修订" : "选择版本与材料"}
      </button>
    </section>

    {revisionDisabledReason && <p className="v2-root-warning" role="status">{revisionDisabledReason}</p>}

    {revisionOpen && <section className="v2-revision-form" aria-label="创建 V2 手动修订">
      <label>修订哪一个 V2 版本
        <select value={parentVersionId} onChange={(event) => setParentVersionId(event.target.value)}>
          <option value="">默认最新版本</option>
          {versions.map((version) => <option value={version.id} key={version.id}>V{version.version_no} · {formatTime(version.decision_at)}</option>)}
        </select>
      </label>
      <label>新增材料
        <select value={sourceKey} onChange={(event) => setSourceKey(event.target.value)}>
          <option value="">选择已读取 SEC 文件或已上传媒体材料</option>
          {sourceOptions.map((source) => <option value={`${source.source_type}:${source.source_id}`} key={`${source.source_type}:${source.source_id}`}>{source.label}</option>)}
        </select>
      </label>
      <div className="v2-form-footer">
        {sourceOptions.length === 0 && <p>没有可用材料。SEC 文件需要先成功读取；媒体材料可在“证据中心”上传。</p>}
        {sourceDateInvalid && <p className="v2-inline-warning">这条材料不晚于所选版本，服务端会拒绝它作为修订依据。</p>}
        <button className="primary-action" type="button" onClick={createRevision} disabled={!canCreateRevision || !rootIsRevisable || sourceDateInvalid}>创建人工修订任务</button>
      </div>
    </section>}

    <EvidenceEvents timeline={timeline} detail={versionDetails} selectedVersionId={selectedVersionId} onSelect={setSelectedVersionId} />
  </section>;
}

function JobStatus({ job }: { job: V2ForecastJob }) {
  const error = job.error?.type;
  return <section className={`v2-job-status is-${job.status}`} aria-live="polite">
    <div><span className="section-label">当前任务 · {job.kind === "manual_revision" ? "人工修订" : "新预测"}</span><strong>{jobStatusText(job)}</strong><small>阶段：{job.current_stage} · 创建于 {formatTime(job.created_at)}</small></div>
    <div className="v2-job-result">{job.result_version_id ? <>结果版本<br /><code>{job.result_version_id.slice(0, 8)}</code></> : error ? <>原因<br /><code>{error}</code></> : <>任务编号<br /><code>{job.id.slice(0, 8)}</code></>}</div>
  </section>;
}

function MonitorCard({ monitor }: { monitor: ResourceState<V2MonitorStatus> }) {
  if (monitor.kind === "loading") return <article><span>自动监控</span><strong>正在读取记录</strong><small>尚未根据定时器配置判断健康。</small></article>;
  if (monitor.kind === "error") return <article><span>自动监控</span><strong>状态不可读取</strong><small>无法确认监控是否正常运行。</small></article>;
  const { health, last_run: lastRun } = monitor.data;
  return <article className={`v2-monitor-card is-${health}`}><span>自动监控</span><strong>{monitorHealthText(health)}</strong><small>{lastRun ? `最近扫描 ${formatTime(lastRun.completed_at ?? lastRun.started_at)}` : "还没有持久化运行记录"}</small></article>;
}

function MonitorDetail({ monitor }: { monitor: ResourceState<V2MonitorStatus> }) {
  if (monitor.kind === "loading") return null;
  if (monitor.kind === "error") return <p className="v2-notice error" role="alert">{monitor.message}</p>;
  const { health, last_run: lastRun, last_success: lastSuccess, stale_after_seconds: staleAfter } = monitor.data;
  if (!lastRun) return <section className="v2-monitor-detail"><p className="section-label">自动监控记录</p><h3>没有运行记录</h3><p>没有实际扫描或成功记录时，页面不会把每小时计划显示为健康。</p></section>;
  const symbols = Object.entries(lastRun.per_symbol_results);
  return <section className={`v2-monitor-detail is-${health}`} aria-label="自动监控状态">
    <div><p className="section-label">自动监控记录</p><h3>{monitorHealthText(health)} · 最近运行 {monitorRunStatusText(lastRun.status)}</h3><p>检查开始 {formatTime(lastRun.started_at)}；完成 {formatTime(lastRun.completed_at)}；下一次预期 {formatTime(lastRun.next_due_at)}。</p><small>延迟阈值为 {Math.round(staleAfter / 3600)} 小时。最近扫描成功只代表一条持久化记录；定时健康需观察两个实际周期。</small></div>
    <div className="v2-monitor-summary"><span>最近全成功</span><strong>{lastSuccess ? formatTime(lastSuccess.completed_at) : "从未记录"}</strong>{lastRun.retry_reason && <small>{lastRun.retry_reason}</small>}</div>
    <div className="v2-monitor-symbols">{symbols.length ? symbols.map(([ticker, result]) => <article key={ticker} className={`is-${result.status}`}><strong>{ticker}</strong><span>{monitorSymbolStatusText(result.status)}</span><small>{result.error ?? `发现 ${result.discovered_count ?? 0} · 新增 ${result.created_count ?? 0} · 入队 ${result.queued_count ?? 0}`}</small></article>) : <p>本次运行没有返回逐股结果。</p>}</div>
  </section>;
}

function EvaluationReadout({ evaluations, selectedRootId, selectedVersionId }: { evaluations: ResourceState<V2EvaluationResponse>; selectedRootId: string; selectedVersionId: string }) {
  if (evaluations.kind === "loading") return <section className="v2-evaluation"><p>正在读取所选预测的到期评估…</p></section>;
  if (evaluations.kind === "error") return <section className="v2-evaluation"><p className="v2-notice error" role="alert">{evaluations.message}</p></section>;
  const match = evaluationRootFor(evaluations.data, selectedRootId);
  if (!match) return <section className="v2-evaluation"><p className="section-label">到期评估</p><h3>所选预测尚无评估记录</h3><p>评估区只读已持久化结果；页面不会拉行情或触发计算。</p></section>;
  const { cohort, root } = match;
  const selectedVersion = root.versions.find((version) => version.id === selectedVersionId)
    ?? [...root.versions].sort((left, right) => right.version_no - left.version_no)[0];
  if (!selectedVersion) return null;
  const evaluation = selectedVersion.latest_evaluation;
  const state = evaluationStateText(evaluation?.status, root.target_end_date);
  const scoreParts = evaluation?.status === "succeeded"
    ? [
      evaluation.brier_score !== null ? `Brier ${scoreText(evaluation.brier_score)}` : null,
      evaluation.log_loss !== null ? `Log loss ${scoreText(evaluation.log_loss)}` : null,
      evaluation.direction_correct !== null ? `方向${evaluation.direction_correct ? "正确" : "错误"}` : null,
    ].filter((part): part is string => part !== null)
    : [];
  const numericScoring = (selectedVersion.model_status === "experimental_joint" || selectedVersion.model_status === "baseline_only") && scoreParts.length > 0;
  return <section className="v2-evaluation" aria-label="所选 V2 预测的到期评估">
    <div><p className="section-label">V2 到期评估 · 所选根</p><h3>{state}</h3><p>目标日 {root.target_end_date ?? "未记录"} · V{selectedVersion.version_no} · {selectedVersion.time_mode === "observed" ? "前向观察" : selectedVersion.time_mode === "historical_research" ? "历史研究" : "时间模式未知"}</p></div>
    <div className="v2-evaluation-sample"><span>根样本</span><strong>标签 {cohort.sample.labelled_root_count} · 评分 {cohort.sample.scored_root_count} / {cohort.sample.root_denominator}</strong><small>{cohortStatusText(cohort.status)}；真实标签与可评分预测分开统计，修订版本不重复计入根样本。</small></div>
    {evaluation?.status === "succeeded" && <div className="v2-evaluation-result"><span>真实标签</span><strong>{labelText(evaluation.actual_label)}</strong><small>{evaluation.actual_target_close === null ? "目标收盘价未记录" : `目标收盘 ${evaluation.actual_target_close}`}{evaluation.label_available_at ? ` · ${formatTime(evaluation.label_available_at)} 可用` : ""}</small>{numericScoring ? <p>{selectedVersion.model_status === "baseline_only" ? "纯行情基线评分：" : "实验性联合模型评分："}{scoreParts.join(" · ")}</p> : <p>{selectedVersion.model_status === "research_only" ? "已到期可记录真实标签，但此版本是仅研究模式：无可评分数值预测。" : "真实标签已记录，但当前没有可显示的数值评分。"}</p>}</div>}
    {evaluation && evaluation.status !== "succeeded" && <p className="v2-evaluation-note">{evaluation.status === "blocked_price" ? "目标价格尚不可用，保持待评估。" : evaluation.status === "failed" ? "最近一次评估失败，未生成替代分数。" : "评估记录已创建，等待目标标签成熟。"}</p>}
  </section>;
}

function evaluationRootFor(response: V2EvaluationResponse, rootId: string): { cohort: V2EvaluationCohort; root: V2EvaluationRoot } | null {
  for (const cohort of Object.values(response.cohorts)) {
    const root = cohort.roots.find((item) => item.root_id === rootId);
    if (root) return { cohort, root };
  }
  return null;
}

function evaluationStateText(status: "pending" | "succeeded" | "blocked_price" | "failed" | undefined, targetEndDate: string | null) {
  if (status === "succeeded") return "评估已记录";
  if (status === "blocked_price") return "等待目标价格";
  if (status === "failed") return "评估失败";
  if (targetEndDate && targetEndDate > new Date().toISOString().slice(0, 10)) return "目标未到期 · 待评估";
  return "待评估";
}

function cohortStatusText(status: V2EvaluationCohort["status"]) {
  if (status === "available") return "样本达到最低门槛";
  if (status === "insufficient_samples") return "样本不足";
  return "尚无可评分根";
}

function labelText(label: "bearish" | "neutral" | "bullish" | null) {
  if (label === "bearish") return "下跌";
  if (label === "neutral") return "持平";
  if (label === "bullish") return "上涨";
  return "未记录";
}

function scoreText(value: number | null) {
  return value === null ? "—" : value.toFixed(4);
}

function monitorHealthText(health: V2MonitorStatus["health"]) {
  switch (health) {
    case "healthy": return "最近扫描成功";
    case "delayed": return "运行延迟";
    case "degraded": return "部分失败或降级";
    default: return "没有运行记录";
  }
}

function monitorRunStatusText(status: "running" | "succeeded" | "partial" | "failed") {
  if (status === "succeeded") return "成功";
  if (status === "partial") return "部分失败";
  if (status === "failed") return "失败";
  return "进行中";
}

function monitorSymbolStatusText(status: "succeeded" | "incomplete" | "failed") {
  if (status === "succeeded") return "成功";
  if (status === "incomplete") return "未完整扫描";
  return "失败";
}

function RootPicker({ roots, selectedRootId, onSelect }: { roots: ResourceState<V2ForecastRoots>; selectedRootId: string; onSelect: (id: string) => void }) {
  if (roots.kind === "loading") return <p className="v2-root-loading">正在读取可修订的预测日期…</p>;
  if (roots.kind === "error") return <p className="v2-notice error" role="alert">{roots.message}</p>;
  if (!roots.data.roots.length) return <p className="v2-root-loading">尚无 V2 预测根。创建任务并成功发布后会出现在这里。</p>;
  return <label className="v2-root-picker">选择预测日期 / 根
    <select value={selectedRootId} onChange={(event) => onSelect(event.target.value)}>
      {roots.data.roots.map((root) => <option value={root.id} key={root.id}>{formatTime(root.decision_at)} · 目标 {root.target_end_date} · V{root.latest_version_no}{root.expired === true ? " · 已到期" : root.expired === null ? " · 合同无效" : ""}</option>)}
    </select>
  </label>;
}

function ForecastSummary({ root, roots, detail }: { root: V2ForecastRoot | null; roots: ResourceState<V2ForecastRoots>; detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null }) {
  if (roots.kind === "loading" || detail?.kind === "loading") return <section className="v2-card"><p>正在读取所选 V2 预测状态…</p></section>;
  if (roots.kind === "error") return null;
  if (!root) return <section className="v2-card"><p className="section-label">所选预测</p><h3>尚无 V2 预测版本</h3><p>可以创建任务；只有 worker 真正处理并成功发布后才会出现版本。</p></section>;
  const current = detail?.kind === "ready" ? detail.data.current : null;
  if (!current) return <section className="v2-card"><p className="section-label">所选预测</p><h3>无法读取所选根的当前版本</h3><p>可重新选择预测日期或刷新页面；不会用其他根的版本代替它。</p></section>;
  const joint = current.model_status === "experimental_joint" ? current.joint_probabilities : null;
  return <section className="v2-card">
    <p className="section-label">所选预测 · V{current.version_no}</p>
    <h3>{modelStatusText(current.model_status)}</h3>
    <dl><div><dt>固定目标日</dt><dd>{root.target_end_date}</dd></div><div><dt>决策时点</dt><dd>{formatTime(current.decision_at)}</dd></div><div><dt>行情截止</dt><dd>{formatTime(current.market_cutoff_at)}</dd></div></dl>
    {joint ? <ProbabilityStrip probabilities={joint} label="实验性联合概率" /> : <p className="v2-limit">仅研究模式：无数值预测。研究基线与旧档案概率不能替代为联合模型结果。</p>}
  </section>;
}

function RevisionComparison({ timeline, detail }: { timeline: ResourceState<V2Timeline> | null; detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null }) {
  if (timeline?.kind === "loading") return <section className="v2-card"><p>正在读取版本链…</p></section>;
  if (timeline?.kind === "error") return <section className="v2-card"><p>版本链读取失败：{timeline.message}</p></section>;
  const versions = timeline?.kind === "ready" ? timeline.data.versions : [];
  const marketChanged = detail?.kind === "ready" && detail.data.parent
    ? marketSummary(detail.data.current.price_input_manifest) !== marketSummary(detail.data.parent.price_input_manifest)
    : false;
  return <section className="v2-card"><p className="section-label">修订对比</p><h3>{versions.length ? `${versions.length} 个不可变版本` : "尚无版本链"}</h3>
    {versions.length > 1 ? <p>每个子版本保留固定目标合同；版本差异见下方事件记录。</p> : <p>出现新的合规材料后，可以从这里选择版本发起人工修订。</p>}
    {versions.length > 0 && <dl><div><dt>固定目标</dt><dd>{String(timeline?.kind === "ready" ? timeline.data.target_contract.target_end_date ?? "已记录" : "—")}</dd></div><div><dt>当前版本</dt><dd>V{versions.at(-1)?.version_no}</dd></div></dl>}
    {detail?.kind === "ready" && detail.data.parent && <p className="v2-limit">市场输入：{marketChanged ? "已更新（见下方截止时间）" : "与父版清单一致"}。本系统尚未验证各输入对市场结果的定量归因。</p>}
  </section>;
}

function EvidenceEvents({ timeline, detail, selectedVersionId, onSelect }: {
  timeline: ResourceState<V2Timeline> | null;
  detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null;
  selectedVersionId: string;
  onSelect: (id: string) => void;
}) {
  if (timeline?.kind !== "ready" || timeline.data.versions.length === 0) return null;
  return <section className="v2-events" aria-labelledby="v2-events-title"><div className="section-heading"><div><p className="eyebrow">版本与来源</p><h2 id="v2-events-title">冻结的 V2 版本链</h2></div><span>同一目标合同</span></div><ol>
    {timeline.data.versions.map((version) => <li key={version.id} className={version.id === selectedVersionId ? "is-selected" : ""}>
      <span className="v2-event-dot" aria-hidden="true" />
      <button type="button" className="v2-event-select" onClick={() => onSelect(version.id)}><strong>V{version.version_no} · {version.parent_version_id ? "修订版本" : "根版本"}</strong><p>{formatTime(version.decision_at)} · {modelStatusText(version.model_status)} · {version.trigger_type}</p>{version.change_reason && <small>{version.change_reason}</small>}</button>
      {version.model_status === "experimental_joint" && version.joint_probabilities && <ProbabilityStrip probabilities={version.joint_probabilities} label="实验性联合概率" compact />}
    </li>)}
  </ol><EvidenceDetail detail={detail} /></section>;
}

function EvidenceDetail({ detail }: { detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null }) {
  if (detail?.kind === "loading") return <p className="v2-detail-loading">正在读取所选版本的冻结来源和市场输入…</p>;
  if (detail?.kind === "error") return <p className="v2-notice error" role="alert">{detail.message}</p>;
  if (detail?.kind !== "ready") return null;
  const { current, parent } = detail.data;
  const currentSources = current.evidence_version_manifest;
  const parentSources = parent?.evidence_version_manifest ?? [];
  const parentBySource = new Map(parentSources.map((item) => [sourceIdentity(item), item]));
  const currentSourceKeys = new Set(currentSources.map(sourceIdentity));
  const added = currentSources.filter((item) => !parentBySource.has(sourceIdentity(item)));
  const replaced = currentSources.filter((item) => {
    const prior = parentBySource.get(sourceIdentity(item));
    return prior !== undefined && eventIdentity(prior) !== eventIdentity(item);
  });
  const removed = parentSources.filter((item) => !currentSourceKeys.has(sourceIdentity(item)));
  const inherited = currentSources.length - added.length - replaced.length;
  const market = current.price_input_manifest;
  const researchStatus = researchStatusText(current.research_report?.research_conclusions_status ?? current.research_report?.status);
  return <section className="v2-detail" aria-label="所选版本的冻结输入">
    <div><p className="section-label">所选 V{current.version_no} 的来源差异</p><h3>{parent ? `新增 ${added.length} · 状态替换 ${replaced.length} · 移除 ${removed.length} · 继承 ${inherited}` : `冻结 ${currentSources.length} 条来源`}</h3><p>{parent ? "按原始来源键区分新增、状态替换与移除；这些输入变化尚未证明对价格或模型输出的影响。" : "根版本没有父版本可比较。"}</p></div>
    <dl><div><dt>行情数据截止</dt><dd>{formatTime(String(market.market_cutoff_at ?? current.market_cutoff_at))}</dd></div><div><dt>最近完整交易日</dt><dd>{String(market.latest_completed_session ?? "未记录")}</dd></div><div><dt>价格输入哈希</dt><dd>{shortHash(market.price_input_sha256)}</dd></div><div><dt>研究报告</dt><dd>{researchStatus}</dd></div></dl>
    <div className="v2-source-list">{currentSources.length ? currentSources.map((item, index) => {
      const sourceUrl = typeof item.source_url === "string" ? item.source_url : null;
      const publishedAt = item.published_at ?? item.public_at;
      return <article key={`${eventIdentity(item)}-${index}`}><strong>{String(item.source_type ?? "source")}</strong>{sourceUrl ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">打开原始来源</a> : <span>原始来源链接未记录</span>}<p>{formatTime(typeof publishedAt === "string" ? publishedAt : null)} · {coverageText(item.coverage_incomplete)}</p><small>{item.is_new ? "本版本新增/状态变更" : "从父版继承"} · {String(item.discovery_kind ?? "未知来源方式")}</small></article>;
    }) : <p>这个版本没有冻结到合规证据来源。</p>}</div>
  </section>;
}

function eventIdentity(item: Record<string, unknown>) {
  return String(item.event_version_id ?? item.id ?? `${item.source_type}:${item.source_id}`);
}

function sourceIdentity(item: Record<string, unknown>) {
  return `${String(item.source_type ?? "unknown")}:${String(item.source_id ?? eventIdentity(item))}`;
}

function shortHash(value: unknown) {
  const text = String(value ?? "—");
  return text.length > 14 ? `${text.slice(0, 12)}…` : text;
}

function marketSummary(manifest: Record<string, unknown>) {
  return [manifest.price_input_sha256, manifest.market_cutoff_at, manifest.latest_completed_session].map(String).join("|");
}

function coverageText(value: unknown) {
  if (value === true) return "覆盖不完整";
  if (value === false) return "覆盖状态已记录";
  return "覆盖状态未知";
}

function researchStatusText(value: unknown) {
  switch (value) {
    case "ready":
    case "succeeded": return "研究已就绪";
    case "failed":
    case "failed_before_run": return "研究失败";
    case "not_run": return "未运行研究";
    case "no_eligible_frozen_sources": return "没有合规冻结来源";
    default: return "研究状态未记录";
  }
}

function ProbabilityStrip({ probabilities, label, compact = false }: { probabilities: Record<"bearish" | "neutral" | "bullish", number>; label: string; compact?: boolean }) {
  return <div className={`v2-probabilities ${compact ? "compact" : ""}`} aria-label={label}>
    {(["bearish", "neutral", "bullish"] as const).map((key) => <span key={key}><small>{key === "bearish" ? "跌" : key === "neutral" ? "平" : "涨"}</small><b>{Math.round(probabilities[key] * 100)}%</b></span>)}
  </div>;
}
