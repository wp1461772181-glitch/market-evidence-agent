import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, fetchFilingContent, getFilingInventory, getMaterialLibrary, getUploadedEvidence, getV2Evaluations, getV2ForecastRoots, getV2MonitorStatus, getV2Prices, getV2Workspace, reviewFiling, scanOfficialFilings, uploadEvidence } from "./api";
import type { FilingContent, FilingInventory, FilingReview, OfficialFiling, PriceHistory, UploadedEvidence } from "./types";
import { V2ForecastWorkspace } from "./V2ForecastWorkspace";
import { MaterialLibrary } from "./MaterialLibrary";

const DEFAULT_SYMBOL = "AAPL";
const SUPPORTED_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"] as const;
type SupportedSymbol = typeof SUPPORTED_SYMBOLS[number];
type ActionState = { kind: "idle" } | { kind: "running" } | { kind: "success"; message: string } | { kind: "error"; message: string };
type FilingState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; data: FilingInventory } | { kind: "error"; message: string };
type FilingFormFilter = "all" | "10-K" | "10-Q" | "8-K";
type FilingReviewFilter = "all" | "pending" | "accepted" | "rejected";
type UploadActionState = ActionState;
type WorkspaceSection = "overview" | "forecast" | "evidence" | "materials" | "revisions" | "evaluation";
const WORKSPACE_SECTION_TITLE: Record<WorkspaceSection, string> = {
  overview: "研究总览", forecast: "预测工作台", evidence: "证据中心", materials: "材料库", revisions: "预测修订", evaluation: "到期评估",
};

const INITIAL_FILING_COUNT = 5;

export function App() {
  const [input, setInput] = useState(DEFAULT_SYMBOL);
  const [requestedSymbol, setRequestedSymbol] = useState(DEFAULT_SYMBOL);
  const [activeSection, setActiveSection] = useState<WorkspaceSection>(sectionFromHash());
  const [activeData, setActiveData] = useState<{ kind: "loading"; symbol: string } | { kind: "ready"; symbol: string; filings: FilingState; priceHistory: PriceHistory | null; priceError: string | null }>({ kind: "loading", symbol: DEFAULT_SYMBOL });
  const [retryKey, setRetryKey] = useState(0);
  const [scanAction, setScanAction] = useState<ActionState>({ kind: "idle" });
  const requestVersion = useRef(0);
  const activeSymbolRef = useRef(requestedSymbol);
  activeSymbolRef.current = requestedSymbol;

  useEffect(() => {
    const onHashChange = () => setActiveSection(sectionFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function navigate(section: WorkspaceSection) {
    if (section === activeSection) return;
    window.location.hash = section;
    setActiveSection(section);
  }

  function openStock(ticker: SupportedSymbol) {
    setInput(ticker);
    setScanAction({ kind: "idle" });
    if (ticker === requestedSymbol) setRetryKey((value) => value + 1);
    else setRequestedSymbol(ticker);
    navigate("overview");
  }

  useEffect(() => {
    const controller = new AbortController();
    const version = ++requestVersion.current;
    setActiveData({ kind: "loading", symbol: requestedSymbol });
    setScanAction({ kind: "idle" });
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

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = input.trim().toUpperCase();
    if (!next) return;
    if (next === requestedSymbol) setRetryKey((value) => value + 1);
    else { setScanAction({ kind: "idle" }); setRequestedSymbol(next); }
  }

  async function startFilingScan() {
    const scanSymbol = requestedSymbol;
    if (!isSupportedSymbol(scanSymbol)) return;
    setScanAction({ kind: "running" });
    try {
      const result = await scanOfficialFilings(scanSymbol);
      if (activeSymbolRef.current !== scanSymbol) return;
      setActiveData((previous) => previous.kind === "ready" && previous.symbol === scanSymbol ? { ...previous, filings: { kind: "ready", data: result } } : previous);
      setScanAction({ kind: "success", message: `扫描完成：发现 ${result.discovered_count} 份，新增 ${result.created_count} 份。` });
    } catch (error: unknown) {
      if (activeSymbolRef.current !== scanSymbol) return;
      setScanAction({ kind: "error", message: actionMessage(error, "扫描官方申报失败。") });
    }
  }

  function updateFilingInventory(filing: OfficialFiling) {
    if (activeSymbolRef.current !== requestedSymbol) return;
    const update = (previous: FilingState): FilingState => {
      if (previous.kind !== "ready") return previous;
      const found = previous.data.filings.some((item) => item.accession_number === filing.accession_number);
      return {
        kind: "ready",
        data: {
          ...previous.data,
          filings: found
            ? previous.data.filings.map((item) => item.accession_number === filing.accession_number ? { ...item, ...filing } : item)
            : [...previous.data.filings, filing],
        },
      };
    };
    setActiveData((previous) => previous.kind === "ready" && previous.symbol === requestedSymbol ? { ...previous, filings: update(previous.filings) } : previous);
  }

  const shellProps = { input, setInput, submit, activeSection, navigate, symbol: requestedSymbol };
  if (activeSection === "materials") return <Shell {...shellProps}><MaterialLibrary key={requestedSymbol} symbol={requestedSymbol} onScan={async () => { await startFilingScan(); setRetryKey((value) => value + 1); }} /></Shell>;
  if (activeData.kind === "loading" || activeData.symbol !== requestedSymbol) return <Shell {...shellProps}><Loading symbol={requestedSymbol} /></Shell>;
  const supported = isSupportedSymbol(requestedSymbol);
  const revisionsNavigate = activeSection === "revisions" ? "revisions" : "forecast";
  return <Shell {...shellProps}>
    <main className="workspace" aria-labelledby="workspace-title">
      <WorkspacePageHeader symbol={requestedSymbol} section={activeSection} recordCount={null} supported={supported} />
      {activeSection === "overview" && <CurrentOverviewWorkspace symbol={requestedSymbol} currentFilings={activeData.filings} priceHistory={activeData.priceHistory} priceError={activeData.priceError} onNavigate={navigate} onOpenStock={openStock} />}
      {(activeSection === "forecast" || activeSection === "revisions") && <section className="workspace-stack">
        {activeSection === "revisions" && <WorkspaceIntro eyebrow="预测与修订" title="从当前版本发起材料修订" description="选择当前预测、版本和新材料，生成保留版本记录的修订。历史版本不会被覆盖。" />}
        <V2ForecastWorkspace key={`${requestedSymbol}-${revisionsNavigate}`} symbol={requestedSymbol} filings={activeData.filings.kind === "ready" ? activeData.filings.data : null} priceHistory={activeData.priceHistory} />
        {activeData.priceError && <p className="inventory-status inventory-error" role="status">行情暂不可用：{activeData.priceError}</p>}
      </section>}
      {activeSection === "evidence" && <section className="workspace-stack">
        <WorkspaceIntro eyebrow="来源与核验" title="SEC 证据中心" description="保留官方文件扫描、原文读取和人工来源核验；媒体材料可上传并记录你的星级判断。" action={<button className="primary-action" disabled={!supported || scanAction.kind === "running"} onClick={startFilingScan}>{scanAction.kind === "running" ? "正在扫描…" : "扫描官方申报"}</button>} />
        <ActionNotice state={scanAction} />
        {!supported && <WorkspaceRestriction />}
        <section className="evidence-layout">
          <EvidenceWorkflowPanel symbol={requestedSymbol} supported={supported} />
          <FilingInventoryPanel symbol={requestedSymbol} state={activeData.filings} onInventoryChanged={updateFilingInventory} />
        </section>
      </section>}
      {activeSection === "evaluation" && <V2EvaluationWorkspace key={requestedSymbol} symbol={requestedSymbol} />}
    </main>
  </Shell>;
}

function CurrentOverviewWorkspace({ symbol, currentFilings, priceHistory, priceError, onNavigate, onOpenStock }: { symbol: string; currentFilings: FilingState; priceHistory: PriceHistory | null; priceError: string | null; onNavigate: (section: WorkspaceSection) => void; onOpenStock: (ticker: SupportedSymbol) => void }) {
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
  const pendingSec = currentFilings.kind === "ready" ? currentFilingsList.filter((filing) => filingReviewBucket(filing) === "pending").length : null;
  return <section className="workspace-stack overview-workspace">
    <WorkspaceIntro eyebrow="研究总览" title="从单股研究队列开始" description="队列汇总独立预测、官方资料和已保存材料。没有预测时仍可上传材料、读取 SEC 原文并开始新研究。" />
    <section className="overview-focus" aria-label={`${symbol} 当前研究状态`}>
      <div><p className="eyebrow">当前股票 / {symbol}</p><h2>{currentWorkspaceState === "loading" ? "正在读取研究状态" : currentWorkspaceState === "error" ? "研究状态暂不可用" : currentVersion ? `预测版本 ${currentVersion.version_no}` : "可开始一项新研究"}</h2><p>{currentWorkspaceState === "loading" ? "正在读取当前预测与研究记录。" : currentWorkspaceState === "error" ? "当前预测状态读取失败，请稍后重试。" : currentVersion ? `最近决策时间 ${formatUtc(currentVersion.decision_at)}；选择预测区查看简报、固定目标和行情。` : "目前没有当前预测版本。历史行情与材料仍可查看，也可以直接创建预测。"}</p></div>
      <div className="overview-focus-actions"><button className="primary-action" disabled={currentWorkspaceState === "loading"} onClick={() => onNavigate("forecast")}>{currentWorkspaceState === "loading" ? "正在读取…" : currentVersion ? "打开预测" : currentWorkspaceState === "ready" ? "创建预测" : "查看预测工作台"}</button><button className="secondary-action" onClick={() => onNavigate("materials")}>查看材料库</button></div>
    </section>
    <section className="overview-metrics" aria-label="当前研究状态">
      <article><span>独立预测数</span><strong>{current?.roots?.roots.length ?? "—"}</strong><small>{current?.workspace?.pending_job_count ? `${current.workspace.pending_job_count} 项任务处理中` : "按当前预测记录统计"}</small></article>
      <article><span>材料总数</span><strong>{current?.materials ?? "—"}</strong><small>已保存官方和上传材料</small></article>
      <article><span>SEC 待核验</span><strong>{pendingSec ?? "—"}</strong><small>{currentFilings.kind === "error" ? "官方资料目录暂不可用" : "当前股票的官方来源"}</small></article>
      <article><span>监控状态</span><strong>{monitor.kind === "loading" ? "正在读取" : monitor.kind === "error" ? "暂不可用" : monitorHealthLabel(monitor.data.health)}</strong><small>{monitor.kind === "loading" ? "正在读取监控记录" : monitor.kind === "error" ? "无法读取监控状态" : monitor.data.last_success?.completed_at ? `最近成功 ${formatUtc(monitor.data.last_success.completed_at)}` : "尚无成功运行记录"}</small></article>
    </section>
    <section className="universe-section" aria-labelledby="universe-title"><div className="section-heading"><div><p className="eyebrow">研究队列</p><h2 id="universe-title">五只关注股票</h2></div><span>按当前研究数据读取</span></div><div className="universe-grid">
      {SUPPORTED_SYMBOLS.map((ticker) => {
        const item = universe[ticker];
        const count = item?.roots?.roots.length;
        const pending = item?.filings?.filings.filter((filing) => filingReviewBucket(filing) === "pending").length;
        return <article className={`universe-card ${ticker === symbol ? "is-current" : ""}`} key={ticker}>
          <div className="universe-card-top"><strong>{ticker}</strong>{ticker === symbol && <span>当前</span>}</div>
          {item?.loading && <p className="universe-pending">正在读取研究状态…</p>}
          {item?.error && <p className="universe-error" role="status">{item.error}</p>}
          {item && !item.loading && <><dl><div><dt>独立预测</dt><dd>{count ?? "—"}</dd></div><div><dt>材料</dt><dd>{item.materials ?? "—"}</dd></div><div><dt>待核验 SEC</dt><dd>{pending ?? "—"}</dd></div></dl><small>{item.workspaceState === "loading" ? "正在读取预测状态" : item.workspaceState === "error" ? "预测状态暂不可用" : item.workspace?.current_version ? `最新版本 ${item.workspace.current_version.version_no} · ${modelStatusLabel(item.workspace.current_version.model_status)}` : "当前没有预测版本"}</small></>}
          <button className="universe-open" type="button" onClick={() => onOpenStock(ticker)}>打开 {ticker} 总览 <span aria-hidden="true">→</span></button>
        </article>;
      })}
    </div></section>
    <div className="overview-shortcuts"><button className="text-button" onClick={() => onNavigate("evidence")}>扫描或核验 SEC</button><button className="text-button" onClick={() => onNavigate("evaluation")}>查看到期评估</button><span>{priceError ? `行情暂不可用：${priceError}` : priceHistory?.latest_trading_date ? `${symbol} 行情截至 ${priceHistory.latest_trading_date}` : "本地没有可展示的行情"}</span></div>
  </section>;
}

function V2EvaluationWorkspace({ symbol }: { symbol: string }) {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "ready"; data: Awaited<ReturnType<typeof getV2Evaluations>> } | { kind: "error"; message: string }>({ kind: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    getV2Evaluations(symbol, controller.signal).then((data) => { if (!controller.signal.aborted) setState({ kind: "ready", data }); }).catch((error: unknown) => { if (!controller.signal.aborted) setState({ kind: "error", message: actionMessage(error, "暂时无法读取到期评估。") }); });
    return () => controller.abort();
  }, [symbol]);
  return <section className="workspace-stack">
    <WorkspaceIntro eyebrow="到期评估" title="按模型分组查看已到期预测" description="每组只统计同一时间模式、模型、服务方和问题版本的代表版本。未到期样本显示为待评，不混入其他模型的指标。" />
    {state.kind === "loading" && <p className="inventory-status">正在读取评估结果…</p>}
    {state.kind === "error" && <p className="inventory-status inventory-error" role="alert">{state.message}</p>}
    {state.kind === "ready" && <div className="model-cohort-list">{state.data.model_cohorts?.length ? state.data.model_cohorts.map((cohort, index) => {
      const versions = cohort.roots.flatMap((root) => root.versions);
      const scored = versions.map((version) => version.latest_evaluation).filter((evaluation) => evaluation?.status === "succeeded");
      const average = (values: Array<number | null | undefined>) => { const valid = values.filter((value): value is number => typeof value === "number"); return valid.length ? (valid.reduce((sum, value) => sum + value, 0) / valid.length).toFixed(3) : "—"; };
      return <article className="model-cohort-card" key={`${cohort.model_status}-${cohort.provider}-${cohort.actual_model}-${cohort.question_version ?? "none"}-${index}`}>
        <div className="section-heading"><div><p className="eyebrow">{cohort.time_mode === "observed" ? "真实观察" : cohort.time_mode === "historical_research" ? "历史研究" : "时间模式未分类"} · {modelStatusLabel(cohort.model_status)}</p><h2>{cohort.provider} / {cohort.actual_model}</h2></div><span>{cohort.status === "available" ? "指标可用" : cohort.status === "pending" ? "等待到期" : "样本不足"}</span></div>
        <p className="fine-print">问题版本：{cohort.question_version ?? "未记录"} · 样本按同组最高版本计数。</p>
        <div className="overview-metrics"><article><span>独立预测样本</span><strong>{cohort.sample.root_denominator}</strong><small>同组代表记录</small></article><article><span>已标注 / 已评分</span><strong>{cohort.sample.labelled_root_count} / {cohort.sample.scored_root_count}</strong><small>{cohort.sample.unscored_root_count} 条尚未评分</small></article><article><span>平均 Brier</span><strong>{average(scored.map((value) => value?.brier_score))}</strong><small>仅已成功评分的版本</small></article><article><span>平均 Log loss</span><strong>{average(scored.map((value) => value?.log_loss))}</strong><small>仅已成功评分的版本</small></article></div>
      </article>;
    }) : <div className="evaluation-empty-state"><h2>{state.data.model_cohorts ? "还没有模型分组评估" : "模型分组数据暂不可用"}</h2><p>这里不会用旧版离线报告或跨模型混合分组填补。预测到期并获得可用行情后，结果会进入对应模型组。</p></div>}</div>}
  </section>;
}
function WorkspacePageHeader({ symbol, section, recordCount, supported }: { symbol: string; section: WorkspaceSection; recordCount: number | null; supported: boolean }) {
  return <header className="workspace-heading">
    <div><p className="eyebrow">{symbol} / {WORKSPACE_SECTION_TITLE[section]}</p><h1 id="workspace-title">{WORKSPACE_SECTION_TITLE[section]}</h1></div>
    <div className="workspace-context"><span className={`live-dot ${supported ? "" : "muted"}`} aria-hidden="true" /><span>{supported ? "支持新建研究" : "可查看已有研究"}</span>{recordCount !== null && <span className="workspace-record-count">{recordCount} 项独立预测</span>}</div>
  </header>;
}

function WorkspaceIntro({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <section className="workspace-intro">
    <div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p>{description}</p></div>
    {action && <div className="workspace-intro-action">{action}</div>}
  </section>;
}

function WorkspaceRestriction() {
  return <p className="workspace-restriction">当前新建研究与 SEC 扫描支持 {SUPPORTED_SYMBOLS.join(" · ")}。</p>;
}

function ActionNotice({ state }: { state: ActionState }) {
  if (state.kind === "idle" || state.kind === "running") return null;
  return <p className={`action-notice ${state.kind}`} role={state.kind === "error" ? "alert" : "status"}>{state.message}</p>;
}

function EvidenceWorkflowPanel({ symbol, supported }: { symbol: string; supported: boolean }) {
  const [uploaded, setUploaded] = useState<{ kind: "loading" } | { kind: "ready"; items: UploadedEvidence[] } | { kind: "error"; message: string }>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    setUploaded({ kind: "loading" });
    getUploadedEvidence(symbol, controller.signal)
      .then((result) => setUploaded({ kind: "ready", items: result.items ?? [] }))
      .catch((error: unknown) => { if (!controller.signal.aborted) setUploaded({ kind: "error", message: actionMessage(error, "暂时无法读取已上传材料。") }); });
    return () => controller.abort();
  }, [symbol]);

  async function handleUpload(input: Parameters<typeof uploadEvidence>[1]) {
    const item = await uploadEvidence(symbol, input);
    setUploaded((previous) => ({ kind: "ready", items: sortUploadedEvidence([item, ...(previous.kind === "ready" ? previous.items : [])]) }));
  }

  return <section className="evidence-workbench evidence-upload-workbench" aria-labelledby="evidence-workbench-title">
    <div className="workbench-heading">
      <div><span className="section-label">媒体材料</span><h3 id="evidence-workbench-title">保存未获官方证实的来源</h3></div>
      <span className="tag">来源记录</span>
    </div>
    <p className="workbench-intro">上传媒体报道、行业消息或其他未获官方确认的信息，并记录来源与初步可信度判断。</p>
    <div className="workbench-grid"><UploadEvidencePanel symbol={symbol} supported={supported} uploaded={uploaded} onUpload={handleUpload} /></div>
  </section>;
}

function UploadEvidencePanel({ symbol, supported, uploaded, onUpload }: {
  symbol: string;
  supported: boolean;
  uploaded: { kind: "loading" } | { kind: "ready"; items: UploadedEvidence[] } | { kind: "error"; message: string };
  onUpload: (input: Parameters<typeof uploadEvidence>[1]) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [publishedAt, setPublishedAt] = useState("");
  const [stars, setStars] = useState(3);
  const [impactSeverity, setImpactSeverity] = useState<"low" | "medium" | "high">("medium");
  const [reason, setReason] = useState("");
  const [action, setAction] = useState<UploadActionState>({ kind: "idle" });
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setFile(null); setTitle(""); setSourceUrl(""); setPublishedAt(""); setStars(3); setImpactSeverity("medium"); setReason(""); setAction({ kind: "idle" });
    if (fileInput.current) fileInput.current.value = "";
  }, [symbol]);

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !isAllowedEvidenceFile(file) || !isHttpsUrl(sourceUrl) || !title.trim() || !publishedAt || !reason.trim()) return;
    setAction({ kind: "running" });
    try {
      await onUpload({ file, title: title.trim(), sourceUrl: sourceUrl.trim(), publishedAt: new Date(publishedAt).toISOString(), credibilityStars: stars, credibilityReason: reason.trim(), impactSeverity });
      setAction({ kind: "success", message: "材料已保存为未获官方证实的媒体证据，可在后续研究中选择。" });
      setFile(null); setTitle(""); setSourceUrl(""); setPublishedAt(""); setStars(3); setImpactSeverity("medium"); setReason("");
      if (fileInput.current) fileInput.current.value = "";
    } catch (error: unknown) {
      setAction({ kind: "error", message: actionMessage(error, "上传材料失败。") });
    }
  }

  const invalidFile = file !== null && !isAllowedEvidenceFile(file);
  return <article className="workbench-card upload-card">
    <span className="action-number">03</span>
    <h3>上传媒体材料</h3>
    <p>用于媒体报道、行业消息或未获官网确认的内容。只接受 TXT、Markdown、PDF，并保留原始 HTTPS 链接与发布时间。</p>
    <form className="evidence-form" onSubmit={submitUpload}>
      <label>材料文件
        <input ref={fileInput} type="file" accept=".txt,.md,.markdown,.pdf,text/plain,text/markdown,application/pdf" required disabled={!supported || action.kind === "running"} onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </label>
      {invalidFile && <p className="content-error" role="alert">请选择 TXT、Markdown 或 PDF 文件。</p>}
      <label>标题<input value={title} required maxLength={240} disabled={!supported || action.kind === "running"} onChange={(event) => setTitle(event.target.value)} placeholder="例如：权威媒体报道产品重大问题" /></label>
      <label>原始 HTTPS 来源<input value={sourceUrl} required type="url" inputMode="url" disabled={!supported || action.kind === "running"} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" /></label>
      <label>发布时间<input value={publishedAt} required type="datetime-local" disabled={!supported || action.kind === "running"} onChange={(event) => setPublishedAt(event.target.value)} /></label>
      <fieldset className="credibility-picker">
        <legend>用户评估的消息可信度</legend>
        <div>{[1, 2, 3, 4, 5].map((value) => <label key={value} title={`${value} 星`}><input type="radio" name={`credibility-${symbol}`} value={value} checked={stars === value} disabled={!supported || action.kind === "running"} onChange={() => setStars(value)} /><span aria-hidden="true">★</span><span className="sr-only">{value} 星</span></label>)}</div>
        <small>{stars} / 5 星 · 记录你的初步判断，不是精确概率，也不代表内容已获官方证实。</small>
      </fieldset>
      <label>潜在影响程度
        <select value={impactSeverity} disabled={!supported || action.kind === "running"} onChange={(event) => setImpactSeverity(event.target.value as "low" | "medium" | "high")}>
          <option value="low">低 · 可能影响有限</option>
          <option value="medium">中 · 值得人工审阅</option>
          <option value="high">高 · 可能显著影响市场预期</option>
        </select>
        <small className="field-note">这是对潜在重要性的判断，不是涨跌方向或概率预测。</small>
      </label>
      <label>评分理由<textarea value={reason} required rows={3} maxLength={1000} disabled={!supported || action.kind === "running"} onChange={(event) => setReason(event.target.value)} placeholder="例如：媒体的一手采访、交叉报道情况，或尚待确认的原因" /></label>
      <button className="action-button" type="submit" disabled={!supported || action.kind === "running" || !file || invalidFile || !isHttpsUrl(sourceUrl) || !title.trim() || !publishedAt || !reason.trim()}>{action.kind === "running" ? "正在保存…" : "保存未证实材料"}</button>
      <ActionNotice state={action} />
    </form>
    <UploadedEvidenceList state={uploaded} />
  </article>;
}

function UploadedEvidenceList({ state }: { state: { kind: "loading" } | { kind: "ready"; items: UploadedEvidence[] } | { kind: "error"; message: string } }) {
  if (state.kind === "loading") return <p className="material-status">正在读取已上传材料…</p>;
  if (state.kind === "error") return <p className="content-error" role="alert">{state.message}</p>;
  if (!state.items.length) return <p className="material-status">还没有手动上传的媒体材料。</p>;
  return <div className="uploaded-list" aria-label="已上传媒体材料">
    <p className="list-caption">已保存 {state.items.length} 份 · 均待进一步核验</p>
    {sortUploadedEvidence(state.items).slice(0, 4).map((item) => <article className="uploaded-item" key={item.id}>
      <div><strong>{item.title}</strong><span aria-label={`用户评估 ${item.credibility_stars} 星`}>{"★".repeat(clampStars(item.credibility_stars))}{"☆".repeat(5 - clampStars(item.credibility_stars))}</span></div>
      <small>{formatDateTime(item.published_at)} · 未获官方证实{item.impact_severity ? ` · 潜在影响${severityLabel(item.impact_severity)}` : ""}</small>
      <SafeLink href={item.source_url}>打开原始来源 <span>↗</span></SafeLink>
    </article>)}
  </div>;
}

function FilingInventoryPanel({ symbol, state, onInventoryChanged }: { symbol: string; state: FilingState; onInventoryChanged: (filing: OfficialFiling) => void }) {
  const [content, setContent] = useState<Record<string, { kind: "loading" } | { kind: "ready"; data: FilingContent } | { kind: "error"; message: string }>>({});
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, { decision: "accepted" | "rejected"; note: string }>>({});
  const [reviews, setReviews] = useState<Record<string, { kind: "loading" } | { kind: "ready"; data: FilingReview } | { kind: "error"; message: string }>>({});
  const [formFilter, setFormFilter] = useState<FilingFormFilter>("all");
  const [reviewFilter, setReviewFilter] = useState<FilingReviewFilter>("all");
  const [visibleCount, setVisibleCount] = useState(INITIAL_FILING_COUNT);
  useEffect(() => setContent({}), [symbol]);
  useEffect(() => {
    setReviewDrafts({});
    setReviews({});
    setFormFilter("all");
    setReviewFilter("all");
    setVisibleCount(INITIAL_FILING_COUNT);
  }, [symbol]);
  async function loadContent(accessionNumber: string) {
    setContent((previous) => ({ ...previous, [accessionNumber]: { kind: "loading" } }));
    try {
      const data = await fetchFilingContent(symbol, accessionNumber);
      setContent((previous) => ({ ...previous, [accessionNumber]: { kind: "ready", data } }));
      onInventoryChanged(data);
    } catch (error: unknown) {
      setContent((previous) => ({ ...previous, [accessionNumber]: { kind: "error", message: actionMessage(error, "无法读取这份 SEC 原文摘要。") } }));
    }
  }
  function draftFor(accessionNumber: string) { return reviewDrafts[accessionNumber] ?? { decision: "accepted" as const, note: "" }; }
  function updateDraft(accessionNumber: string, update: Partial<{ decision: "accepted" | "rejected"; note: string }>) {
    setReviewDrafts((previous) => ({ ...previous, [accessionNumber]: { ...draftFor(accessionNumber), ...update } }));
  }
  async function submitReview(accessionNumber: string) {
    const draft = draftFor(accessionNumber);
    if (!draft.note.trim()) return;
    setReviews((previous) => ({ ...previous, [accessionNumber]: { kind: "loading" } }));
    try {
      const data = await reviewFiling(symbol, accessionNumber, draft.decision, draft.note.trim());
      setReviews((previous) => ({ ...previous, [accessionNumber]: { kind: "ready", data } }));
      onInventoryChanged(data);
    } catch (error: unknown) {
      setReviews((previous) => ({ ...previous, [accessionNumber]: { kind: "error", message: actionMessage(error, "保存人工核验结果失败。") } }));
    }
  }
  if (state.kind === "idle") return null;
  const displayedFilings = state.kind === "ready"
    ? state.data.filings
      .map((baseFiling) => {
        const review = reviews[baseFiling.accession_number];
        return review?.kind === "ready" ? review.data : baseFiling;
      })
      .sort(sortFilingsNewestFirst)
    : [];
  const filteredFilings = displayedFilings.filter((filing) => (
    (formFilter === "all" || filing.form === formFilter)
    && (reviewFilter === "all" || filingReviewBucket(filing) === reviewFilter)
  ));
  const pendingReviewCount = displayedFilings.filter((filing) => filingReviewBucket(filing) === "pending").length;
  const visibleFilings = filteredFilings.slice(0, visibleCount);
  const hasMore = visibleCount < filteredFilings.length;
  return <section className="filing-inventory" aria-labelledby="filing-title">
    <div className="inventory-heading"><div><span className="section-label">官方资料目录</span><h3 id="filing-title">{symbol} 的 SEC 申报</h3></div><span className="review-flag">{pendingReviewCount ? `${pendingReviewCount} 份待人工核验` : "已全部人工核验"}</span></div>
    {state.kind === "loading" && <p className="inventory-status">正在读取已发现的官方申报…</p>}
    {state.kind === "error" && <p className="inventory-status inventory-error">{state.message}</p>}
    {state.kind === "ready" && (displayedFilings.length
      ? <>
          <div className="filing-controls" aria-label="筛选 SEC 申报">
            <label>
              <span>文件类型</span>
              <select value={formFilter} onChange={(event) => { setFormFilter(event.target.value as FilingFormFilter); setVisibleCount(INITIAL_FILING_COUNT); }}>
                <option value="all">全部类型</option>
                <option value="10-K">10-K</option>
                <option value="10-Q">10-Q</option>
                <option value="8-K">8-K</option>
              </select>
            </label>
            <label>
              <span>人工核验</span>
              <select value={reviewFilter} onChange={(event) => { setReviewFilter(event.target.value as FilingReviewFilter); setVisibleCount(INITIAL_FILING_COUNT); }}>
                <option value="all">全部状态</option>
                <option value="pending">待核验</option>
                <option value="accepted">已接受</option>
                <option value="rejected">已驳回</option>
              </select>
            </label>
            <p className="filing-count" aria-live="polite">共 {displayedFilings.length} 份 · 当前筛选 {filteredFilings.length} 份 · 显示 {visibleFilings.length} 份</p>
          </div>
          {filteredFilings.length
            ? <div className="filing-list">
              {visibleFilings.map((filing) => {
                const review = reviews[filing.accession_number];
                return <article className="filing-row" key={filing.accession_number}>
            <div><strong>{filing.form}</strong><span>{filing.filed_at}</span></div>
            <p>{filing.primary_document}</p>
            <SafeLink href={filing.source_url}>SEC 原始文件 <span>↗</span></SafeLink>
            <small>{filing.accepted_at ? `SEC 受理 ${formatSecAcceptedAt(filing.accepted_at)} · ` : ""}已于 {formatDateTime(filing.observed_at)} 发现 · {filingStatus(filing.content_status)}</small>
            <FilingContentPreview filing={filing} state={content[filing.accession_number]} onLoad={() => loadContent(filing.accession_number)} />
            <FilingReviewPanel filing={filing} draft={draftFor(filing.accession_number)} state={review} onDraft={(update) => updateDraft(filing.accession_number, update)} onSubmit={() => submitReview(filing.accession_number)} />
          </article>;
              })}
            </div>
            : <p className="inventory-status">当前筛选没有匹配的已保存申报。</p>}
          {filteredFilings.length > INITIAL_FILING_COUNT && <div className="filing-pagination">
            {hasMore
              ? <button className="text-button" type="button" onClick={() => setVisibleCount((count) => count + INITIAL_FILING_COUNT)}>显示更多（余 {filteredFilings.length - visibleFilings.length} 份）</button>
              : <button className="text-button" type="button" onClick={() => setVisibleCount(INITIAL_FILING_COUNT)}>收起至最近 {INITIAL_FILING_COUNT} 份</button>}
          </div>}
        </>
      : <p className="inventory-status">尚未发现已保存的官方申报。点击“扫描官方申报”后会在这里列出文件。</p>)}
    <p className="fine-print inventory-note">目录记录需要先读取正文才能分析；某次预测实际使用的材料可在研究简报中查看。</p>
  </section>;
}

function FilingReviewPanel({ filing, draft, state, onDraft, onSubmit }: { filing: OfficialFiling; draft: { decision: "accepted" | "rejected"; note: string }; state: { kind: "loading" } | { kind: "ready"; data: FilingReview } | { kind: "error"; message: string } | undefined; onDraft: (update: Partial<{ decision: "accepted" | "rejected"; note: string }>) => void; onSubmit: () => void }) {
  const finalized = filing.review_status === "accepted" || filing.review_status === "rejected";
  if (finalized) return <p className={`review-result ${filing.review_status}`}><strong>人工来源核验：{filing.review_status === "accepted" ? "接受" : "驳回"}</strong>{filing.human_review_note && <span> · {filing.human_review_note}</span>}<small>{filing.reviewed_at ? `核验于 ${formatDateTime(filing.reviewed_at)} · ` : ""}{filing.review_scope_note ?? "仅确认来源相关性；不验证观点、方向或预测。"}</small></p>;
  return <details className="filing-review">
    <summary>人工核验这份来源</summary>
    <p>仅记录这份 SEC 来源是否与研究相关，不会验证模型观点，也不会自动用于预测。</p>
    <div className="review-decisions" role="group" aria-label="人工核验结论">
      <button type="button" className={draft.decision === "accepted" ? "selected" : ""} onClick={() => onDraft({ decision: "accepted" })}>接受来源</button>
      <button type="button" className={draft.decision === "rejected" ? "selected reject" : ""} onClick={() => onDraft({ decision: "rejected" })}>驳回来源</button>
    </div>
    <label>核验备注<textarea value={draft.note} onChange={(event) => onDraft({ note: event.target.value })} placeholder="说明该官方文件为何与本研究相关或不相关" rows={3} /></label>
    <button type="button" className="text-button review-submit" disabled={state?.kind === "loading" || !draft.note.trim()} onClick={onSubmit}>{state?.kind === "loading" ? "正在保存…" : "保存人工结论"}</button>
    {state?.kind === "error" && <p className="content-error" role="alert">{state.message}</p>}
  </details>;
}

function FilingContentPreview({ filing, state, onLoad }: { filing: FilingInventory["filings"][number]; state: { kind: "loading" } | { kind: "ready"; data: FilingContent } | { kind: "error"; message: string } | undefined; onLoad: () => void }) {
  const loaded = state?.kind === "ready" ? state.data : null;
  return <div className="filing-content">
    {!loaded && <button className="text-button" disabled={state?.kind === "loading"} onClick={onLoad}>{state?.kind === "loading" ? "正在读取原文摘要…" : "读取原文摘要"}</button>}
    {state?.kind === "error" && <p className="content-error" role="alert">{state.message}</p>}
    {loaded?.content_status === "unavailable" && <p className="content-error">{loaded.content_error ?? "原文暂时不可用。"}</p>}
    {loaded?.content_status === "fetched" && loaded.content_excerpt && <details className="filing-excerpt">
      <summary>原文摘要（待人工核验）</summary>
      <p>{excerptForDisplay(loaded.content_excerpt)}</p>
      <small>{loaded.content_truncated ? "服务返回的是受限摘录" : "已读取的原文摘要"}{loaded.content_excerpt.length > 6000 ? "；此页仅显示前 6000 字符，全文请打开 SEC 原始文件" : ""} · 内容指纹 {loaded.content_excerpt_sha256?.slice(0, 12) ?? "—"} · 不会自动用于预测</small>
    </details>}
  </div>;
}

function Shell({ input, setInput, submit, activeSection, navigate, symbol, children }: { input: string; setInput: (value: string) => void; submit: (event: FormEvent) => void; activeSection: WorkspaceSection; navigate: (section: WorkspaceSection) => void; symbol: string; children: ReactNode }) {
  const nav: Array<{ id: WorkspaceSection; label: string; hint: string; icon: string }> = [
    { id: "overview", label: "总览", hint: "研究队列", icon: "◌" },
    { id: "forecast", label: "预测", hint: "版本与价格", icon: "⌁" },
    { id: "evidence", label: "证据", hint: "SEC 与核验", icon: "◇" },
    { id: "materials", label: "材料库", hint: "AI 分析与原文", icon: "▤" },
    { id: "revisions", label: "预测修订", hint: "材料关联", icon: "↗" },
    { id: "evaluation", label: "到期评估", hint: "同组样本", icon: "≋" },
  ];
  return <div className="app-frame">
    <aside className="app-rail" aria-label="主要导航">
      <a className="workspace-brand" href="#overview" onClick={() => navigate("overview")} aria-label="Market Evidence Agent 总览"><span className="brand-mark">ME</span><span>market<br /><em>evidence</em></span></a>
      <div className="rail-context"><span>当前股票</span><strong>{symbol}</strong><small>Agent 研究工作台</small></div>
      <nav className="workspace-nav">
        {nav.map((item) => <button key={item.id} type="button" className={activeSection === item.id ? "active" : ""} aria-current={activeSection === item.id ? "page" : undefined} onClick={() => navigate(item.id)}><span aria-hidden="true">{item.icon}</span><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>)}
      </nav>
      <div className="rail-bottom"><span className="rail-review-dot" aria-hidden="true" />所有结论需人工审阅</div>
    </aside>
    <div className="app-content">
      <header className="topbar">
      <div className="topbar-path"><span>研究工作台</span><b>/</b><strong>{symbol}</strong></div>
        <form className="symbol-form" onSubmit={submit}>
          <label htmlFor="symbol">切换股票</label>
          <input id="symbol" value={input} onChange={(event) => setInput(event.target.value.toUpperCase())} maxLength={5} autoComplete="off" spellCheck="false" aria-label="股票代码" />
          <button type="submit">打开</button>
        </form>
      </header>
      {children}
      <footer>Market Evidence Agent · 本地研究记录 · 所有结论均须人工审阅</footer>
    </div>
  </div>;
}

function Loading({ symbol }: { symbol: string }) {
  return <main className="state-card" aria-live="polite" aria-busy="true"><span className="loading-mark" aria-hidden="true" /><p className="kicker">研究工作台</p><h1>正在读取 {symbol} 的研究资料</h1><p>正在读取本地预测、SEC 目录和行情。</p></main>;
}

function SafeLink({ href, children }: { href: string; children: ReactNode }) { return /^https:\/\//i.test(href) ? <a className="source-link" href={href} target="_blank" rel="noreferrer">{children}</a> : <span className="source-link disabled">来源链接不可用</span>; }
function isSupportedSymbol(symbol: string): symbol is SupportedSymbol { return (SUPPORTED_SYMBOLS as readonly string[]).includes(symbol); }
function sectionFromHash(): WorkspaceSection {
  const value = window.location.hash.replace(/^#/, "");
  return (["overview", "forecast", "evidence", "materials", "revisions", "evaluation"] as const).includes(value as WorkspaceSection)
    ? value as WorkspaceSection
    : "overview";
}
function isHttpsUrl(value: string) {
  try { return new URL(value.trim()).protocol === "https:"; } catch { return false; }
}
function isAllowedEvidenceFile(file: File) {
  return /\.(txt|md|markdown|pdf)$/i.test(file.name)
    || ["text/plain", "text/markdown", "application/pdf"].includes(file.type);
}
function clampStars(value: number) { return Math.min(5, Math.max(1, Math.round(value || 1))); }
function severityLabel(value: "low" | "medium" | "high") { return { low: "低", medium: "中", high: "高" }[value]; }
function sortUploadedEvidence(items: UploadedEvidence[]) {
  return [...items].sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at) || (b.id ?? "").localeCompare(a.id ?? ""));
}
function actionMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return fallback;
  if (error.message.includes("SEC_EDGAR_USER_AGENT")) return "尚未配置 SEC 联系邮箱。请先按 README 配置后重试。";
  return error.message;
}
function filingStatus(status: OfficialFiling["content_status"]) { return status === "fetched" ? "原文摘要已读取，仍待核验" : status === "unavailable" ? "原文摘要暂不可用" : "仅发现目录，尚未读取原文"; }
function filingReviewBucket(filing: OfficialFiling): Exclude<FilingReviewFilter, "all"> {
  return filing.review_status === "accepted" || filing.review_status === "rejected" ? filing.review_status : "pending";
}
function sortFilingsNewestFirst(a: OfficialFiling, b: OfficialFiling) {
  return b.filed_at.localeCompare(a.filed_at) || b.accession_number.localeCompare(a.accession_number);
}
function formatPrice(value: number) { return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value); }
function formatReturn(value: number) { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`; }
function formatPercent(value: number) { return `${(value * 100).toFixed(1)}%`; }
function modelStatusLabel(status: string) {
  if (status === "research_only") return "研究结果（未输出概率）";
  if (status === "experimental_jev") return "Jev 实验模型";
  if (status === "experimental_joint") return "联合研究模型";
  if (status === "baseline_only") return "基线模型";
  return "模型状态未记录";
}
function monitorHealthLabel(status: string) {
  if (status === "healthy") return "监控正常";
  if (status === "delayed") return "监控延迟";
  if (status === "degraded") return "监控异常";
  return "尚无记录";
}
function signedPoints(value: number) { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}pp`; }
function probabilityLabel(key: "bearish" | "neutral" | "bullish") { return { bearish: "看跌", neutral: "中性", bullish: "看涨" }[key]; }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(value)); }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(new Date(value)); }
function formatUtc(value: string) { return formatDateTime(value); }
function formatSecAcceptedAt(value: string) {
  if (/^\d{14}$/.test(value)) return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)} ${value.slice(8, 10)}:${value.slice(10, 12)} UTC`;
  return Number.isNaN(Date.parse(value)) ? value : formatDateTime(value);
}
function excerptForDisplay(value: string) { return value.length > 6000 ? `${value.slice(0, 6000)}…` : value; }
