import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, createEvidenceRevision, createForecastRun, fetchFilingContent, getDashboard, getEvidenceRevisions, getFilingInventory, getUploadedEvidence, reviewFiling, scanOfficialFilings, uploadEvidence } from "./api";
import type { Claim, DashboardResponse, EvidenceRevision, FilingContent, FilingInventory, FilingReview, OfficialFiling, PriceCandle, PriceHistory, RefreshReport, Snapshot, UploadedEvidence } from "./types";
import { V2ForecastWorkspace } from "./V2ForecastWorkspace";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; data: DashboardResponse }
  | { kind: "empty"; data: DashboardResponse }
  | { kind: "error"; message: string; symbol: string };

const DEFAULT_SYMBOL = "AAPL";
const SUPPORTED_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"] as const;
type SupportedSymbol = typeof SUPPORTED_SYMBOLS[number];
type ActionState = { kind: "idle" } | { kind: "running" } | { kind: "success"; message: string } | { kind: "error"; message: string };
type FilingState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; data: FilingInventory } | { kind: "error"; message: string };
type EvidenceRevisionState = { kind: "loading" } | { kind: "ready"; items: EvidenceRevision[] } | { kind: "error"; message: string };
type FilingFormFilter = "all" | "10-K" | "10-Q" | "8-K";
type FilingReviewFilter = "all" | "pending" | "accepted" | "rejected";
type UploadActionState = ActionState;
type RevisionActionState = ActionState & { revision?: EvidenceRevision };
type WorkspaceSection = "overview" | "forecast" | "evidence" | "revisions" | "evaluation";
const WORKSPACE_SECTION_TITLE: Record<WorkspaceSection, string> = {
  overview: "研究总览", forecast: "预测工作台", evidence: "证据中心", revisions: "预测修订", evaluation: "离线评估",
};

const INITIAL_FILING_COUNT = 5;

export function App() {
  const [input, setInput] = useState(DEFAULT_SYMBOL);
  const [requestedSymbol, setRequestedSymbol] = useState(DEFAULT_SYMBOL);
  const [activeSection, setActiveSection] = useState<WorkspaceSection>(sectionFromHash());
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [evidenceOnly, setEvidenceOnly] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const [forecastAction, setForecastAction] = useState<ActionState>({ kind: "idle" });
  const [scanAction, setScanAction] = useState<ActionState>({ kind: "idle" });
  const [filings, setFilings] = useState<FilingState>({ kind: "idle" });
  const [evidenceRevisions, setEvidenceRevisions] = useState<EvidenceRevisionState>({ kind: "loading" });
  const requestVersion = useRef(0);

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
    if (ticker === requestedSymbol) setRetryKey((value) => value + 1);
    else {
      setForecastAction({ kind: "idle" });
      setScanAction({ kind: "idle" });
      setRequestedSymbol(ticker);
    }
    navigate("overview");
  }

  useEffect(() => {
    const controller = new AbortController();
    const version = ++requestVersion.current;
    setState({ kind: "loading" });
    setSelectedId(null);
    setEvidenceOnly(false);

    getDashboard(requestedSymbol, controller.signal)
      .then((data) => {
        if (version !== requestVersion.current) return;
        if (data.snapshots.length === 0) {
          setState({ kind: "empty", data });
          return;
        }
        setState({ kind: "ready", data });
        setSelectedId(latestSnapshot(data.snapshots).id);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || version !== requestVersion.current) return;
        const message = error instanceof ApiError ? error.message : "无法连接到本地服务。";
        setState({ kind: "error", message, symbol: requestedSymbol });
      });
    return () => controller.abort();
  }, [requestedSymbol, retryKey]);

  useEffect(() => {
    if (!isSupportedSymbol(requestedSymbol)) {
      setFilings({ kind: "idle" });
      return;
    }
    const controller = new AbortController();
    setFilings({ kind: "loading" });
    getFilingInventory(requestedSymbol, controller.signal)
      .then((data) => setFilings({ kind: "ready", data }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFilings({ kind: "error", message: actionMessage(error, "暂时无法读取官方申报清单。") });
      });
    return () => controller.abort();
  }, [requestedSymbol]);

  useEffect(() => {
    const controller = new AbortController();
    setEvidenceRevisions({ kind: "loading" });
    getEvidenceRevisions(requestedSymbol, controller.signal)
      .then((result) => setEvidenceRevisions({ kind: "ready", items: result.revisions ?? [] }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setEvidenceRevisions({ kind: "error", message: actionMessage(error, "暂时无法读取证据修订记录。") });
      });
    return () => controller.abort();
  }, [requestedSymbol]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = input.trim().toUpperCase();
    if (!next) return;
    if (next === requestedSymbol) setRetryKey((value) => value + 1);
    else {
      setForecastAction({ kind: "idle" });
      setScanAction({ kind: "idle" });
      setRequestedSymbol(next);
    }
  }

  async function startForecast() {
    if (!isSupportedSymbol(requestedSymbol)) return;
    setForecastAction({ kind: "running" });
    try {
      const result = await createForecastRun(requestedSymbol);
      const cutoff = result.cutoff_date ? `数据截止 ${result.cutoff_date}` : "新的保存版本";
      const modelLabel = result.model_status === "experimental_offline_model" ? "实验性离线模型预测" : "预测版本";
      setForecastAction({ kind: "success", message: `已生成并保存${modelLabel}（${cutoff}），档案已刷新。` });
      setRetryKey((value) => value + 1);
    } catch (error: unknown) {
      setForecastAction({ kind: "error", message: actionMessage(error, "生成预测失败。") });
    }
  }

  async function startFilingScan() {
    if (!isSupportedSymbol(requestedSymbol)) return;
    setScanAction({ kind: "running" });
    try {
      const result = await scanOfficialFilings(requestedSymbol);
      setFilings({ kind: "ready", data: result });
      setScanAction({ kind: "success", message: `扫描完成：发现 ${result.discovered_count} 份，新增 ${result.created_count} 份。` });
    } catch (error: unknown) {
      setScanAction({ kind: "error", message: actionMessage(error, "扫描官方申报失败。") });
    }
  }

  function updateFilingInventory(filing: OfficialFiling) {
    setFilings((previous) => {
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
    });
  }

  const shellProps = { input, setInput, submit, activeSection, navigate, symbol: requestedSymbol };
  if (state.kind === "loading") return <Shell {...shellProps}><Loading symbol={requestedSymbol} /></Shell>;
  if (state.kind === "error") return <Shell {...shellProps}><ErrorState message={state.message} retry={() => setRetryKey((value) => value + 1)} /></Shell>;
  if (state.kind === "empty") {
    const supported = isSupportedSymbol(requestedSymbol);
    return <Shell {...shellProps}><main className="workspace" aria-labelledby="workspace-title"><WorkspacePageHeader symbol={state.data.symbol} section={activeSection} snapshotCount={state.data.snapshots.length} supported={supported} /><EmptyWorkspace data={state.data} section={activeSection} supported={supported} forecastAction={forecastAction} onForecast={startForecast} scanAction={scanAction} onScan={startFilingScan} filings={filings} revisions={evidenceRevisions} onNavigate={navigate} onOpenStock={openStock} onFilingChanged={updateFilingInventory} /></main></Shell>;
  }

  const { data } = state;
  const manualRevisions = evidenceRevisions.kind === "ready" ? evidenceRevisions.items : [];
  const manualRevisionByChild = new Map(manualRevisions.map((revision) => [revision.revised_snapshot_id, revision]));
  const presentedSnapshots = data.snapshots.map((snapshot) => {
    const revision = manualRevisionByChild.get(snapshot.id);
    return revision ? { ...snapshot, parent_snapshot_id: revision.parent_snapshot_id, revision_reason: "证据修订 · 待人工核验" } : snapshot;
  });
  const selected = presentedSnapshots.find((snapshot) => snapshot.id === selectedId) ?? latestSnapshot(presentedSnapshots);
  const selectedReport = reportForRevisedSnapshot(selected.id, data.refresh_reports);
  const selectedChainReport = reportForSnapshotInChain(selected.id, data.refresh_reports);
  const selectedManualRevision = manualRevisionByChild.get(selected.id);
  const evidenceIds = new Set([...data.refresh_reports.map((report) => report.revised_snapshot.id), ...manualRevisionByChild.keys()]);
  const pickable = evidenceOnly ? presentedSnapshots.filter((snapshot) => evidenceIds.has(snapshot.id)) : presentedSnapshots;

  const recordVersionPicker = <section className="version-strip" aria-label="选择存档版本">
          <div className="version-controls">
            <div>
              <span className="section-label">查看版本</span>
              <p>{evidenceOnly ? "仅显示带后续事件证据的修订版本" : "默认选中最新数据截止时间的版本"}</p>
            </div>
            <label className="evidence-toggle">
              <input
                type="checkbox"
                checked={evidenceOnly}
                disabled={evidenceIds.size === 0}
                onChange={(event) => {
                  const enabled = event.target.checked;
                  setEvidenceOnly(enabled);
                  if (enabled) {
                    const eventSnapshots = presentedSnapshots.filter((snapshot) => evidenceIds.has(snapshot.id));
                    if (eventSnapshots.length) setSelectedId(latestSnapshot(eventSnapshots).id);
                  }
                }}
              />
              <span>有事件证据</span>
            </label>
          </div>
          <div className="snapshot-picker" role="listbox" aria-label="存档版本">
            {pickable.map((snapshot) => (
              <button
                key={snapshot.id}
                className={`snapshot-chip ${snapshot.id === selected.id ? "is-selected" : ""}`}
                onClick={() => setSelectedId(snapshot.id)}
                role="option"
                aria-selected={snapshot.id === selected.id}
              >
                <span>{formatDate(snapshot.feature_as_of_time)}</span>
                <small>{manualRevisionByChild.has(snapshot.id) ? "证据修订 · 待人工核验" : isOnDemandSnapshot(snapshot) ? "主动生成 · 实验模型" : evidenceIds.has(snapshot.id) ? "事件后修订" : snapshot.version > 1 ? `第 ${snapshot.version} 版` : "存档版本"}</small>
              </button>
            ))}
          </div>
        </section>;

  const onRevisionCreated = (revision: EvidenceRevision) => {
    setEvidenceRevisions((previous) => ({ kind: "ready", items: [revision, ...(previous.kind === "ready" ? previous.items : [])] }));
    setRetryKey((value) => value + 1);
  };
  const supported = isSupportedSymbol(requestedSymbol);
  return <Shell {...shellProps}>
    <main className="workspace" aria-labelledby="workspace-title">
      <WorkspacePageHeader symbol={data.symbol} section={activeSection} snapshotCount={data.snapshots.length} supported={supported} />

      {activeSection === "overview" && <OverviewWorkspace
        current={data}
        symbol={requestedSymbol}
        revisions={manualRevisions}
        filings={filings}
        onNavigate={navigate}
        onOpenStock={openStock}
        onSelectSnapshot={(id) => { setSelectedId(id); navigate("forecast"); }}
      />}

      {activeSection === "forecast" && <section className="workspace-stack">
        <V2ForecastWorkspace symbol={requestedSymbol} filings={filings.kind === "ready" ? filings.data : null} />
        <WorkspaceIntro eyebrow="旧档案" title="生成、检查与回放历史预测" description="这是旧版实验性离线模型档案。它和上方 V2 任务流分开保留，不代表联合模型结果。" action={<button className="primary-action" disabled={!supported || forecastAction.kind === "running"} onClick={startForecast}>{forecastAction.kind === "running" ? "正在生成…" : "生成旧版预测"}</button>} />
        <ActionNotice state={forecastAction} />
        {!supported && <WorkspaceRestriction />}
        {recordVersionPicker}
        <section className="analysis-grid"><ProbabilityPanel snapshot={selected} report={selectedReport} /><MetadataPanel snapshot={selected} report={selectedReport} /></section>
        <PriceComparisonPanel key={selected.id} history={data.price_history} selected={selected} report={selectedChainReport} />
        <Timeline snapshots={presentedSnapshots} reports={data.refresh_reports} manualRevisions={manualRevisions} selectedId={selected.id} onSelect={(id) => { setEvidenceOnly(false); setSelectedId(id); }} />
      </section>}

      {activeSection === "evidence" && <section className="workspace-stack">
        <WorkspaceIntro eyebrow="官方来源" title="SEC 证据中心" description="先建立官方文件目录，再读取摘要和记录人工来源核验。扫描与核验不会自动改变预测结论。" action={<button className="primary-action" disabled={!supported || scanAction.kind === "running"} onClick={startFilingScan}>{scanAction.kind === "running" ? "正在扫描…" : "扫描官方申报"}</button>} />
        <ActionNotice state={scanAction} />
        {!supported && <WorkspaceRestriction />}
        <section className="evidence-layout">
          <EvidenceWorkflowPanel mode="upload" symbol={requestedSymbol} supported={supported} snapshots={data.snapshots} filings={filings} revisions={evidenceRevisions} onRevisionCreated={onRevisionCreated} />
          <FilingInventoryPanel symbol={requestedSymbol} state={filings} onInventoryChanged={updateFilingInventory} />
        </section>
      </section>}

      {activeSection === "revisions" && <section className="workspace-stack">
        <WorkspaceIntro eyebrow="预测修订" title="把新材料关联到历史预测" description="选择已读取的 SEC 文件或已保存媒体材料，再定位到一个主动生成的预测版本。修订会保留原预测，且不会擅自改变模型概率。" />
        <RevisionAutomationNote />
        <EvidenceWorkflowPanel mode="revision" symbol={requestedSymbol} supported={supported} snapshots={data.snapshots} filings={filings} revisions={evidenceRevisions} onRevisionCreated={onRevisionCreated} />
        <section className="revision-review-grid">
          <EvidencePanel report={selectedReport} manualRevision={selectedManualRevision} selected={selected} />
          <Timeline snapshots={presentedSnapshots} reports={data.refresh_reports} manualRevisions={manualRevisions} selectedId={selected.id} onSelect={(id) => setSelectedId(id)} />
        </section>
      </section>}

      {activeSection === "evaluation" && <section className="workspace-stack"><WorkspaceIntro eyebrow="模型验证" title="固定样本上的离线评估" description="这些指标来自保存的历史评测，用于比较基线模型；不等同于实时交易表现或投资建议。" /><Evaluation evaluation={data.evaluation} /></section>}
    </main>
  </Shell>;
}

function WorkspacePageHeader({ symbol, section, snapshotCount, supported }: { symbol: string; section: WorkspaceSection; snapshotCount: number; supported: boolean }) {
  return <header className="workspace-heading">
    <div><p className="eyebrow">{symbol} / {WORKSPACE_SECTION_TITLE[section]}</p><h1 id="workspace-title">{WORKSPACE_SECTION_TITLE[section]}</h1></div>
    <div className="workspace-context"><span className={`live-dot ${supported ? "" : "muted"}`} aria-hidden="true" /><span>{supported ? "支持主动研究" : "仅可查看存档"}</span><span className="workspace-record-count">{snapshotCount} 个保存版本</span></div>
  </header>;
}

function WorkspaceIntro({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <section className="workspace-intro">
    <div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p>{description}</p></div>
    {action && <div className="workspace-intro-action">{action}</div>}
  </section>;
}

function WorkspaceRestriction() {
  return <p className="workspace-restriction">当前主动操作仅支持 {SUPPORTED_SYMBOLS.join(" · ")}。其他股票可以查看已有档案，但不能扫描 SEC 或生成新的预测。</p>;
}

function RevisionAutomationNote() {
  return <aside className="revision-automation-note" aria-labelledby="revision-trigger-title"><span className="section-label">修订触发规则</span><div><h2 id="revision-trigger-title">何时建立待核验修订？</h2><p><strong>手动：</strong>选择一份主动生成的历史预测，并绑定预测后公开的已读取 SEC 文件或已保存媒体材料。</p><p><strong>自动：</strong>本机定时任务每小时检查 SEC；发现预测后发布的新官方文件，且距上次预测不超过 72 小时，才会建立待人工核验修订。媒体材料不会自动触发。</p><p className="fine-print">所有修订保留原模型概率，方向结论待人工审核。自动检查需要本机已配置并持续运行定时任务。</p></div></aside>;
}

function EmptyWorkspace({ data, section, supported, forecastAction, onForecast, scanAction, onScan, filings, revisions, onNavigate, onOpenStock, onFilingChanged }: {
  data: DashboardResponse;
  section: WorkspaceSection;
  supported: boolean;
  forecastAction: ActionState;
  onForecast: () => void;
  scanAction: ActionState;
  onScan: () => void;
  filings: FilingState;
  revisions: EvidenceRevisionState;
  onNavigate: (section: WorkspaceSection) => void;
  onOpenStock: (ticker: SupportedSymbol) => void;
  onFilingChanged: (filing: OfficialFiling) => void;
}) {
  if (section === "overview") return <OverviewWorkspace current={data} symbol={data.symbol} revisions={revisions.kind === "ready" ? revisions.items : []} filings={filings} onNavigate={onNavigate} onOpenStock={onOpenStock} onSelectSnapshot={() => undefined} />;
  if (section === "forecast") return <section className="workspace-stack"><V2ForecastWorkspace symbol={data.symbol} filings={filings.kind === "ready" ? filings.data : null} /><WorkspaceIntro eyebrow="旧档案" title="尚无可回放的旧版预测" description="旧版实验性离线模型档案为空。上方的 V2 任务不会用它的概率作为联合结果。" action={<button className="primary-action" disabled={!supported || forecastAction.kind === "running"} onClick={onForecast}>{forecastAction.kind === "running" ? "正在生成…" : "生成旧版预测"}</button>} /><ActionNotice state={forecastAction} /><EmptyState data={data} /></section>;
  if (section === "evidence") return <section className="workspace-stack"><WorkspaceIntro eyebrow="官方来源" title="SEC 证据中心" description="即使还没有预测版本，也可以先保存媒体材料并建立官方资料目录，为后续研究准备可核验来源。" action={<button className="primary-action" disabled={!supported || scanAction.kind === "running"} onClick={onScan}>{scanAction.kind === "running" ? "正在扫描…" : "扫描官方申报"}</button>} /><ActionNotice state={scanAction} /><section className="evidence-layout"><EvidenceWorkflowPanel mode="upload" symbol={data.symbol} supported={supported} snapshots={[]} filings={filings} revisions={revisions} onRevisionCreated={() => undefined} /><FilingInventoryPanel symbol={data.symbol} state={filings} onInventoryChanged={onFilingChanged} /></section></section>;
  if (section === "evaluation") return <section className="workspace-stack"><WorkspaceIntro eyebrow="模型验证" title="尚无可展示的离线评估" description="此股票目前没有固定的历史评测摘要。评估区不会把缺失预测替换成行情或其他模块内容。" /><EvaluationEmptyState /></section>;
  return <section className="workspace-stack"><WorkspaceIntro eyebrow="预测修订" title="尚无可修订预测" description="手动修订需要先存在一份主动生成的预测版本。已保存材料会在预测可用后保留为候选来源。" /><RevisionAutomationNote /></section>;
}

type UniverseItem = { dashboard?: DashboardResponse; filingCount?: number; pendingCount?: number; error?: string; loading?: boolean };

function OverviewWorkspace({ current, symbol, revisions, filings, onNavigate, onOpenStock, onSelectSnapshot }: { current: DashboardResponse; symbol: string; revisions: EvidenceRevision[]; filings: FilingState; onNavigate: (section: WorkspaceSection) => void; onOpenStock: (ticker: SupportedSymbol) => void; onSelectSnapshot: (id: string) => void }) {
  const [universe, setUniverse] = useState<Record<string, UniverseItem>>({});
  useEffect(() => {
    const controller = new AbortController();
    const others = SUPPORTED_SYMBOLS.filter((ticker) => ticker !== symbol);
    setUniverse(Object.fromEntries(others.map((ticker) => [ticker, { loading: true }])));
    Promise.all(others.map(async (ticker) => {
      try {
        const [dashboard, inventory] = await Promise.all([getDashboard(ticker, controller.signal), getFilingInventory(ticker, controller.signal)]);
        return [ticker, { dashboard, filingCount: inventory.filings.length, pendingCount: inventory.filings.filter((filing) => filingReviewBucket(filing) === "pending").length }] as const;
      } catch (error: unknown) {
        return [ticker, { error: actionMessage(error, "暂时无法读取该股票的本地研究状态。") }] as const;
      }
    })).then((entries) => { if (!controller.signal.aborted) setUniverse(Object.fromEntries(entries)); });
    return () => controller.abort();
  }, [symbol]);

  const latest = current.snapshots.length ? latestSnapshot(current.snapshots) : undefined;
  const currentInventory = filings.kind === "ready" ? filings.data.filings : undefined;
  const currentPendingFilings = currentInventory?.filter((filing) => filingReviewBucket(filing) === "pending").length;
  const allItems: Array<[string, UniverseItem]> = SUPPORTED_SYMBOLS.map((ticker) => {
    if (ticker === symbol) return [ticker, {
      dashboard: current,
      filingCount: currentInventory?.length,
      pendingCount: currentInventory?.filter((filing) => filingReviewBucket(filing) === "pending").length,
    }];
    return [ticker, universe[ticker] ?? { loading: true }];
  });
  const pendingRevisions = revisions.filter((revision) => revision.review_status === "pending_review").length;

  return <section className="workspace-stack overview-workspace">
    <WorkspaceIntro eyebrow="企业研究总览" title="从任务队列进入单股分析" description="这里仅汇总本地 API 已返回的研究状态。不会用空白数据补成 0，也不会把资料目录当作已验证结论。" />
    <section className="overview-focus" aria-label={`${symbol} 当前研究状态`}>
      <div><p className="eyebrow">当前上下文 / {symbol}</p><h2>{latest ? `最新版本 · ${formatDate(latest.feature_as_of_time)}` : "还没有预测版本"}</h2><p>{latest ? `数据截止 ${formatDateTime(latest.feature_as_of_time)}；可继续查看概率、价格窗口和版本链。` : "可以先扫描 SEC 资料，或在本地数据准备好后创建第一份预测。"}</p></div>
      <div className="overview-focus-actions">
        {latest && <button className="primary-action" onClick={() => onSelectSnapshot(latest.id)}>查看最新预测</button>}
        <button className="secondary-action" onClick={() => onNavigate("evidence")}>打开证据中心</button>
      </div>
    </section>
    <section className="overview-metrics" aria-label="待处理事项">
      <article><span>当前股票存档</span><strong>{current.snapshots.length}</strong><small>保存的预测版本</small></article>
      <article><span>SEC 待来源核验</span><strong>{currentPendingFilings === undefined ? "—" : currentPendingFilings}</strong><small>{filings.kind === "loading" ? "正在读取 SEC 目录" : filings.kind === "error" ? "SEC 目录读取失败" : "当前股票的官方资料"}</small></article>
      <article><span>待核验证据修订</span><strong>{pendingRevisions}</strong><small>当前股票的修订关联</small></article>
      <article><span>行情数据截止</span><strong>{current.price_history?.latest_trading_date ?? "—"}</strong><small>{current.price_history?.candles.length ? `${current.price_history.candles.length} 根本地日线` : "本地 API 未返回行情"}</small></article>
    </section>
    <section className="universe-section" aria-labelledby="universe-title"><div className="section-heading"><div><p className="eyebrow">已支持股票</p><h2 id="universe-title">研究覆盖面</h2></div><span>本地实时读取</span></div><div className="universe-grid">
      {allItems.map(([ticker, item]) => <article className={`universe-card ${ticker === symbol ? "is-current" : ""}`} key={ticker}>
        <div className="universe-card-top"><strong>{ticker}</strong>{ticker === symbol && <span>当前</span>}</div>
        {item.loading && <p className="universe-pending">正在读取本地状态…</p>}
        {item.error && <p className="universe-error" role="status">{item.error}</p>}
        {item.dashboard && <>
          <dl><div><dt>预测版本</dt><dd>{item.dashboard.snapshots.length}</dd></div><div><dt>SEC 目录</dt><dd>{item.filingCount === undefined ? "—" : item.filingCount}</dd></div><div><dt>待来源核验</dt><dd>{item.pendingCount === undefined ? "—" : item.pendingCount}</dd></div></dl>
          <small>{item.dashboard.snapshots.length ? `最新数据截止 ${formatDate(latestSnapshot(item.dashboard.snapshots).feature_as_of_time)}` : "暂无预测版本；不代表没有 SEC 资料"}</small>
        </>}
        <button className="universe-open" type="button" onClick={() => onOpenStock(ticker as SupportedSymbol)}>打开 {ticker} 总览 <span aria-hidden="true">→</span></button>
      </article>)}
    </div></section>
  </section>;
}

function ActionNotice({ state }: { state: ActionState }) {
  if (state.kind === "idle" || state.kind === "running") return null;
  return <p className={`action-notice ${state.kind}`} role={state.kind === "error" ? "alert" : "status"}>{state.message}</p>;
}

function EvidenceWorkflowPanel({ mode, symbol, supported, snapshots, filings, revisions, onRevisionCreated }: { mode: "upload" | "revision"; symbol: string; supported: boolean; snapshots: Snapshot[]; filings: FilingState; revisions: EvidenceRevisionState; onRevisionCreated: (revision: EvidenceRevision) => void }) {
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

  async function handleRevision(parentSnapshotId: string, sourceType: "official_filing" | "uploaded_media", sourceId: string) {
    const revision = await createEvidenceRevision(symbol, { parent_snapshot_id: parentSnapshotId, source_type: sourceType, source_id: sourceId, mode: "manual" });
    onRevisionCreated(revision);
    return revision;
  }

  const uploadedItems = uploaded.kind === "ready" ? uploaded.items : [];
  const existingRevisions = revisions.kind === "ready" ? revisions.items : [];
  const revisionReadyFilings = filings.kind === "ready"
    ? filings.data.filings.filter((filing) => filing.content_status === "fetched" && filing.review_status !== "rejected" && Boolean(filing.id)).sort(sortFilingsNewestFirst)
    : [];

  const isUpload = mode === "upload";
  return <section className={`evidence-workbench ${isUpload ? "evidence-upload-workbench" : "revision-workbench"}`} aria-labelledby="evidence-workbench-title">
    <div className="workbench-heading">
      <div><span className="section-label">{isUpload ? "媒体材料" : "手动修订"}</span><h3 id="evidence-workbench-title">{isUpload ? "保存未获官方证实的来源" : "让新信息关联到具体预测"}</h3></div>
      <span className="tag">{isUpload ? "evidence intake" : "human initiated"}</span>
    </div>
    <p className="workbench-intro">{isUpload ? "上传媒体消息、行业资料或未获官网确认的信息。星级和影响程度记录你的初步判断，不是官方验证。" : "选择一份具体历史预测与一条新来源，建立待人工核验的修订关联。系统保留原预测；模型概率不会因为这一步被人为改写。"}</p>
    <div className="workbench-grid">
      {isUpload
        ? <UploadEvidencePanel symbol={symbol} supported={supported} uploaded={uploaded} onUpload={handleUpload} />
        : <ManualRevisionPanel
        supported={supported}
        snapshots={snapshots}
        revisionReadyFilings={revisionReadyFilings}
        uploaded={uploadedItems}
        priorRevisions={existingRevisions}
        loadingSources={filings.kind === "loading" || uploaded.kind === "loading"}
        sourceError={filings.kind === "error" ? filings.message : uploaded.kind === "error" ? uploaded.message : revisions.kind === "error" ? revisions.message : undefined}
        onCreate={handleRevision}
      />}
    </div>
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
      setAction({ kind: "success", message: "材料已保存为未获官方证实的媒体证据；现在可用于手动修订。" });
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

function ManualRevisionPanel({ supported, snapshots, revisionReadyFilings, uploaded, priorRevisions, loadingSources, sourceError, onCreate }: {
  supported: boolean;
  snapshots: Snapshot[];
  revisionReadyFilings: OfficialFiling[];
  uploaded: UploadedEvidence[];
  priorRevisions: EvidenceRevision[];
  loadingSources: boolean;
  sourceError?: string;
  onCreate: (parentSnapshotId: string, sourceType: "official_filing" | "uploaded_media", sourceId: string) => Promise<EvidenceRevision>;
}) {
  const manualRevisionIds = new Set(priorRevisions.map((revision) => revision.revised_snapshot_id));
  const sortedSnapshots = snapshots
    .filter((snapshot) => isOnDemandSnapshot(snapshot) && !manualRevisionIds.has(snapshot.id))
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.version - a.version);
  const [parentId, setParentId] = useState("");
  const [sourceValue, setSourceValue] = useState("");
  const [action, setAction] = useState<RevisionActionState>({ kind: "idle" });

  const selectedParent = sortedSnapshots.find((snapshot) => snapshot.id === parentId);
  const sourceOptions = [
    ...revisionReadyFilings.map((filing) => ({ value: `official_filing:${filing.id}`, sourceType: "official_filing" as const, sourceId: filing.id!, availableAt: filingAvailableAt(filing), label: `SEC ${filing.form} · ${filing.filed_at} · ${filing.review_status === "accepted" ? "已接受" : "待核验"}` })),
    ...sortUploadedEvidence(uploaded).map((item) => ({ value: `uploaded_media:${item.id}`, sourceType: "uploaded_media" as const, sourceId: item.id, availableAt: item.published_at, label: `媒体材料 · ${item.title} · ${clampStars(item.credibility_stars)} 星` })),
  ].filter((source) => selectedParent ? Date.parse(source.availableAt) > Date.parse(selectedParent.created_at) : true);
  const snapshotChoiceKey = sortedSnapshots.map((snapshot) => snapshot.id).join("|");
  const sourceChoiceKey = sourceOptions.map((source) => source.value).join("|");

  useEffect(() => {
    setParentId((current) => sortedSnapshots.some((snapshot) => snapshot.id === current) ? current : (sortedSnapshots[0]?.id ?? ""));
    setAction({ kind: "idle" });
  }, [snapshotChoiceKey]);

  useEffect(() => {
    setSourceValue((current) => sourceOptions.some((source) => source.value === current) ? current : (sourceOptions[0]?.value ?? ""));
  }, [sourceChoiceKey]);

  const selectedSource = sourceOptions.find((option) => option.value === sourceValue);
  async function submitRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!parentId || !selectedSource) return;
    setAction({ kind: "running" });
    try {
      const revision = await onCreate(parentId, selectedSource.sourceType, selectedSource.sourceId);
      setAction({ kind: "success", revision, message: "已建立待人工核验的修订记录；原预测保持不变，模型概率暂未改变。" });
    } catch (error: unknown) {
      setAction({ kind: "error", message: actionMessage(error, "生成修订失败。") });
    }
  }

  return <article className="workbench-card revision-card">
    <span className="action-number">04</span>
    <h3>修正预测</h3>
    <p>选择要修订的具体历史版本，再绑定一条新 SEC 文件或媒体材料。会建立不可变的新关联，原版本始终保留。</p>
    <form className="evidence-form" onSubmit={submitRevision}>
      <label>要修订的历史预测
        <select value={parentId} disabled={!supported || action.kind === "running" || !sortedSnapshots.length} onChange={(event) => { setParentId(event.target.value); setAction({ kind: "idle" }); }}>
          {!sortedSnapshots.length && <option value="">暂无可修订的主动生成版本</option>}
          {sortedSnapshots.map((snapshot) => <option value={snapshot.id} key={snapshot.id}>生成 {formatDateTime(snapshot.created_at)} · 数据截止 {formatDateTime(snapshot.feature_as_of_time)} · 第 {snapshot.version} 版 · {snapshot.id.slice(0, 8)}</option>)}
        </select>
      </label>
      <label>新材料
        <select value={sourceValue} disabled={!supported || action.kind === "running" || !sourceOptions.length || loadingSources} onChange={(event) => setSourceValue(event.target.value)}>
          {!sourceOptions.length && <option value="">{loadingSources ? "正在读取材料…" : selectedParent ? "没有发布时间晚于该预测的新材料" : "请先选择预测版本"}</option>}
          {sourceOptions.map((source) => <option key={source.value} value={source.value}>{source.label}</option>)}
        </select>
      </label>
      <p className="revision-note">只可修订“主动生成”的 observed 预测版本；旧 Week 8 历史演示仅供回放。SEC 仅列出已读取原文且未驳回、并在该预测之后公开的来源。媒体材料保留“未获官方证实”和你的星级判断，必须在人工核验中审阅。</p>
      <button className="action-button" type="submit" disabled={!supported || action.kind === "running" || !parentId || !selectedSource}>{action.kind === "running" ? "正在生成待核验修订…" : "生成待核验修订"}</button>
      <ActionNotice state={action} />
      {action.kind === "success" && action.revision && <RevisionLink revision={action.revision} />}
      {sourceError && <p className="content-error" role="alert">{sourceError}</p>}
    </form>
    <RevisionHistory revisions={priorRevisions} />
  </article>;
}

function RevisionLink({ revision }: { revision: EvidenceRevision }) {
  return <p className="revision-result" role="status"><strong>版本关联已保存</strong><span>{revision.parent_snapshot_id.slice(0, 8)} → {revision.revised_snapshot_id.slice(0, 8)}</span><small>待人工核验 · 模型概率暂未改变</small></p>;
}

function RevisionHistory({ revisions }: { revisions: EvidenceRevision[] }) {
  if (!revisions.length) return <p className="material-status">还没有由新材料发起的手动修订。</p>;
  return <div className="revision-history" aria-label="手动修订记录">
    <p className="list-caption">最近手动修订</p>
    {revisions.slice(0, 3).map((revision) => <div className="revision-history-item" key={revision.id}>
      <strong>{revision.source_type === "official_filing" ? "SEC 文件" : "媒体材料"}</strong>
      <span>{revision.parent_snapshot_id.slice(0, 8)} → {revision.revised_snapshot_id.slice(0, 8)}</span>
      <small>待人工核验 · 模型概率暂未改变</small>
    </div>)}
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
    <p className="fine-print inventory-note">这些是原始 SEC 文件索引。人工结论仅核验来源相关性；文件不会自动用于本次预测。</p>
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
    { id: "revisions", label: "预测修订", hint: "材料关联", icon: "↗" },
    { id: "evaluation", label: "评估", hint: "离线表现", icon: "≋" },
  ];
  return <div className="app-frame">
    <aside className="app-rail" aria-label="主要导航">
      <a className="workspace-brand" href="#overview" onClick={() => navigate("overview")} aria-label="Market Evidence Agent 总览"><span className="brand-mark">ME</span><span>market<br /><em>evidence</em></span></a>
      <div className="rail-context"><span>当前股票</span><strong>{symbol}</strong><small>本地研究档案</small></div>
      <nav className="workspace-nav">
        {nav.map((item) => <button key={item.id} type="button" className={activeSection === item.id ? "active" : ""} aria-current={activeSection === item.id ? "page" : undefined} onClick={() => navigate(item.id)}><span aria-hidden="true">{item.icon}</span><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>)}
      </nav>
      <div className="rail-bottom"><span className="rail-review-dot" aria-hidden="true" />所有结论需人工审阅</div>
    </aside>
    <div className="app-content">
      <header className="topbar">
        <div className="topbar-path"><span>Research workspace</span><b>/</b><strong>{symbol}</strong></div>
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
  return <main className="state-card" aria-live="polite" aria-busy="true"><span className="loading-mark" aria-hidden="true" /><p className="kicker">RETRIEVING ARCHIVE</p><h1>正在读取 {symbol} 的已保存记录</h1><p>只请求本地已存档的版本与评测，不会调用模型或产生费用。</p></main>;
}

function EmptyState({ data }: { data: DashboardResponse }) {
  const hasPrices = Boolean(data.price_history?.candles.length);
  return <main className="state-card"><p className="kicker">NO ARCHIVED FORECAST</p><h1>{data.symbol} 暂无预测存档。</h1><p>{hasPrices ? "没有保存的离线预测、证据或版本链；下面仅展示本地已存的历史行情。" : "输入的代码有效，但目前没有保存的快照或行情；可尝试 AAPL 查看现有示例。"}</p>{hasPrices && <PriceOnlyPanel history={data.price_history!} symbol={data.symbol} />}</main>;
}

function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return <main className="state-card error-state" role="alert"><p className="kicker">ARCHIVE UNAVAILABLE</p><h1>暂时无法读取这份档案。</h1><p>{message}</p><button onClick={retry}>重新尝试</button></main>;
}

function ProbabilityPanel({ snapshot, report }: { snapshot: Snapshot; report?: RefreshReport }) {
  const probabilities = [
    ["Bearish", "看跌", snapshot.bearish_probability, "bearish"],
    ["Neutral", "中性", snapshot.neutral_probability, "neutral"],
    ["Bullish", "看涨", snapshot.bullish_probability, "bullish"],
  ] as const;
  return <article className="panel probability-panel">
    <div className="panel-title"><span className="section-label">三分类概率</span><span className="tag">offline snapshot</span></div>
    <div className="probability-chart">
      {probabilities.map(([english, chinese, value, tone]) => <div className="probability-row" key={tone}>
        <div className="probability-name"><span>{english}</span><small>{chinese}</small></div>
        <div className="meter" aria-label={`${chinese} ${formatPercent(value)}`}><i className={tone} style={{ width: `${Math.max(1, value * 100)}%` }} /></div>
        <strong>{formatPercent(value)}</strong>
      </div>)}
    </div>
    <p className="panel-note">模型输出反映该版本保存时的特征；不是投资建议，也不是实时信号。</p>
    {report && <div className="delta-line"><span>相对事件前版本（百分点）</span>{(["bearish", "neutral", "bullish"] as const).map((key) => <b key={key}>{probabilityLabel(key)} {signedPoints(report.probability_delta[key])}</b>)}</div>}
  </article>;
}

function MetadataPanel({ snapshot, report }: { snapshot: Snapshot; report?: RefreshReport }) {
  const target = targetWindowForSnapshot(report, snapshot);
  const onDemand = isOnDemandSnapshot(snapshot);
  return <article className="panel metadata-panel">
    <div className="panel-title"><span className="section-label">数据边界</span>{onDemand ? <span className="tag generated-tag">主动生成 · 实验模型</span> : <span className="folio">0{snapshot.version}</span>}</div>
    <dl>
      <div><dt>数据截止</dt><dd>{formatDateTime(snapshot.feature_as_of_time)}</dd></div>
      <div><dt>交易日</dt><dd>{snapshot.feature_trading_date}</dd></div>
      <div><dt>特征快照</dt><dd>{snapshot.feature_snapshot_mode}</dd></div>
      <div><dt>模型记录</dt><dd>{snapshot.model_version}</dd></div>
      {target && <div><dt>滚动目标窗口</dt><dd>{target.start} → {target.end}</dd></div>}
    </dl>
    {report && <p className="fine-print">此修订使用更晚的 20 个 XNYS 交易日目标窗口；概率差并不代表事件造成了变化。</p>}
  </article>;
}

function PriceComparisonPanel({ history, selected, report }: { history?: PriceHistory | null; selected: Snapshot; report?: RefreshReport }) {
  const candles = (history?.candles ?? []).filter(isUsableCandle).sort((a, b) => a.trading_date.localeCompare(b.trading_date));
  const target = targetWindowForSnapshot(report, selected);
  const shown = chartCandles(candles, selected, report, target);
  const [activeDate, setActiveDate] = useState<string | null>(shown.at(-1)?.trading_date ?? null);

  if (!history || candles.length === 0 || shown.length === 0) {
    return <section className="price-panel price-panel-empty" aria-labelledby="price-title">
      <div><span className="section-label">价格对照</span><h2 id="price-title">没有可展示的历史蜡烛图。</h2></div>
      <p>这个存档版本没有对应的已保存行情。价格面板只读取本地数据库中的历史 OHLCV 数据。</p>
    </section>;
  }

  const active = shown.find((candle) => candle.trading_date === activeDate) ?? shown.at(-1)!;
  const coverage = target ? targetCoverage(candles, selected.feature_trading_date, target.start, target.end) : null;
  const comparison = target && coverage?.complete
    ? comparisonReturn(candles, selected.feature_trading_date, target.end)
    : comparisonReturn(candles, selected.feature_trading_date, history.latest_trading_date ?? candles.at(-1)!.trading_date);
  const comparisonEnd = target && coverage?.complete ? target.end : history.latest_trading_date ?? candles.at(-1)!.trading_date;
  const awaitingEvaluation = Boolean(target && !coverage?.complete);
  const showReturn = Boolean(comparison && !awaitingEvaluation && comparisonEnd > selected.feature_trading_date);
  const targetStatus = !target
    ? "当前版本没有保存的滚动目标窗口。"
    : coverage?.complete
      ? `20 个交易日目标窗口已完整覆盖至 ${target.end}。`
      : `20 个交易日目标窗口尚未到期或本地行情不足（目标结束：${target.end}）。`;

  return <section className="price-panel" aria-labelledby="price-title">
    <div className="price-panel-heading">
      <div><span className="section-label">价格对照</span><h2 id="price-title">股价与存档版本的时间边界</h2></div>
      <span className="tag">{history.source}</span>
    </div>
    <p className="price-intro">蜡烛图来自已存日线。竖线标出版本数据截止日；阴影表示该版本保存的 20 个交易日目标窗口，可来自主动预测或后续修订。</p>
    <CandlestickChart
      candles={shown}
      selected={selected}
      report={report}
      target={target}
      activeDate={active.trading_date}
      onInspect={setActiveDate}
    />
    <div className="candle-detail" aria-live="polite">
      <strong>{active.trading_date}</strong><span>开 {formatPrice(active.open)} · 高 {formatPrice(active.high)} · 低 {formatPrice(active.low)} · 收 {formatPrice(active.close)}</span>
      {active.benchmark_close !== null && active.benchmark_close !== undefined && <span>SPY 收 {formatPrice(active.benchmark_close)}</span>}
    </div>
    <div className="price-summary">
      <div><span>行情截至</span><strong>{history.latest_trading_date ?? candles.at(-1)!.trading_date}</strong></div>
      <div><span>{showReturn && coverage?.complete ? "版本截止 → 目标结束" : awaitingEvaluation ? "20 日目标窗口" : "版本后已存行情"}</span><strong>{awaitingEvaluation ? "待评测" : showReturn && comparison ? formatReturn(comparison.stock) : "—"}</strong><small>{awaitingEvaluation ? "尚无完整目标结果" : showReturn ? "股票" : "尚无版本后行情"}</small></div>
      <div><span>同期 SPY</span><strong>{awaitingEvaluation ? "待评测" : !showReturn || comparison?.benchmark === null || !comparison ? "—" : formatReturn(comparison.benchmark)}</strong><small>{awaitingEvaluation ? "尚无完整目标结果" : comparison?.benchmark === null ? "无完整对齐基准" : showReturn ? "基准" : "尚无版本后行情"}</small></div>
    </div>
    <p className="fine-print price-fine-print">{targetStatus} 以上对比只描述已存价格变动，不表示预测分类、准确率或事件因果。</p>
  </section>;
}

function PriceOnlyPanel({ history, symbol }: { history: PriceHistory; symbol: string }) {
  const candles = history.candles.filter(isUsableCandle).sort((a, b) => a.trading_date.localeCompare(b.trading_date)).slice(-90);
  const [activeDate, setActiveDate] = useState<string | null>(candles.at(-1)?.trading_date ?? null);
  if (!candles.length) return null;
  const active = candles.find((candle) => candle.trading_date === activeDate) ?? candles.at(-1)!;
  return <section className="price-panel price-only-panel" aria-labelledby="price-title">
    <div className="price-panel-heading"><div><span className="section-label">已存行情</span><h2 id="price-title">{symbol} 的历史蜡烛图</h2></div><span className="tag">{history.source}</span></div>
    <p className="price-intro">当前没有预测存档，因此没有版本截止线、目标窗口或预测对比。可悬停或聚焦蜡烛查看当天 OHLC。</p>
    <CandlestickChart candles={candles} activeDate={active.trading_date} onInspect={setActiveDate} />
    <div className="candle-detail" aria-live="polite"><strong>{active.trading_date}</strong><span>开 {formatPrice(active.open)} · 高 {formatPrice(active.high)} · 低 {formatPrice(active.low)} · 收 {formatPrice(active.close)}</span>{active.benchmark_close !== null && active.benchmark_close !== undefined && <span>SPY 收 {formatPrice(active.benchmark_close)}</span>}</div>
    <div className="price-summary price-only-summary"><div><span>行情截至</span><strong>{history.latest_trading_date ?? candles.at(-1)!.trading_date}</strong></div><div><span>最新收盘</span><strong>{formatPrice(candles.at(-1)!.close)}</strong><small>{symbol}</small></div><div><span>同期 SPY 收盘</span><strong>{candles.at(-1)!.benchmark_close === null || candles.at(-1)!.benchmark_close === undefined ? "—" : formatPrice(candles.at(-1)!.benchmark_close!)}</strong><small>基准</small></div></div>
  </section>;
}

function CandlestickChart({ candles, selected, report, target, activeDate, onInspect }: { candles: PriceCandle[]; selected?: Snapshot; report?: RefreshReport; target?: TargetWindow; activeDate: string; onInspect: (date: string) => void }) {
  const width = 1000;
  const height = 320;
  const margin = { top: 22, right: 58, bottom: 34, left: 8 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const low = Math.min(...candles.map((candle) => candle.low));
  const high = Math.max(...candles.map((candle) => candle.high));
  const padding = Math.max((high - low) * 0.08, 0.5);
  const min = low - padding;
  const max = high + padding;
  const originalDate = report?.original_snapshot.feature_trading_date;
  const revisedDate = report?.revised_snapshot.feature_trading_date;
  const markerIndex = (date: string | undefined) => date ? candles.findIndex((candle) => candle.trading_date === date) : -1;
  const targetStart = markerIndex(target?.start);
  const targetEnd = markerIndex(target?.end);
  const futureTargetSlots = target && targetStart < 0 && target.start > candles.at(-1)!.trading_date ? 20 : 0;
  const slotCount = candles.length + futureTargetSlots;
  const x = (index: number) => margin.left + ((index + 0.5) / slotCount) * plotWidth;
  const y = (value: number) => margin.top + ((max - value) / (max - min)) * plotHeight;
  const bodyWidth = Math.max(2, Math.min(10, (plotWidth / slotCount) * 0.58));
  const originalIndex = markerIndex(originalDate);
  const revisedIndex = report ? markerIndex(revisedDate) : -1;
  const cutoffIndex = report ? -1 : markerIndex(selected?.feature_trading_date);
  const actualStart = targetEnd >= 0 ? targetEnd + 1 : -1;
  const ticks = [max, (max + min) / 2, min];
  const dateTicks = [0, Math.floor((candles.length - 1) / 2), candles.length - 1];

  return <figure className="candlestick-figure">
    <svg className="candlestick-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="candle-chart-title candle-chart-description">
      <title id="candle-chart-title">{selected?.symbol ?? "股票"} 历史日线蜡烛图</title>
      <desc id="candle-chart-description">使用鼠标停留或键盘聚焦蜡烛图中的日线，读取当天开盘、最高、最低和收盘价格。{target ? "阴影区域是保存的 20 个交易日滚动目标窗口。" : "当前版本没有保存的滚动目标窗口。"}</desc>
      {ticks.map((tick) => <g key={tick}><line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} className="chart-grid" /><text x={width - margin.right + 9} y={y(tick) + 4} className="chart-price-label">{formatPrice(tick)}</text></g>)}
      {targetStart >= 0 && targetEnd >= targetStart && <g className="target-window"><rect x={x(targetStart) - bodyWidth} y={margin.top} width={x(targetEnd) - x(targetStart) + bodyWidth * 2} height={plotHeight} /><text x={x(targetStart) + 5} y={margin.top + 14}>20-session target</text></g>}
      {futureTargetSlots > 0 && <g className="target-window target-window-future"><rect x={x(candles.length - 1) + bodyWidth} y={margin.top} width={width - margin.right - (x(candles.length - 1) + bodyWidth)} height={plotHeight} /><text x={x(candles.length - 1) + bodyWidth + 5} y={margin.top + 14}>20-session target / awaiting results</text></g>}
      {actualStart >= 0 && actualStart < candles.length && <g className="actual-region"><line x1={x(actualStart) - bodyWidth} x2={x(actualStart) - bodyWidth} y1={margin.top} y2={margin.top + plotHeight} /><text x={x(actualStart) + 5} y={height - margin.bottom - 8}>目标后实际行情</text></g>}
      {candles.map((candle, index) => {
        const up = candle.close >= candle.open;
        const bodyY = y(Math.max(candle.open, candle.close));
        const bodyHeight = Math.max(1.5, Math.abs(y(candle.open) - y(candle.close)));
        const afterTarget = actualStart >= 0 && index >= actualStart;
        return <g
          key={candle.trading_date}
          className={`candle ${up ? "is-up" : "is-down"} ${afterTarget ? "is-actual" : ""} ${activeDate === candle.trading_date ? "is-active" : ""}`}
          tabIndex={0}
          role="img"
          aria-label={candleLabel(candle)}
          onFocus={() => onInspect(candle.trading_date)}
          onMouseEnter={() => onInspect(candle.trading_date)}
        >
          <title>{candleLabel(candle)}</title><line x1={x(index)} x2={x(index)} y1={y(candle.high)} y2={y(candle.low)} /><rect x={x(index) - bodyWidth / 2} y={bodyY} width={bodyWidth} height={bodyHeight} />
        </g>;
      })}
      {originalIndex >= 0 && <Marker x={x(originalIndex)} label="原始截止" />}
      {revisedIndex >= 0 && revisedIndex !== originalIndex && <Marker x={x(revisedIndex)} label="修订截止" tone="revised" />}
      {cutoffIndex >= 0 && <Marker x={x(cutoffIndex)} label="版本截止" />}
      {dateTicks.map((index) => <text key={index} x={x(index)} y={height - 10} textAnchor="middle" className="chart-date-label">{shortDate(candles[index]!.trading_date)}</text>)}
    </svg>
    <figcaption><span><i className="legend-up" />收高于开</span><span><i className="legend-down" />收低于开</span>{target && <span><i className="legend-window" />保存的目标窗口</span>}</figcaption>
  </figure>;
}

function Marker({ x, label, tone }: { x: number; label: string; tone?: "revised" }) { return <g className={`snapshot-marker ${tone ?? ""}`}><line x1={x} x2={x} y1={19} y2={287} /><text x={x + 5} y={18}>{label}</text></g>; }

function EvidencePanel({ report, manualRevision, selected }: { report?: RefreshReport; manualRevision?: EvidenceRevision; selected: Snapshot }) {
  if (manualRevision) {
    const source = manualRevision.source;
    const evidence = manualRevision.evidence;
    return <article className="panel evidence-panel">
      <div className="panel-title"><span className="section-label">主要证据 / {manualRevision.mode === "automatic" ? "自动修订" : "手动修订"}</span><span className="review-flag">待人工核验</span></div>
      <h2>{evidence?.summary ?? source?.title ?? "已保存新的证据修订"}</h2>
      {evidence?.quote && <blockquote>“{evidence.quote}”</blockquote>}
      {source?.url && <SafeLink href={source.url}>打开原始来源 <span>↗</span></SafeLink>}
      <p className="trigger-line">{manualRevision.source_type === "official_filing" ? "官方 SEC 文件" : "用户上传的媒体材料 · 未获官方证实"}{source?.credibility_stars ? ` · 用户评估 ${source.credibility_stars} / 5 星` : ""}</p>
      <p className="fine-print">该证据修订与原预测使用相同的模型概率。方向结论来自材料分析，尚未完成人工核验，也不代表因果价格估计。</p>
    </article>;
  }
  if (!report) return <article className="panel evidence-panel no-evidence">
    <span className="section-label">主要证据</span>
    <h2>此版本没有绑定的公告事件证据。</h2>
    <p>选择标记为“事件后修订”的版本，才会显示该修订当时可用的来源与研究记录。</p>
    {selected.parent_snapshot_id && <p className="fine-print">修订原因：{selected.revision_reason ?? "未提供"}</p>}
  </article>;

  const reportData = report.research_run.report;
  const supporting = reportData?.supporting_evidence ?? [];
  const counter = reportData?.counter_evidence ?? [];
  return <article className="panel evidence-panel">
    <div className="panel-title"><span className="section-label">主要证据 / 后续触发</span><span className="review-flag">待人工复核</span></div>
    <h2>{report.trigger.summary}</h2>
    <blockquote>“{report.trigger.evidence_quote}”</blockquote>
    <SafeLink href={report.trigger.source_url}>打开原始来源 <span>↗</span></SafeLink>
    <p className="trigger-line">{report.trigger.reason} · {report.trigger.event_type} · {report.trigger.event_date}</p>
    <div className="claim-columns">
      <Claims title="支持性观点" claims={supporting} tone="support" />
      <Claims title="限制性观点" claims={counter} tone="counter" />
    </div>
    <p className="fine-print">每条观点是未经确认的模型推断；引用文字已做来源校验，但观点本身仍须人工审核。</p>
  </article>;
}

function Claims({ title, claims, tone }: { title: string; claims: Claim[]; tone: "support" | "counter" }) {
  return <div className={`claims ${tone}`}><h3>{title}<span>AI 观点 · 未经确认</span></h3>{claims.length ? claims.map((claim, index) => <div className="claim" key={`${claim.source_id}-${index}`}><p>{claim.claim}</p><small>“{claim.evidence_quote}”</small></div>) : <p className="empty-claim">没有保存可展示的观点。</p>}</div>;
}

function Timeline({ snapshots, reports, manualRevisions, selectedId, onSelect }: { snapshots: Snapshot[]; reports: RefreshReport[]; manualRevisions: EvidenceRevision[]; selectedId: string; onSelect: (id: string) => void }) {
  const chains = useMemo(() => buildChains(snapshots), [snapshots]);
  const reportIds = new Set(reports.map((report) => report.revised_snapshot.id));
  const manualRevisionIds = new Set(manualRevisions.map((revision) => revision.revised_snapshot_id));
  return <article className="panel timeline-panel">
    <div className="panel-title"><span className="section-label">历史版本</span><span className="tag">immutable archive</span></div>
    <p className="timeline-intro">每条线是一条独立的版本链；不同根版本不代表连续修订。</p>
    <div className="chains">
      {chains.map((chain, chainIndex) => <div className="chain" key={chain[0]?.id ?? chainIndex}>
        <span className="chain-label">链 {String(chainIndex + 1).padStart(2, "0")}</span>
        {chain.map((snapshot) => <button key={snapshot.id} className={`timeline-item ${snapshot.id === selectedId ? "active" : ""}`} onClick={() => onSelect(snapshot.id)}>
          <i aria-hidden="true" />
          <span><strong>{formatDate(snapshot.feature_as_of_time)}</strong><small>{manualRevisionIds.has(snapshot.id) ? "证据修订 · 待人工核验" : reportIds.has(snapshot.id) ? "事件后修订" : snapshot.parent_snapshot_id ? (snapshot.revision_reason ?? "修订") : "原始存档"}</small></span>
        </button>)}
      </div>)}
    </div>
  </article>;
}

function Evaluation({ evaluation }: { evaluation: DashboardResponse["evaluation"] }) {
  const raw = evaluation?.models?.logistic_raw;
  if (!evaluation || !raw) return <section className="evaluation empty-evaluation"><span className="section-label">离线评测</span><p>当前没有可展示的固定离线评测摘要。</p></section>;
  const calibrated = evaluation.models?.logistic_calibrated;
  const classPrior = evaluation.models?.baseline_class_prior;
  return <section className="evaluation" aria-labelledby="evaluation-title">
    <div><span className="section-label">离线评测</span><h2 id="evaluation-title">固定历史样本上的走步验证</h2><p>{evaluation.scope ?? "离线基线评测，仅供比较。"}</p></div>
    <div className="comparison-wrap"><p className="comparison-note">本次固定评测中，校准模型没有改善 Brier 与 log loss。</p><table className="comparison"><thead><tr><th>模型</th><th>Accuracy</th><th>Brier</th><th>Log loss</th></tr></thead><tbody><MetricRow name="未校准 logistic" metrics={raw} /><MetricRow name="校准 logistic" metrics={calibrated} /><MetricRow name="class-prior" metrics={classPrior} /></tbody></table></div>
    <p className="fine-print">范围：{evaluation.test_rows ?? "—"} 个测试行，{evaluation.fold_count ?? "—"} 个时间顺序测试折；覆盖全体样本，并非 AAPL 专属表现。{evaluation.limitations?.[0] ? ` ${evaluation.limitations[0]}` : " 历史回测并非实时或独立交易表现。"}</p>
  </section>;
}

function EvaluationEmptyState() {
  return <section className="evaluation empty-evaluation evaluation-empty-state"><span className="section-label">离线评测</span><h2>没有保存的评测摘要</h2><p>当前股票没有可展示的固定历史评测。这里仅展示模型验证材料，不会回退显示预测档案或行情图。</p></section>;
}

function MetricRow({ name, metrics }: { name: string; metrics: { accuracy?: number; brier_multiclass?: number; log_loss?: number } | undefined }) { return <tr><th>{name}</th><td>{metrics?.accuracy === undefined ? "—" : formatPercent(metrics.accuracy)}</td><td>{metrics?.brier_multiclass?.toFixed(3) ?? "—"}</td><td>{metrics?.log_loss?.toFixed(3) ?? "—"}</td></tr>; }
function SafeLink({ href, children }: { href: string; children: ReactNode }) { return /^https:\/\//i.test(href) ? <a className="source-link" href={href} target="_blank" rel="noreferrer">{children}</a> : <span className="source-link disabled">来源链接不可用</span>; }
function isSupportedSymbol(symbol: string): symbol is SupportedSymbol { return (SUPPORTED_SYMBOLS as readonly string[]).includes(symbol); }
function sectionFromHash(): WorkspaceSection {
  const value = window.location.hash.replace(/^#/, "");
  return (["overview", "forecast", "evidence", "revisions", "evaluation"] as const).includes(value as WorkspaceSection)
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
function isOnDemandSnapshot(snapshot: Snapshot) { return snapshot.feature_snapshot_mode === "observed" && Boolean(snapshot.target_window); }
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
function filingAvailableAt(filing: OfficialFiling) {
  if (filing.accepted_at) {
    if (/^\d{14}$/.test(filing.accepted_at)) {
      const value = filing.accepted_at;
      return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}T${value.slice(8, 10)}:${value.slice(10, 12)}:${value.slice(12, 14)}Z`;
    }
    if (!Number.isNaN(Date.parse(filing.accepted_at))) return filing.accepted_at;
  }
  return `${filing.filed_at}T00:00:00Z`;
}
function reportForRevisedSnapshot(snapshotId: string, reports: RefreshReport[]) { return reports.find((report) => report.revised_snapshot.id === snapshotId); }
function reportForSnapshotInChain(snapshotId: string, reports: RefreshReport[]) { return reports.find((report) => report.revised_snapshot.id === snapshotId || report.original_snapshot.id === snapshotId); }
function latestSnapshot(snapshots: Snapshot[]) { return [...snapshots].sort((a, b) => Date.parse(b.feature_as_of_time) - Date.parse(a.feature_as_of_time) || Date.parse(b.created_at) - Date.parse(a.created_at) || b.version - a.version)[0]!; }
function buildChains(snapshots: Snapshot[]) {
  const byParent = new Map<string | null, Snapshot[]>();
  for (const snapshot of snapshots) byParent.set(snapshot.parent_snapshot_id, [...(byParent.get(snapshot.parent_snapshot_id) ?? []), snapshot]);
  const roots = byParent.get(null) ?? [];
  return roots.sort(sortSnapshot).map((root) => {
    const chain = [root]; let current = root;
    while ((byParent.get(current.id) ?? []).length === 1) { current = byParent.get(current.id)![0]; chain.push(current); }
    return chain;
  });
}
function sortSnapshot(a: Snapshot, b: Snapshot) { return Date.parse(a.feature_as_of_time) - Date.parse(b.feature_as_of_time); }
function isUsableCandle(value: PriceCandle) {
  return Boolean(value.trading_date) && [value.open, value.high, value.low, value.close].every((number) => Number.isFinite(number) && number > 0)
    && value.high >= Math.max(value.open, value.close) && value.low <= Math.min(value.open, value.close);
}
type TargetWindow = { start: string; end: string };
function targetWindowForSnapshot(report: RefreshReport | undefined, selected: Snapshot): TargetWindow | undefined {
  if (!report) return selected.target_window ?? undefined;
  return report.original_snapshot.id === selected.id ? report.target_windows.original : report.target_windows.revised;
}
function chartCandles(candles: PriceCandle[], selected: Snapshot, report: RefreshReport | undefined, target: TargetWindow | undefined) {
  const anchorDates = [selected.feature_trading_date, report?.original_snapshot.feature_trading_date, report?.revised_snapshot.feature_trading_date, target?.end]
    .filter((value): value is string => Boolean(value));
  const indexes = anchorDates.map((date) => candles.findIndex((candle) => candle.trading_date === date)).filter((index) => index >= 0);
  if (!indexes.length) return candles.slice(-90);
  const start = Math.max(0, Math.min(...indexes) - 18);
  const end = Math.min(candles.length, Math.max(...indexes) + 34);
  const focused = candles.slice(start, end);
  return focused.length > 96 ? focused.slice(0, 96) : focused;
}
function targetCoverage(candles: PriceCandle[], cutoffDate: string, targetStart: string, targetEnd: string) {
  const cutoff = candles.find((candle) => candle.trading_date === cutoffDate);
  const targetBars = candles.filter((candle) => candle.trading_date >= targetStart && candle.trading_date <= targetEnd);
  const spyAligned = Boolean(cutoff && cutoff.benchmark_close !== null && cutoff.benchmark_close !== undefined)
    && targetBars.every((candle) => candle.benchmark_close !== null && candle.benchmark_close !== undefined);
  return { complete: Boolean(cutoff) && targetBars.length === 20 && targetBars[0]?.trading_date === targetStart && targetBars.at(-1)?.trading_date === targetEnd && spyAligned };
}
function comparisonReturn(candles: PriceCandle[], startDate: string, endDate: string | null) {
  if (!endDate) return null;
  const start = candles.find((candle) => candle.trading_date === startDate);
  const end = candles.find((candle) => candle.trading_date === endDate);
  if (!start || !end) return null;
  const stock = end.close / start.close - 1;
  const benchmark = start.benchmark_close !== null && start.benchmark_close !== undefined && end.benchmark_close !== null && end.benchmark_close !== undefined
    ? end.benchmark_close / start.benchmark_close - 1
    : null;
  return { stock, benchmark };
}
function candleLabel(candle: PriceCandle) { return `${candle.trading_date}：开 ${formatPrice(candle.open)}，高 ${formatPrice(candle.high)}，低 ${formatPrice(candle.low)}，收 ${formatPrice(candle.close)}。`; }
function shortDate(value: string) { return value.slice(5).replace("-", "/"); }
function formatPrice(value: number) { return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value); }
function formatReturn(value: number) { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`; }
function formatPercent(value: number) { return `${(value * 100).toFixed(1)}%`; }
function signedPoints(value: number) { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}pp`; }
function probabilityLabel(key: "bearish" | "neutral" | "bullish") { return { bearish: "看跌", neutral: "中性", bullish: "看涨" }[key]; }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(value)); }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(new Date(value)); }
function formatSecAcceptedAt(value: string) {
  if (/^\d{14}$/.test(value)) return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)} ${value.slice(8, 10)}:${value.slice(10, 12)} UTC`;
  return Number.isNaN(Date.parse(value)) ? value : formatDateTime(value);
}
function excerptForDisplay(value: string) { return value.length > 6000 ? `${value.slice(0, 6000)}…` : value; }
