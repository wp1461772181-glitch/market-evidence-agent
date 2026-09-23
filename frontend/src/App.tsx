import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, getDashboard } from "./api";
import type { Claim, DashboardResponse, PriceCandle, PriceHistory, RefreshReport, Snapshot } from "./types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; data: DashboardResponse }
  | { kind: "empty"; data: DashboardResponse }
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

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = input.trim().toUpperCase();
    if (!next) return;
    if (next === requestedSymbol) setRetryKey((value) => value + 1);
    else setRequestedSymbol(next);
  }

  if (state.kind === "loading") return <Shell input={input} setInput={setInput} submit={submit}><Loading symbol={requestedSymbol} /></Shell>;
  if (state.kind === "error") return <Shell input={input} setInput={setInput} submit={submit}><ErrorState message={state.message} retry={() => setRetryKey((value) => value + 1)} /></Shell>;
  if (state.kind === "empty") return <Shell input={input} setInput={setInput} submit={submit}><EmptyState data={state.data} /></Shell>;

  const { data } = state;
  const selected = data.snapshots.find((snapshot) => snapshot.id === selectedId) ?? latestSnapshot(data.snapshots);
  const selectedReport = reportForRevisedSnapshot(selected.id, data.refresh_reports);
  const selectedChainReport = reportForSnapshotInChain(selected.id, data.refresh_reports);
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

        <PriceComparisonPanel
          key={selected.id}
          history={data.price_history}
          selected={selected}
          report={selectedChainReport}
        />

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
    <p className="price-intro">蜡烛图来自已存日线。竖线标出版本数据截止日；阴影仅在有保存的滚动修订报告时表示目标窗口。</p>
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
      <div><span>{coverage?.complete ? "版本截止 → 目标结束" : "版本截止 → 已存行情日期"}</span><strong>{comparison ? formatReturn(comparison.stock) : "—"}</strong><small>股票</small></div>
      <div><span>同期 SPY</span><strong>{comparison?.benchmark === null || !comparison ? "—" : formatReturn(comparison.benchmark)}</strong><small>{comparison?.benchmark === null ? "无完整对齐基准" : "基准"}</small></div>
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
  const x = (index: number) => margin.left + ((index + 0.5) / candles.length) * plotWidth;
  const y = (value: number) => margin.top + ((max - value) / (max - min)) * plotHeight;
  const bodyWidth = Math.max(2, Math.min(10, (plotWidth / candles.length) * 0.58));
  const originalDate = report?.original_snapshot.feature_trading_date;
  const revisedDate = report?.revised_snapshot.feature_trading_date;
  const markerIndex = (date: string | undefined) => date ? candles.findIndex((candle) => candle.trading_date === date) : -1;
  const originalIndex = markerIndex(originalDate);
  const revisedIndex = markerIndex(revisedDate ?? selected?.feature_trading_date);
  const targetStart = markerIndex(target?.start);
  const targetEnd = markerIndex(target?.end);
  const actualStart = targetEnd >= 0 ? targetEnd + 1 : -1;
  const ticks = [max, (max + min) / 2, min];
  const dateTicks = [0, Math.floor((candles.length - 1) / 2), candles.length - 1];

  return <figure className="candlestick-figure">
    <svg className="candlestick-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="candle-chart-title candle-chart-description">
      <title id="candle-chart-title">{selected?.symbol ?? "股票"} 历史日线蜡烛图</title>
      <desc id="candle-chart-description">使用鼠标停留或键盘聚焦蜡烛图中的日线，读取当天开盘、最高、最低和收盘价格。{target ? "阴影区域是保存的 20 个交易日滚动目标窗口。" : "当前版本没有保存的滚动目标窗口。"}</desc>
      {ticks.map((tick) => <g key={tick}><line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} className="chart-grid" /><text x={width - margin.right + 9} y={y(tick) + 4} className="chart-price-label">{formatPrice(tick)}</text></g>)}
      {targetStart >= 0 && targetEnd >= targetStart && <g className="target-window"><rect x={x(targetStart) - bodyWidth} y={margin.top} width={x(targetEnd) - x(targetStart) + bodyWidth * 2} height={plotHeight} /><text x={x(targetStart) + 5} y={margin.top + 14}>20-session target</text></g>}
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
      {dateTicks.map((index) => <text key={index} x={x(index)} y={height - 10} textAnchor="middle" className="chart-date-label">{shortDate(candles[index]!.trading_date)}</text>)}
    </svg>
    <figcaption><span><i className="legend-up" />收高于开</span><span><i className="legend-down" />收低于开</span>{target && <span><i className="legend-window" />保存的目标窗口</span>}</figcaption>
  </figure>;
}

function Marker({ x, label, tone }: { x: number; label: string; tone?: "revised" }) { return <g className={`snapshot-marker ${tone ?? ""}`}><line x1={x} x2={x} y1={19} y2={287} /><text x={x + 5} y={18}>{label}</text></g>; }

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
type TargetWindow = RefreshReport["target_windows"]["revised"];
function targetWindowForSnapshot(report: RefreshReport | undefined, selected: Snapshot): TargetWindow | undefined {
  if (!report) return undefined;
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
