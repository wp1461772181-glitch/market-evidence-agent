import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, getDashboard } from "./api";
import type { Claim, DashboardResponse, RefreshReport, Snapshot } from "./types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; data: DashboardResponse }
  | { kind: "empty"; symbol: string }
  | { kind: "error"; message: string; symbol: string };

const DEFAULT_SYMBOL = "AAPL";

export function App() {
  const [input, setInput] = useState(DEFAULT_SYMBOL);
  const [requestedSymbol, setRequestedSymbol] = useState(DEFAULT_SYMBOL);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [evidenceOnly, setEvidenceOnly] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const requestVersion = useRef(0);

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
          setState({ kind: "empty", symbol: data.symbol });
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

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = input.trim().toUpperCase();
    if (!next) return;
    if (next === requestedSymbol) setRetryKey((value) => value + 1);
    else setRequestedSymbol(next);
  }

  if (state.kind === "loading") return <Shell input={input} setInput={setInput} submit={submit}><Loading symbol={requestedSymbol} /></Shell>;
  if (state.kind === "error") return <Shell input={input} setInput={setInput} submit={submit}><ErrorState message={state.message} retry={() => setRetryKey((value) => value + 1)} /></Shell>;
  if (state.kind === "empty") return <Shell input={input} setInput={setInput} submit={submit}><EmptyState symbol={state.symbol} /></Shell>;

  const { data } = state;
  const selected = data.snapshots.find((snapshot) => snapshot.id === selectedId) ?? latestSnapshot(data.snapshots);
  const selectedReport = reportForRevisedSnapshot(selected.id, data.refresh_reports);
  const evidenceIds = new Set(data.refresh_reports.map((report) => report.revised_snapshot.id));
  const pickable = evidenceOnly ? data.snapshots.filter((snapshot) => evidenceIds.has(snapshot.id)) : data.snapshots;

  return (
    <Shell input={input} setInput={setInput} submit={submit}>
      <main className="dashboard">
        <section className="headline" aria-labelledby="report-title">
          <p className="kicker">ARCHIVED RESEARCH / NO LIVE TRADE CALL</p>
          <div className="headline-row">
            <div>
              <h1 id="report-title">{data.symbol}<span> / evidence ledger</span></h1>
              <p className="lede">已保存的离线预测版本、证据与评测。这里展示历史研究记录，不生成新的预测。</p>
            </div>
            <p className="record-count"><strong>{data.snapshots.length}</strong><br />archived snapshots</p>
          </div>
        </section>

        <section className="version-strip" aria-label="选择存档版本">
          <div className="version-controls">
            <div>
              <span className="section-label">查看版本</span>
              <p>{evidenceOnly ? "仅显示带后续事件证据的修订版本" : "默认选中最新数据截止时间的版本"}</p>
            </div>
            <label className="evidence-toggle">
              <input
                type="checkbox"
                checked={evidenceOnly}
                disabled={data.refresh_reports.length === 0}
                onChange={(event) => {
                  const enabled = event.target.checked;
                  setEvidenceOnly(enabled);
                  if (enabled) {
                    const eventSnapshots = data.snapshots.filter((snapshot) => evidenceIds.has(snapshot.id));
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
                <small>{evidenceIds.has(snapshot.id) ? "事件后修订" : snapshot.version > 1 ? `第 ${snapshot.version} 版` : "存档版本"}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="analysis-grid">
          <ProbabilityPanel snapshot={selected} report={selectedReport} />
          <MetadataPanel snapshot={selected} report={selectedReport} />
        </section>

        <section className="lower-grid">
          <EvidencePanel report={selectedReport} selected={selected} />
          <Timeline snapshots={data.snapshots} reports={data.refresh_reports} selectedId={selected.id} onSelect={(id) => { setEvidenceOnly(false); setSelectedId(id); }} />
        </section>

        <Evaluation evaluation={data.evaluation} />
      </main>
    </Shell>
  );
}

function Shell({ input, setInput, submit, children }: { input: string; setInput: (value: string) => void; submit: (event: FormEvent) => void; children: ReactNode }) {
  return <div className="page-shell">
    <header className="site-header">
      <a className="wordmark" href="/" aria-label="Market Evidence Archive 首页">MARKET<br /><em>EVIDENCE</em></a>
      <form className="symbol-form" onSubmit={submit}>
        <label htmlFor="symbol">股票代码</label>
        <input id="symbol" value={input} onChange={(event) => setInput(event.target.value.toUpperCase())} maxLength={5} autoComplete="off" spellCheck="false" />
        <button type="submit">打开档案 <span>↗</span></button>
      </form>
    </header>
    {children}
    <footer>Market Evidence Agent · 所有结论均需人工审阅 · <time dateTime="2026-09-22">research archive</time></footer>
  </div>;
}

function Loading({ symbol }: { symbol: string }) {
  return <main className="state-card" aria-live="polite" aria-busy="true"><span className="loading-mark" aria-hidden="true" /><p className="kicker">RETRIEVING ARCHIVE</p><h1>正在读取 {symbol} 的已保存记录</h1><p>只请求本地已存档的版本与评测，不会调用模型或产生费用。</p></main>;
}

function EmptyState({ symbol }: { symbol: string }) {
  return <main className="state-card"><p className="kicker">NO ARCHIVED RECORD</p><h1>{symbol} 还没有可展示的离线预测版本。</h1><p>输入的代码有效，但目前没有保存的快照；可尝试 AAPL 查看现有示例。</p></main>;
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
  const target = report?.target_windows.revised;
  return <article className="panel metadata-panel">
    <div className="panel-title"><span className="section-label">数据边界</span><span className="folio">0{snapshot.version}</span></div>
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

function EvidencePanel({ report, selected }: { report?: RefreshReport; selected: Snapshot }) {
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

function Timeline({ snapshots, reports, selectedId, onSelect }: { snapshots: Snapshot[]; reports: RefreshReport[]; selectedId: string; onSelect: (id: string) => void }) {
  const chains = useMemo(() => buildChains(snapshots), [snapshots]);
  const reportIds = new Set(reports.map((report) => report.revised_snapshot.id));
  return <article className="panel timeline-panel">
    <div className="panel-title"><span className="section-label">历史版本</span><span className="tag">immutable archive</span></div>
    <p className="timeline-intro">每条线是一条独立的版本链；不同根版本不代表连续修订。</p>
    <div className="chains">
      {chains.map((chain, chainIndex) => <div className="chain" key={chain[0]?.id ?? chainIndex}>
        <span className="chain-label">链 {String(chainIndex + 1).padStart(2, "0")}</span>
        {chain.map((snapshot) => <button key={snapshot.id} className={`timeline-item ${snapshot.id === selectedId ? "active" : ""}`} onClick={() => onSelect(snapshot.id)}>
          <i aria-hidden="true" />
          <span><strong>{formatDate(snapshot.feature_as_of_time)}</strong><small>{reportIds.has(snapshot.id) ? "事件后修订" : snapshot.parent_snapshot_id ? (snapshot.revision_reason ?? "修订") : "原始存档"}</small></span>
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

function MetricRow({ name, metrics }: { name: string; metrics: { accuracy?: number; brier_multiclass?: number; log_loss?: number } | undefined }) { return <tr><th>{name}</th><td>{metrics?.accuracy === undefined ? "—" : formatPercent(metrics.accuracy)}</td><td>{metrics?.brier_multiclass?.toFixed(3) ?? "—"}</td><td>{metrics?.log_loss?.toFixed(3) ?? "—"}</td></tr>; }
function SafeLink({ href, children }: { href: string; children: ReactNode }) { return /^https:\/\//i.test(href) ? <a className="source-link" href={href} target="_blank" rel="noreferrer">{children}</a> : <span className="source-link disabled">来源链接不可用</span>; }
function reportForRevisedSnapshot(snapshotId: string, reports: RefreshReport[]) { return reports.find((report) => report.revised_snapshot.id === snapshotId); }
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
function formatPercent(value: number) { return `${(value * 100).toFixed(1)}%`; }
function signedPoints(value: number) { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}pp`; }
function probabilityLabel(key: "bearish" | "neutral" | "bullish") { return { bearish: "看跌", neutral: "中性", bullish: "看涨" }[key]; }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(value)); }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(new Date(value)); }
