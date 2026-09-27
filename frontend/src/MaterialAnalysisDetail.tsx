import { useEffect, useRef, useState } from "react";
import { ApiError, createMaterialAnalysisJob, fetchFilingContent, getMaterialAnalysis, getMaterialAnalysisHistory, getMaterialAnalysisJob, getMaterialOriginal } from "./api";
import type { MaterialAnalysisJob, MaterialAnalysisVersion, MaterialItem, MaterialOriginal } from "./types";

type DetailTab = "analysis" | "original" | "history";
type Load<T> = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; value: T } | { kind: "error"; message: string };
type Citation = { quote: string; start_char?: number; end_char?: number };
type CitationRange = { start: number; end: number; quote: string } | null;

export function MaterialAnalysisDetail({ material, initialAnalysisId = null, onUpdated }: { material: MaterialItem; initialAnalysisId?: string | null; onUpdated: () => void }) {
  const [tab, setTab] = useState<DetailTab>(material.latest_analysis_id ? "analysis" : "analysis");
  const [analysisId, setAnalysisId] = useState<string | null>(material.latest_analysis_id);
  const [focusedAnalysisId, setFocusedAnalysisId] = useState<string | null>(initialAnalysisId);
  const [analysis, setAnalysis] = useState<Load<MaterialAnalysisVersion>>({ kind: "idle" });
  const [history, setHistory] = useState<Load<MaterialAnalysisVersion[]>>({ kind: "idle" });
  const [original, setOriginal] = useState<Load<MaterialOriginal>>({ kind: "idle" });
  const [job, setJob] = useState<MaterialAnalysisJob | null>(material.latest_job);
  const [actionError, setActionError] = useState<string | null>(null);
  const analysisRequestSequence = useRef(0);
  const originalCitationRef = useRef<HTMLElement | null>(null);
  const [citationRange, setCitationRange] = useState<CitationRange>(null);

  useEffect(() => {
    setFocusedAnalysisId(initialAnalysisId);
  }, [initialAnalysisId, material.source_type, material.source_id]);

  useEffect(() => {
    const controller = new AbortController();
    const requestSequence = ++analysisRequestSequence.current;
    const selectedAnalysisId = focusedAnalysisId ?? material.latest_analysis_id;
    setAnalysisId(selectedAnalysisId);
    setJob(material.latest_job);
    setAnalysis(selectedAnalysisId ? { kind: "loading" } : { kind: "idle" });
    setHistory({ kind: "idle" });
    setOriginal({ kind: "idle" });
    setActionError(null);
    setCitationRange(null);
    if (selectedAnalysisId) {
      getMaterialAnalysis(selectedAnalysisId, controller.signal)
        .then((value) => { if (!controller.signal.aborted && requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "ready", value }); })
        .catch((error: unknown) => { if (!controller.signal.aborted && requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "error", message: actionMessage(error, "暂时无法读取这版分析。") }); });
    }
    return () => { controller.abort(); analysisRequestSequence.current += 1; };
  }, [material.source_type, material.source_id, material.latest_analysis_id, focusedAnalysisId]);

  useEffect(() => {
    if (!job || !isActive(job.status)) return;
    let stopped = false;
    let busy = false;
    const selectionSequence = analysisRequestSequence.current;
    const timer = window.setInterval(async () => {
      if (busy) return;
      busy = true;
      try {
        const next = await getMaterialAnalysisJob(job.job_id);
        if (stopped) return;
        setJob(next);
        if (!isActive(next.status)) {
          if (next.status === "succeeded" && next.analysis_id) {
            if (selectionSequence === analysisRequestSequence.current) {
              setAnalysisId(next.analysis_id);
              setAnalysis({ kind: "loading" });
            }
            const saved = await getMaterialAnalysis(next.analysis_id);
            if (!stopped && selectionSequence === analysisRequestSequence.current) setAnalysis({ kind: "ready", value: saved });
          } else if (next.status === "failed" || next.status === "blocked_data") {
            setActionError(jobError(next.safe_error_code));
          }
          if (!stopped) onUpdated();
        }
      } catch (error) {
        if (!stopped) setActionError(actionMessage(error, "读取分析任务状态失败；可以稍后重试。"));
      } finally { busy = false; }
    }, 1400);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [job?.job_id, job?.status, onUpdated]);

  useEffect(() => {
    if (tab !== "history") return;
    const controller = new AbortController();
    setHistory({ kind: "loading" });
    getMaterialAnalysisHistory(material.source_type, material.source_id, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setHistory({ kind: "ready", value: result.items }); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setHistory({ kind: "error", message: actionMessage(error, "暂时无法读取分析历史。") }); });
    return () => controller.abort();
  }, [tab, material.source_type, material.source_id]);

  useEffect(() => {
    if (tab !== "original") return;
    if (analysis.kind === "ready" && analysis.value.frozen_text) return;
    const controller = new AbortController();
    setOriginal({ kind: "loading" });
    getMaterialOriginal(material.source_type, material.source_id, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setOriginal({ kind: "ready", value }); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setOriginal({ kind: "error", message: actionMessage(error, "暂时无法读取原文。") }); });
    return () => controller.abort();
  }, [tab, analysis.kind === "ready" ? analysis.value.analysis_id : null, material.source_type, material.source_id]);

  async function startAnalysis(force: boolean) {
    setFocusedAnalysisId(null);
    setActionError(null);
    setJob({ job_id: "", status: "queued", analysis_id: null, cache_hit: false });
    try {
      const created = await createMaterialAnalysisJob(material.source_type, material.source_id, force);
      setJob(created);
      if (created.status === "succeeded" && created.analysis_id) {
        setAnalysisId(created.analysis_id);
        setAnalysis({ kind: "ready", value: await getMaterialAnalysis(created.analysis_id) });
        onUpdated();
      } else if (created.status === "blocked_data") {
        setActionError("这份材料目前没有可分析的正文。请先读取 SEC 原文，或核对已上传文件。" );
        onUpdated();
      }
    } catch (error) {
      setJob(material.latest_job);
      setActionError(actionMessage(error, "提交分析任务失败。"));
    }
  }

  async function fetchOriginal() {
    if (material.source_type !== "official_filing") return;
    try {
      const accession = material.title.split(" ").slice(1).join(" ");
      await fetchFilingContent(material.symbol, accession);
      setOriginal({ kind: "idle" });
      onUpdated();
    } catch (error) { setOriginal({ kind: "error", message: actionMessage(error, "读取 SEC 原文失败。请打开来源网站核对状态。") }); }
  }

  async function openHistoryVersion(id: string) {
    const requestSequence = ++analysisRequestSequence.current;
    setAnalysisId(id);
    setCitationRange(null);
    setAnalysis({ kind: "loading" });
    setTab("analysis");
    try {
      const value = await getMaterialAnalysis(id);
      if (requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "ready", value });
    } catch (error) {
      if (requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "error", message: actionMessage(error, "暂时无法读取选中的历史版本。") });
    }
  }

  function locateCitation(citation: Citation) {
    if (typeof citation.start_char !== "number" || typeof citation.end_char !== "number") return;
    setCitationRange({ start: citation.start_char, end: citation.end_char, quote: citation.quote });
    setTab("original");
  }

  useEffect(() => {
    if (tab === "original" && citationRange && originalCitationRef.current) {
      originalCitationRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [tab, citationRange, analysis]);

  const selectedAnalysis = analysis.kind === "ready" ? analysis.value : null;
  const frozen = selectedAnalysis?.frozen_text;
  const hasActiveJob = Boolean(job && isActive(job.status));
  const canAnalyze = material.can_view_original;
  return <section className="material-detail" aria-label={`${material.title} 详情`}>
    <div className="material-detail-heading"><div><p className="eyebrow">{material.source_type === "official_filing" ? "SEC 官方材料" : "用户上传材料"} · {material.symbol}</p><h2>{material.title}</h2><p>发布 {formatDate(material.published_at)} · 发现 {formatDate(material.observed_at)}</p></div><a href={material.source_url} target="_blank" rel="noopener noreferrer">打开来源 ↗</a></div>
    <div className="material-tabs" role="tablist" aria-label="材料详情">
      <button id="material-tab-analysis" role="tab" aria-selected={tab === "analysis"} aria-controls="material-panel" onClick={() => setTab("analysis")}>AI 分析</button>
      <button id="material-tab-original" role="tab" aria-selected={tab === "original"} aria-controls="material-panel" onClick={() => setTab("original")}>原始材料</button>
      <button id="material-tab-history" role="tab" aria-selected={tab === "history"} aria-controls="material-panel" onClick={() => setTab("history")}>分析历史</button>
    </div>
    <div className="material-tab-panel" id="material-panel" role="tabpanel" aria-labelledby={`material-tab-${tab}`}>
      {actionError && <p className="material-error" role="alert">{actionError}</p>}
      {tab === "analysis" && <>
        {hasActiveJob && <div className="material-job-status" role="status"><span className="loading-mark" aria-hidden="true" />{job?.status === "queued" ? "分析任务排队中…" : "正在分析这份材料…"}</div>}
        {job && (job.status === "failed" || job.status === "blocked_data") && <p className="material-error" role="status">最近一次任务{job.status === "failed" ? "失败" : "暂不可执行"}（{jobError(job.safe_error_code)}）。既有成功版本仍可查看。</p>}
        {analysis.kind === "loading" && <p className="material-empty">正在读取保存的分析版本…</p>}
        {analysis.kind === "error" && <p className="material-error" role="alert">{analysis.message}</p>}
        {selectedAnalysis && <AnalysisView analysis={selectedAnalysis} onLocateCitation={locateCitation} />}
        {!selectedAnalysis && analysis.kind !== "loading" && canAnalyze && <div className="material-start-card"><span className="section-label">单份材料分析</span><h3>这份材料还没有成功的分析版本</h3><p>分析会引用本次冻结的材料文本。保存后重新打开可直接查看，不会重复调用模型。</p><button className="primary-action" disabled={hasActiveJob} onClick={() => startAnalysis(false)}>{hasActiveJob ? "任务处理中…" : "开始分析"}</button></div>}
        {!canAnalyze && <div className="material-start-card"><span className="section-label">先读取正文</span><h3>SEC 目录记录没有材料正文</h3><p>请先通过现有 SEC 接口读取原文；目录标题不会作为正文提交分析。</p><button className="primary-action" disabled={hasActiveJob} onClick={fetchOriginal}>读取 SEC 原文</button></div>}
        {selectedAnalysis && <button className="secondary-action material-reanalyze" disabled={hasActiveJob} onClick={() => startAnalysis(true)}>{hasActiveJob ? "重新分析中…" : "重新分析"}</button>}
      </>}
      {tab === "original" && <OriginalView material={material} analysis={selectedAnalysis} original={original} onFetch={fetchOriginal} citationRange={citationRange} citationRef={originalCitationRef} />}
      {tab === "history" && <HistoryView state={history} selectedAnalysisId={analysisId} onSelect={openHistoryVersion} />}
    </div>
  </section>;
}

function AnalysisView({ analysis, onLocateCitation }: { analysis: MaterialAnalysisVersion; onLocateCitation: (citation: Citation) => void }) {
  const payload = analysis.payload;
  const truncated = Boolean(analysis.source_manifest.truncated || analysis.source_manifest.coverage_incomplete);
  return <article className="analysis-version">
    <div className="analysis-version-meta"><span>分析 v{analysis.version_no}</span><span>{formatDate(analysis.created_at)}</span>{truncated && <strong>仅分析可用摘录</strong>}</div>
    <p className="analysis-summary">{payload.summary}</p>
    <AnalysisSection title="原文事实" rows={payload.facts} empty="未提取到有引用支持的事实。" onLocateCitation={onLocateCitation} />
    <AnalysisSection title="潜在利好" rows={payload.supporting} empty="当前材料没有足够依据支持利好判断。" onLocateCitation={onLocateCitation} />
    <AnalysisSection title="潜在利空 / 反证" rows={payload.counter} empty="当前材料没有足够依据支持利空判断。" onLocateCitation={onLocateCitation} />
    <AnalysisSection title="未知与不确定" rows={payload.uncertainties} empty="没有记录额外的不确定项。" onLocateCitation={onLocateCitation} />
    {payload.key_numbers.length > 0 && <section className="analysis-section"><h3>关键数字</h3>{payload.key_numbers.map((item, index) => <div className="analysis-number" key={`${item.name}:${index}`}><strong>{item.name}</strong><span>{item.value_text}{item.period ? ` · ${item.period}` : ""}</span><CitationList citations={item.citations} onLocateCitation={onLocateCitation} /></div>)}</section>}
  </article>;
}

function AnalysisSection({ title, rows, empty, onLocateCitation }: { title: string; rows: Array<{ id: string; statement: string; rationale?: string; reason?: string; citations: Citation[] }>; empty: string; onLocateCitation: (citation: Citation) => void }) {
  return <section className="analysis-section"><h3>{title}<span>{rows.length}</span></h3>{rows.length ? rows.map((row) => <article key={row.id}><p>{row.statement}</p>{(row.rationale || row.reason) && <small>{row.rationale || row.reason}</small>}<CitationList citations={row.citations} onLocateCitation={onLocateCitation} /></article>) : <p className="analysis-quiet">{empty}</p>}</section>;
}
function CitationList({ citations, onLocateCitation }: { citations: Citation[]; onLocateCitation: (citation: Citation) => void }) { return citations?.length ? <blockquote className="material-citation">{citations.map((citation, index) => <p key={`${citation.quote}:${index}`}><button type="button" onClick={() => onLocateCitation(citation)} aria-label="在冻结原文中定位这条引文">“{citation.quote}” ↗</button></p>)}</blockquote> : null; }

function OriginalView({ material, analysis, original, onFetch, citationRange, citationRef }: { material: MaterialItem; analysis: MaterialAnalysisVersion | null; original: Load<MaterialOriginal>; onFetch: () => void; citationRange: CitationRange; citationRef: { current: HTMLElement | null } }) {
  if (analysis?.frozen_text) return <article className="frozen-original"><div className="analysis-version-meta"><span>与分析 v{analysis.version_no} 一同冻结</span>{Boolean(analysis.source_manifest.truncated || analysis.source_manifest.coverage_incomplete) && <strong>仅分析可用摘录</strong>}</div><pre><HighlightedText text={analysis.frozen_text} range={citationRange} citationRef={citationRef} /></pre>{material.can_download_original && <a className="secondary-action download-original" href={`/api/v3/materials/${material.source_type}/${encodeURIComponent(material.source_id)}/download`}>下载原始文件</a>}</article>;
  if (original.kind === "loading" || original.kind === "idle") return <p className="material-empty">正在读取已保存的原文…</p>;
  if (original.kind === "error") return <p className="material-error" role="alert">{original.message}</p>;
  const value = original.value;
  if (!value.content_text) return <div className="material-start-card"><h3>目前只保存了 SEC 目录</h3><p>{value.content_error || "读取 SEC 原文后才能进行分析；系统不会把申报标题当正文。"}</p><button className="primary-action" disabled={!value.can_fetch} onClick={onFetch}>读取 SEC 原文</button></div>;
  return <article className="frozen-original"><div className="analysis-version-meta"><span>{material.source_type === "official_filing" ? "已保存的 SEC 原文摘录" : "上传材料提取文本"}</span>{value.truncated && <strong>内容不完整</strong>}<span>{value.document_name || ""}</span></div><pre>{value.content_text}</pre>{material.can_download_original && <a className="secondary-action download-original" href={`/api/v3/materials/${material.source_type}/${encodeURIComponent(material.source_id)}/download`}>下载原始文件</a>}</article>;
}

function HighlightedText({ text, range, citationRef }: { text: string; range: CitationRange; citationRef: { current: HTMLElement | null } }) {
  if (!range || range.start < 0 || range.end > text.length || range.end <= range.start || text.slice(range.start, range.end) !== range.quote) return <>{text}</>;
  return <>{text.slice(0, range.start)}<mark ref={citationRef}>{text.slice(range.start, range.end)}</mark>{text.slice(range.end)}</>;
}

function HistoryView({ state, selectedAnalysisId, onSelect }: { state: Load<MaterialAnalysisVersion[]>; selectedAnalysisId: string | null; onSelect: (id: string) => void }) {
  if (state.kind === "loading" || state.kind === "idle") return <p className="material-empty">正在读取分析历史…</p>;
  if (state.kind === "error") return <p className="material-error" role="alert">{state.message}</p>;
  if (state.value.length === 0) return <p className="material-empty">还没有保存成功的分析版本。失败的尝试只记录在最近任务状态中。</p>;
  return <ol className="analysis-history">{state.value.map((version) => <li key={version.analysis_id}><button className={selectedAnalysisId === version.analysis_id ? "is-selected" : ""} onClick={() => onSelect(version.analysis_id)}><span><strong>分析 v{version.version_no}</strong><small>{formatDate(version.created_at)} · {version.actual_model}</small></span><span>查看 →</span></button></li>)}</ol>;
}

function isActive(status: string) { return status === "queued" || status === "running"; }
function jobError(code?: string | null) {
  if (code === "no_content") return "没有可分析正文，请先读取 SEC 原文";
  if (code === "invalid_model_output") return "分析输出未通过引用或格式校验，可重新分析";
  if (code === "provider_error") return "分析服务暂时不可用，可重试";
  if (code === "config_version_changed") return "分析配置已更新，请新建任务";
  return code || "请检查正文后重试";
}
function actionMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) return error.status === 402 ? "模型服务余额或账单状态需要处理后再试。" : error.status === 429 ? "模型服务暂时限流，请稍后重试。" : error.message;
  return fallback;
}
function formatDate(value: string | null | undefined) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value.slice(0, 16) : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(date);
}
