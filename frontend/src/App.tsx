import { Tx, formatDateTime as formatLocalizedDateTime, getLocalePreference, translatePhrase, useLocale } from "./i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, getFilingInventory, getJevLearningStatus, getMaterialLibrary, getPendingAccessKeyRequest, getV2Evaluations, getV2ForecastRoots, getV2MonitorStatus, getV2Prices, getV2Workspace, scanOfficialFilings, submitApiAccessKey } from "./api";
import type { FilingInventory, JevLearningStatus, PriceHistory } from "./types";
import { V2ForecastWorkspace } from "./V2ForecastWorkspace";
import { EvidenceCenter } from "./EvidenceCenter";
import { MaterialLibrary } from "./MaterialLibrary";
import { HistoricalReplayWorkspace } from "./HistoricalReplayWorkspace";

const DEFAULT_SYMBOL = "AAPL";
const SUPPORTED_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"] as const;
type SupportedSymbol = typeof SUPPORTED_SYMBOLS[number];
type FilingState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; data: FilingInventory } | { kind: "error"; message: string };
type WorkspaceSection = "overview" | "forecast" | "evidence" | "materials" | "revisions" | "evaluation" | "replay";
const WORKSPACE_SECTION_TITLE: Record<WorkspaceSection, string> = {
  overview: "研究总览", forecast: "预测工作台", evidence: "证据中心", materials: "材料库", revisions: "预测修订", evaluation: "到期评估", replay: "历史回放",
};

export function App() {
  const [accessPromptOpen, setAccessPromptOpen] = useState(false);
  const [accessPromptInvalid, setAccessPromptInvalid] = useState(false);

  useEffect(() => {
    const requireAccessKey = (event: Event) => {
      setAccessPromptInvalid((event as CustomEvent<boolean>).detail === true);
      setAccessPromptOpen(true);
    };
    const rejectAccessKey = () => {
      setAccessPromptInvalid(true);
      setAccessPromptOpen(true);
    };
    window.addEventListener("market-evidence-api-access-required", requireAccessKey);
    window.addEventListener("market-evidence-api-access-invalid", rejectAccessKey);
    const pendingRequest = getPendingAccessKeyRequest();
    if (pendingRequest !== null) {
      setAccessPromptInvalid(pendingRequest);
      setAccessPromptOpen(true);
    }
    return () => {
      window.removeEventListener("market-evidence-api-access-required", requireAccessKey);
      window.removeEventListener("market-evidence-api-access-invalid", rejectAccessKey);
    };
  }, []);

  const { t } = useLocale();
  return <>
    <WorkspaceApp />
    {accessPromptOpen && <div className="media-dialog-backdrop" role="presentation">
      <section className="media-dialog api-access-dialog" role="dialog" aria-modal="true" aria-labelledby="api-access-title" style={{ width: "min(480px, 100%)", maxHeight: "90vh" }}>
        <header className="media-dialog-header">
          <div>
            <p className="section-label">Market Evidence Agent</p>
            <h3 id="api-access-title">{t("本机后端访问口令")}</h3>
            <p>{t("输入访问口令后才能读取或修改本机研究数据。")}</p>
          </div>
        </header>
        <div className="media-dialog-content" style={{ gridTemplateColumns: "1fr", overflow: "visible" }}>
          <form className="evidence-form" onSubmit={(event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const data = new FormData(form);
            const key = String(data.get("api-access-key") ?? "");
            submitApiAccessKey(key);
            setAccessPromptInvalid(false);
            setAccessPromptOpen(false);
          }}>
            {accessPromptInvalid && <p className="material-status material-error" role="alert">{t("访问口令不正确，请重试。")}</p>}
            <label>{t("访问口令")}<input name="api-access-key" type="password" autoComplete="current-password" autoFocus required /></label>
            <div className="material-form-actions">
              <button type="button" className="secondary-action" onClick={() => {
                submitApiAccessKey(null);
                setAccessPromptOpen(false);
                setAccessPromptInvalid(false);
              }}>{t("稍后")}</button>
              <button type="submit" className="primary-action">{t("连接后端")}</button>
            </div>
          </form>
        </div>
      </section>
    </div>}
  </>;
}

function WorkspaceApp() {
  const [requestedSymbol, setRequestedSymbol] = useState(DEFAULT_SYMBOL);
  const [activeSection, setActiveSection] = useState<WorkspaceSection>(sectionFromHash());
  const [activeData, setActiveData] = useState<{ kind: "loading"; symbol: string } | { kind: "ready"; symbol: string; filings: FilingState; priceHistory: PriceHistory | null; priceError: string | null }>({ kind: "loading", symbol: DEFAULT_SYMBOL });
  const [retryKey, setRetryKey] = useState(0);
  const requestVersion = useRef(0);
  const activeSymbolRef = useRef(requestedSymbol);
  activeSymbolRef.current = requestedSymbol;

  useEffect(() => {
    const onHashChange = () => setActiveSection(sectionFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function navigate(section: WorkspaceSection) {
    if (section === activeSection) {
      if (section === "evidence" && window.location.hash !== "#evidence") window.location.hash = "evidence";
      return;
    }
    window.location.hash = section;
    setActiveSection(section);
  }

  function openStock(ticker: SupportedSymbol) {
    if (ticker === requestedSymbol) setRetryKey((value) => value + 1);
    else setRequestedSymbol(ticker);
    navigate("overview");
  }

  useEffect(() => {
    const controller = new AbortController();
    const version = ++requestVersion.current;
    setActiveData({ kind: "loading", symbol: requestedSymbol });
    Promise.allSettled([
      getFilingInventory(requestedSymbol, controller.signal),
      getV2Prices(requestedSymbol, controller.signal),
    ]).then(([filingResult, pricesResult]) => {
      if (controller.signal.aborted || version !== requestVersion.current) return;
      const nextFilings: FilingState = filingResult.status === "fulfilled"
        ? { kind: "ready", data: filingResult.value }
        : { kind: "error", message: actionMessage(filingResult.reason, "暂时无法读取官方申报清单。") };
      const priceHistory = pricesResult.status === "fulfilled" ? pricesResult.value.price_history : null;
      const priceError = pricesResult.status === "rejected" ? actionMessage(pricesResult.reason, "行情暂不可用。") : null;
      setActiveData({ kind: "ready", symbol: requestedSymbol, filings: nextFilings, priceHistory, priceError });
    });
    return () => controller.abort();
  }, [requestedSymbol, retryKey]);

  async function startFilingScan() {
    const scanSymbol = requestedSymbol;
    if (!isSupportedSymbol(scanSymbol)) throw new Error(`SEC 扫描仅支持 ${SUPPORTED_SYMBOLS.join("、")}。`);
    const result = await scanOfficialFilings(scanSymbol);
    if (activeSymbolRef.current === scanSymbol) {
      setActiveData((previous) => previous.kind === "ready" && previous.symbol === scanSymbol ? { ...previous, filings: { kind: "ready", data: result } } : previous);
    }
    return result;
  }

  const shellProps = { activeSection, navigate, symbol: requestedSymbol, onSelectStock: openStock };
  if (activeSection === "materials") return <Shell {...shellProps}><MaterialLibrary key={requestedSymbol} symbol={requestedSymbol} onScan={async () => { const result = await startFilingScan(); setRetryKey((value) => value + 1); return result; }} /></Shell>;
  if (activeData.kind === "loading" || activeData.symbol !== requestedSymbol) return <Shell {...shellProps}><Loading symbol={requestedSymbol} /></Shell>;
  const supported = isSupportedSymbol(requestedSymbol);
  const revisionsNavigate = activeSection === "revisions" ? "revisions" : "forecast";
  return <Shell {...shellProps}>
    <main className="workspace" aria-labelledby="workspace-title">
      <WorkspacePageHeader symbol={requestedSymbol} section={activeSection} recordCount={null} supported={supported} />
      {activeSection === "overview" && <CurrentOverviewWorkspace symbol={requestedSymbol} currentFilings={activeData.filings} priceHistory={activeData.priceHistory} priceError={activeData.priceError} onNavigate={navigate} onOpenStock={openStock} />}
      {(activeSection === "forecast" || activeSection === "revisions") && <section className="workspace-stack">
        <V2ForecastWorkspace key={`${requestedSymbol}-${revisionsNavigate}`} mode={revisionsNavigate} symbol={requestedSymbol} filings={activeData.filings.kind === "ready" ? activeData.filings.data : null} priceHistory={activeData.priceHistory} onOpenForecast={() => navigate("forecast")} />
        {activeData.priceError && <p className="inventory-status inventory-error" role="status">行情暂不可用：{activeData.priceError}</p>}
      </section>}
      {activeSection === "evidence" && <EvidenceCenter key={requestedSymbol} symbol={requestedSymbol} supported={supported} onOpenForecast={() => navigate("forecast")} />}
      {activeSection === "evaluation" && <V2EvaluationWorkspace key={requestedSymbol} symbol={requestedSymbol} />}
      {activeSection === "replay" && <HistoricalReplayWorkspace key={requestedSymbol} initialSymbol={requestedSymbol} />}
    </main>
  </Shell>;
}

function CurrentOverviewWorkspace({ symbol, currentFilings, priceHistory, priceError, onNavigate, onOpenStock }: { symbol: string; currentFilings: FilingState; priceHistory: PriceHistory | null; priceError: string | null; onNavigate: (section: WorkspaceSection) => void; onOpenStock: (ticker: SupportedSymbol) => void }) {
  const { t } = useLocale();
  type UniverseItem = { workspace?: Awaited<ReturnType<typeof getV2Workspace>>; workspaceState: "loading" | "ready" | "error"; roots?: Awaited<ReturnType<typeof getV2ForecastRoots>>; materials?: number; filings?: FilingInventory; error?: string; loading: boolean };
  const [universe, setUniverse] = useState<Record<string, UniverseItem>>(() => Object.fromEntries(SUPPORTED_SYMBOLS.map((ticker) => [ticker, { workspaceState: "loading", loading: true }])));
  const [monitor, setMonitor] = useState<{ kind: "loading" } | { kind: "ready"; data: Awaited<ReturnType<typeof getV2MonitorStatus>> } | { kind: "error" }>({ kind: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    setMonitor({ kind: "loading" });
    setUniverse(Object.fromEntries(SUPPORTED_SYMBOLS.map((ticker) => [ticker, { workspaceState: "loading", loading: true }])));
    Promise.all(SUPPORTED_SYMBOLS.map(async (ticker) => {
      const [workspace, roots, materials, filings] = await Promise.allSettled([
        getV2Workspace(ticker, controller.signal), getV2ForecastRoots(ticker, controller.signal),
        getMaterialLibrary(ticker, { limit: 1, signal: controller.signal }), getFilingInventory(ticker, controller.signal),
      ]);
      if (controller.signal.aborted) return [ticker, { workspaceState: "loading" as const, loading: false }] as const;
      const firstError = [workspace, roots, materials, filings].find((value) => value.status === "rejected");
      return [ticker, {
        ...(workspace.status === "fulfilled" ? { workspace: workspace.value } : {}),
        workspaceState: workspace.status === "fulfilled" ? "ready" as const : "error" as const,
        ...(roots.status === "fulfilled" ? { roots: roots.value } : {}),
        ...(materials.status === "fulfilled" ? { materials: materials.value.total } : {}),
        ...(filings.status === "fulfilled" ? { filings: filings.value } : {}),
        ...(firstError ? { error: actionMessage(firstError.reason, "暂时无法读取该股票的研究状态。") } : {}), loading: false,
      }] as const;
    })).then((entries) => { if (!controller.signal.aborted) setUniverse(Object.fromEntries(entries)); });
    getV2MonitorStatus(controller.signal).then((data) => { if (!controller.signal.aborted) setMonitor({ kind: "ready", data }); }).catch(() => { if (!controller.signal.aborted) setMonitor({ kind: "error" }); });
    return () => controller.abort();
  }, [symbol]);
  const current = universe[symbol];
  const currentWorkspaceState = current?.workspaceState ?? "loading";
  const currentVersion = currentWorkspaceState === "ready" ? current?.workspace?.current_version : null;
  const currentFilingsList = currentFilings.kind === "ready" ? currentFilings.data.filings : [];
  const officialFilingCount = currentFilings.kind === "ready" ? currentFilingsList.length : null;
  return <section className="workspace-stack overview-workspace">
    <WorkspaceIntro eyebrow={t("研究总览")} title={t("从单股研究队列开始")} description={t("队列汇总独立预测、官方资料和已保存材料。没有预测时仍可上传材料、读取 SEC 原文并开始新研究。")} />
    <section className="overview-focus" aria-label={`${symbol} ${t("当前研究状态")}`}>
      <div><p className="eyebrow">{t("当前股票")} / {symbol}</p><h2>{currentWorkspaceState === "loading" ? t("正在读取研究状态") : currentWorkspaceState === "error" ? t("研究状态暂不可用") : currentVersion ? `${t("预测版本")} ${currentVersion.version_no}` : t("可开始一项新研究")}</h2><p>{currentWorkspaceState === "loading" ? t("正在读取当前预测与研究记录。") : currentWorkspaceState === "error" ? t("当前预测状态读取失败，请稍后重试。") : currentVersion ? `${t("最近决策时间")} ${formatUtc(currentVersion.decision_at)}; ${t("选择预测区查看简报、固定目标和行情。")}` : t("目前没有当前预测版本。历史行情与材料仍可查看，也可以直接创建预测。")}</p></div>
      <div className="overview-focus-actions"><button className="primary-action" disabled={currentWorkspaceState === "loading"} onClick={() => onNavigate("forecast")}>{currentWorkspaceState === "loading" ? t("正在读取…") : currentVersion ? t("打开预测") : currentWorkspaceState === "ready" ? t("创建预测") : t("查看预测工作台")}</button><button className="secondary-action" onClick={() => onNavigate("materials")}>{<Tx text={"查看材料库"} />}</button></div>
    </section>
    <section className="overview-metrics" aria-label={t("当前研究状态")}>
      <article><span>{<Tx text={"独立预测数"} />}</span><strong>{current?.roots?.roots.length ?? "—"}</strong><small>{current?.workspace?.pending_job_count ? `${current.workspace.pending_job_count} ${t("项任务处理中")}` : t("按当前预测记录统计")}</small></article>
      <article><span>{<Tx text={"材料总数"} />}</span><strong>{current?.materials ?? "—"}</strong><small>{<Tx text={"已保存官方和上传材料"} />}</small></article>
      <article><span>{<Tx text={"SEC 官方材料"} />}</span><strong>{officialFilingCount ?? "—"}</strong><small>{currentFilings.kind === "error" ? t("官方资料目录暂不可用") : t("当前股票已保存的官方文件")}</small></article>
      <article><span>{<Tx text={"监控状态"} />}</span><strong>{monitor.kind === "loading" ? t("正在读取") : monitor.kind === "error" ? t("暂不可用") : t(monitorHealthLabel(monitor.data.health))}</strong><small>{monitor.kind === "loading" ? t("正在读取监控记录") : monitor.kind === "error" ? t("无法读取监控状态") : monitor.data.last_success?.completed_at ? `${t("最近成功")} ${formatUtc(monitor.data.last_success.completed_at)}` : t("尚无成功运行记录")}</small></article>
    </section>
    <section className="universe-section" aria-labelledby="universe-title"><div className="section-heading"><div><p className="eyebrow">{<Tx text={"研究队列"} />}</p><h2 id="universe-title">{<Tx text={"七只关注股票"} />}</h2></div><span>{<Tx text={"按当前研究数据读取"} />}</span></div><div className="universe-grid">
      {SUPPORTED_SYMBOLS.map((ticker) => {
        const item = universe[ticker];
        const count = item?.roots?.roots.length;
        const filingCount = item?.filings?.filings.length;
        return <article className={`universe-card ${ticker === symbol ? "is-current" : ""}`} key={ticker}>
          <div className="universe-card-top"><strong>{ticker}</strong>{ticker === symbol && <span>{<Tx text={"当前"} />}</span>}</div>
          {item?.loading && <p className="universe-pending">{<Tx text={"正在读取研究状态…"} />}</p>}
          {item?.error && <p className="universe-error" role="status">{item.error}</p>}
          {item && !item.loading && <><dl><div><dt>{<Tx text={"独立预测"} />}</dt><dd>{count ?? "—"}</dd></div><div><dt>{<Tx text={"材料"} />}</dt><dd>{item.materials ?? "—"}</dd></div><div><dt>{<Tx text={"SEC 官方材料"} />}</dt><dd>{filingCount ?? "—"}</dd></div></dl><small>{item.workspaceState === "loading" ? t("正在读取预测状态") : item.workspaceState === "error" ? t("预测状态暂不可用") : item.workspace?.current_version ? `${t("最新版本")} ${item.workspace.current_version.version_no} · ${t(modelStatusLabel(item.workspace.current_version.model_status))}` : t("当前没有预测版本")}</small></>}
          <button className="universe-open" type="button" onClick={() => onOpenStock(ticker)}>{t("打开")} {ticker} {t("总览")} <span aria-hidden="true">→</span></button>
        </article>;
      })}
    </div></section>
    <div className="overview-shortcuts"><button className="text-button" onClick={() => onNavigate("materials")}>{<Tx text={"管理材料 / 扫描 SEC"} />}</button><button className="text-button" onClick={() => onNavigate("evaluation")}>{<Tx text={"查看到期评估"} />}</button><span>{priceError ? `${t("行情暂不可用")}: ${priceError}` : priceHistory?.latest_trading_date ? `${symbol} ${t("行情截至")} ${priceHistory.latest_trading_date}` : t("本地没有可展示的行情")}</span></div>
  </section>;
}

function V2EvaluationWorkspace({ symbol }: { symbol: string }) {
  const { t } = useLocale();
  const [state, setState] = useState<{ kind: "loading" } | { kind: "ready"; data: Awaited<ReturnType<typeof getV2Evaluations>> } | { kind: "error"; message: string }>({ kind: "loading" });
  const [learning, setLearning] = useState<{ kind: "loading" } | { kind: "ready"; data: JevLearningStatus } | { kind: "error"; message: string }>({ kind: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    setLearning({ kind: "loading" });
    getV2Evaluations(symbol, controller.signal).then((data) => { if (!controller.signal.aborted) setState({ kind: "ready", data }); }).catch((error: unknown) => { if (!controller.signal.aborted) setState({ kind: "error", message: actionMessage(error, "暂时无法读取到期评估。") }); });
    getJevLearningStatus(symbol, controller.signal).then((data) => { if (!controller.signal.aborted) setLearning({ kind: "ready", data }); }).catch((error: unknown) => { if (!controller.signal.aborted) setLearning({ kind: "error", message: actionMessage(error, "暂时无法读取 Jev 学习状态。") }); });
    return () => controller.abort();
  }, [symbol]);
  return <section className="workspace-stack">
    <WorkspaceIntro eyebrow="到期评估" title="按模型分组查看已到期预测" description="每组只统计同一时间模式、模型、服务方和问题版本的代表版本。未到期样本显示为待评，不混入其他模型的指标。" />
    <JevLearningReadout state={learning} />
    {state.kind === "loading" && <p className="inventory-status">{<Tx text={"正在读取评估结果…"} />}</p>}
    {state.kind === "error" && <p className="inventory-status inventory-error" role="alert">{state.message}</p>}
    {state.kind === "ready" && <div className="model-cohort-list">{state.data.model_cohorts?.length ? state.data.model_cohorts.map((cohort, index) => {
      const versions = cohort.roots.flatMap((root) => root.versions);
      const scored = versions.map((version) => version.latest_evaluation).filter((evaluation) => evaluation?.status === "succeeded");
      const average = (values: Array<number | null | undefined>) => { const valid = values.filter((value): value is number => typeof value === "number"); return valid.length ? (valid.reduce((sum, value) => sum + value, 0) / valid.length).toFixed(3) : "—"; };
      return <article className="model-cohort-card" key={`${cohort.model_status}-${cohort.provider}-${cohort.actual_model}-${cohort.question_version ?? "none"}-${index}`}>
        <div className="section-heading"><div><p className="eyebrow">{t(cohort.time_mode === "observed" ? "真实观察" : cohort.time_mode === "historical_research" ? "历史研究" : "时间模式未分类")} · {t(modelStatusLabel(cohort.model_status))}</p><h2>{cohort.provider} / {cohort.actual_model}</h2></div><span>{t(cohort.status === "available" ? "指标可用" : cohort.status === "pending" ? "等待到期" : "样本不足")}</span></div>
        <p className="fine-print">{t("问题版本")}: {cohort.question_version ?? t("未记录")} · {t("样本按同组最高版本计数")}</p>
        <div className="overview-metrics"><article><span>{<Tx text={"独立预测样本"} />}</span><strong>{cohort.sample.root_denominator}</strong><small>{<Tx text={"同组代表记录"} />}</small></article><article><span>{<Tx text={"已标注 / 已评分"} />}</span><strong>{cohort.sample.labelled_root_count} / {cohort.sample.scored_root_count}</strong><small>{cohort.sample.unscored_root_count} {t("条尚未评分")}</small></article><article><span>{<Tx text={"平均 Brier"} />}</span><strong>{average(scored.map((value) => value?.brier_score))}</strong><small>{<Tx text={"仅已成功评分的版本"} />}</small></article><article><span>{<Tx text={"平均 Log loss"} />}</span><strong>{average(scored.map((value) => value?.log_loss))}</strong><small>{<Tx text={"仅已成功评分的版本"} />}</small></article></div>
      </article>;
    }) : <div className="evaluation-empty-state"><h2>{state.data.model_cohorts ? t("还没有模型分组评估") : t("模型分组数据暂不可用")}</h2><p>{<Tx text={"这里不会用旧版离线报告或跨模型混合分组填补。预测到期并获得可用行情后，结果会进入对应模型组。"} />}</p></div>}</div>}
  </section>;
}

function JevLearningReadout({ state }: { state: { kind: "loading" } | { kind: "ready"; data: JevLearningStatus } | { kind: "error"; message: string } }) {
  const { locale, t } = useLocale();
  if (state.kind === "loading") return <section className="jev-learning-card"><p className="eyebrow">{<Tx text={"Jev 本地校准"} />}</p><p>{<Tx text={"正在读取学习状态…"} />}</p></section>;
  if (state.kind === "error") return <section className="jev-learning-card"><p className="eyebrow">{<Tx text={"Jev 本地校准"} />}</p><p className="inventory-error" role="alert">{state.message}</p></section>;
  const status = state.data;
  const cohorts = status.cohorts;
  const current = cohorts[0] ?? null;
  const active = current?.active_model ?? null;
  const metrics = active?.test_metrics && typeof active.test_metrics === "object" ? active.test_metrics as Record<string, unknown> : null;
  const rawMetrics = metrics?.raw_jev && typeof metrics.raw_jev === "object" ? metrics.raw_jev as Record<string, unknown> : null;
  const correctedMetrics = metrics?.candidate && typeof metrics.candidate === "object" ? metrics.candidate as Record<string, unknown> : null;
  const priorMetrics = metrics?.training_prior_baseline && typeof metrics.training_prior_baseline === "object" ? metrics.training_prior_baseline as Record<string, unknown> : null;
  const metric = (value: unknown) => typeof value === "number" ? value.toFixed(3) : "—";
  return <section className="jev-learning-card" aria-label={t("Jev 本地校准器学习状态")}>
    <div className="section-heading"><div><p className="eyebrow">{<Tx text={"Jev 本地校准"} />}</p><h2>{active ? t("已启用经过时间外验证的校准器") : t("正在积累可训练的真实预测结果")}</h2></div><span>{active ? t("已启用") : t("收集样本中")}</span></div>
    <p>{current ? `${current.actual_model} · ${t("问题版本")} ${current.question_version}` : t("当前股票还没有可统计的 Jev 预测组。")} {t("校准器只学习如何调整 Jev 的三类概率，不修改 Jev 本身，也不声称改变它的内部参数。")}</p>
    <div className="overview-metrics jev-learning-metrics">
      <article><span>{<Tx text={"当前预测组"} />}</span><strong>{current?.forecast_roots ?? 0}</strong><small>{<Tx text={"独立根预测"} />}</small></article>
      <article><span>{<Tx text={"已到期并评分"} />}</span><strong>{current?.matured_roots ?? 0} / {status.required_mature_roots}</strong><small>{locale === "en-US" ? `At least ${status.minimum_validation_months} months of out-of-time validation` : `至少 ${status.minimum_validation_months} 个月时间外验证`}</small></article>
      <article><span>{<Tx text={"待到期"} />}</span><strong>{current?.pending_roots ?? 0}</strong><small>{<Tx text={"预测目标为 20 个交易日"} />}</small></article>
      <article><span>{<Tx text={"历史回放排除"} />}</span><strong>{status.historical_replay_mature_roots_excluded}</strong><small>{<Tx text={"不进入实时校准训练"} />}</small></article>
    </div>
    {active && <div className="jev-learning-validation"><strong>{<Tx text={"最近校准器的封存时间外结果"} />}</strong><span>{t("原始 Jev Brier")} {metric(rawMetrics?.brier)} → {t("校准后")} {metric(correctedMetrics?.brier)}</span><span>{t("训练期类别基线 Brier")} {metric(priorMetrics?.brier)} → {t("校准后")} {metric(correctedMetrics?.brier)}</span><span>{t("原始 Log loss")} {metric(rawMetrics?.log_loss)} → {t("校准后")} {metric(correctedMetrics?.log_loss)}</span><small>{<Tx text={"启用条件：按月份分块的 95% 置信区间显示 Brier 同时优于 Jev 和训练期类别基线，且 Log loss 没有明显退化。新的成熟结果会进入下一轮验证。"} />}</small></div>}
    {!active && <p className="jev-learning-note">{<Tx text={"校准器目前没有启用，所以新预测继续显示原始 Jev 概率。只有足够的真实观察结果通过时间顺序留出验证后才会自动启用；历史回放和同一目标的修订不会混入训练。"} />}</p>}
  </section>;
}
function WorkspacePageHeader({ symbol, section, recordCount, supported }: { symbol: string; section: WorkspaceSection; recordCount: number | null; supported: boolean }) {
  const { t } = useLocale();
  const title = t(WORKSPACE_SECTION_TITLE[section]);
  return <header className="workspace-heading">
    <div><p className="eyebrow">{symbol} / {title}</p><h1 id="workspace-title">{title}</h1></div>
    <div className="workspace-context"><span className={`live-dot ${supported ? "" : "muted"}`} aria-hidden="true" /><span>{supported ? t("支持新建研究") : t("可查看已有研究")}</span>{recordCount !== null && <span className="workspace-record-count">{recordCount} {t("项独立预测")}</span>}</div>
  </header>;
}

function WorkspaceIntro({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  const { t } = useLocale();
  return <section className="workspace-intro">
    <div><p className="eyebrow">{t(eyebrow)}</p><h2>{t(title)}</h2><p>{t(description)}</p></div>
    {action && <div className="workspace-intro-action">{action}</div>}
  </section>;
}

function Shell({ activeSection, navigate, symbol, onSelectStock, children }: { activeSection: WorkspaceSection; navigate: (section: WorkspaceSection) => void; symbol: string; onSelectStock: (ticker: SupportedSymbol) => void; children: ReactNode }) {
  const { locale, setLocale, t } = useLocale();
  const nav: Array<{ id: WorkspaceSection; label: string; hint: string; icon: string }> = [
    { id: "overview", label: t("总览"), hint: t("研究队列"), icon: "◌" },
    { id: "forecast", label: t("预测"), hint: t("版本与价格"), icon: "⌁" },
    { id: "evidence", label: t("证据"), hint: t("预测引用与事件"), icon: "◇" },
    { id: "materials", label: t("材料库"), hint: t("AI 分析与原文"), icon: "▤" },
    { id: "revisions", label: t("预测修订"), hint: t("材料关联"), icon: "↗" },
    { id: "evaluation", label: t("到期评估"), hint: t("同组样本"), icon: "≋" },
    { id: "replay", label: t("历史回放"), hint: t("生成已到期样本"), icon: "↺" },
  ];
  return <div className="app-frame">
    <aside className="app-rail" aria-label={t("主要导航")}>
      <a className="workspace-brand" href="#overview" onClick={() => navigate("overview")} aria-label={`Market Evidence Agent · ${t("总览")}`}><span className="brand-mark">ME</span><span>market<br /><em>evidence</em></span></a>
      <div className="rail-context"><span>{<Tx text={"当前股票"} />}</span><strong>{symbol}</strong><small>{<Tx text={"Agent 研究工作台"} />}</small></div>
      <nav className="workspace-nav">
        {nav.map((item) => <button key={item.id} type="button" className={activeSection === item.id ? "active" : ""} aria-current={activeSection === item.id ? "page" : undefined} onClick={() => navigate(item.id)}><span aria-hidden="true">{item.icon}</span><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>)}
      </nav>
      <div className="rail-bottom"><span className="rail-review-dot" aria-hidden="true" />{<Tx text={"判断可回到原始来源查看"} />}</div>
    </aside>
    <div className="app-content">
      <header className="topbar">
      <div className="topbar-path"><span>{<Tx text={"研究工作台"} />}</span><b>/</b><strong>{symbol}</strong></div>
        <div className="toolbar-controls">
        <div className="symbol-form">
          <label htmlFor="symbol">{<Tx text={"切换股票"} />}</label>
          <select id="symbol" value={symbol} onChange={(event) => onSelectStock(event.target.value as SupportedSymbol)} aria-label={t("切换股票")}>
            {SUPPORTED_SYMBOLS.map((ticker) => <option key={ticker} value={ticker}>{ticker}</option>)}
          </select>
        </div>
        <div className="language-form">
          <label htmlFor="locale">{t("语言")}</label>
          <select id="locale" value={locale} onChange={(event) => setLocale(event.target.value as "zh-CN" | "en-US")} aria-label={t("语言")}>
            <option value="zh-CN">中文</option>
            <option value="en-US">English</option>
          </select>
        </div>
        </div>
      </header>
      {children}
      <footer>{<Tx text={"Market Evidence Agent · 本地研究记录 · 研究判断可追溯到原始来源"} />}</footer>
    </div>
  </div>;
}

function Loading({ symbol }: { symbol: string }) {
  const { t } = useLocale();
  return <main className="state-card" aria-live="polite" aria-busy="true"><span className="loading-mark" aria-hidden="true" /><p className="kicker">{<Tx text={"研究工作台"} />}</p><h1>{t("正在读取")} {symbol} {t("的研究资料")}</h1><p>{<Tx text={"正在读取本地预测、SEC 目录和行情。"} />}</p></main>;
}

function isSupportedSymbol(symbol: string): symbol is SupportedSymbol { return (SUPPORTED_SYMBOLS as readonly string[]).includes(symbol); }
function sectionFromHash(): WorkspaceSection {
  const value = window.location.hash.replace(/^#/, "").split("/", 1)[0];
  return (["overview", "forecast", "evidence", "materials", "revisions", "evaluation", "replay"] as const).includes(value as WorkspaceSection)
    ? value as WorkspaceSection
    : "overview";
}
function actionMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return translatePhrase(fallback);
  if (error.message.includes("SEC_EDGAR_USER_AGENT")) return translatePhrase("尚未配置 SEC 联系邮箱。请先按 README 配置后重试。");
  return translatePhrase(error.message);
}
function formatPrice(value: number) { return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value); }
function formatReturn(value: number) { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`; }
function formatPercent(value: number) { return `${(value * 100).toFixed(1)}%`; }
function modelStatusLabel(status: string) {
  if (status === "research_only") return translatePhrase("研究结果（未输出概率）");
  if (status === "experimental_jev") return translatePhrase("Jev 实验模型");
  if (status === "experimental_joint") return translatePhrase("联合研究模型");
  if (status === "baseline_only") return translatePhrase("基线模型");
  return translatePhrase("模型状态未记录");
}
function monitorHealthLabel(status: string) {
  if (status === "healthy") return translatePhrase("监控正常");
  if (status === "delayed") return translatePhrase("监控延迟");
  if (status === "degraded") return translatePhrase("监控异常");
  return translatePhrase("尚无记录");
}
function signedPoints(value: number) { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}pp`; }
function probabilityLabel(key: "bearish" | "neutral" | "bullish") { return translatePhrase(({ bearish: "看跌", neutral: "中性", bullish: "看涨" })[key]); }
function formatDateTime(value: string, locale: "zh-CN" | "en-US" = getLocalePreference()) { return formatLocalizedDateTime(value, locale); }
function formatUtc(value: string) { return formatDateTime(value); }
