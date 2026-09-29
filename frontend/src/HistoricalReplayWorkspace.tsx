import { Tx, formatDate as formatLocalizedDate, getLocalePreference, translatePhrase, useLocale } from "./i18n";
import { ResearchBriefView } from "./ResearchBriefView";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ApiError, createV2HistoricalReplayJob, getV2Evaluations, getV2ForecastVersion, getV2Job } from "./api";
import type { ResearchMaterialReference, V2EvaluationResponse, V2EvaluationVersion, V2ForecastJob, V2VersionDetail } from "./types";

const SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"] as const;
type ReplayResult = { version: V2VersionDetail; evaluation: V2EvaluationVersion | null };

export function HistoricalReplayWorkspace({ initialSymbol }: { initialSymbol: string }) {
  const { locale, t } = useLocale();
  const [symbol, setSymbol] = useState<string>(initialSymbol);
  const [decisionDate, setDecisionDate] = useState(defaultReplayDate);
  const [job, setJob] = useState<V2ForecastJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [evaluationState, setEvaluationState] = useState<{ kind: "loading" } | { kind: "ready"; data: V2EvaluationResponse } | { kind: "error"; message: string }>({ kind: "loading" });
  const [evaluationRefresh, setEvaluationRefresh] = useState(0);
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [result, setResult] = useState<{ kind: "loading" } | { kind: "ready"; data: ReplayResult } | { kind: "error"; message: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const historicalVersions = useMemo(() => {
    if (evaluationState.kind !== "ready") return [] as V2EvaluationVersion[];
    return evaluationState.data.cohorts.historical_research.roots
      .flatMap((root) => root.versions)
      .sort((a, b) => b.decision_at.localeCompare(a.decision_at));
  }, [evaluationState]);

  useEffect(() => {
    if (evaluationState.kind === "ready" && !selectedVersionId && historicalVersions.length > 0) {
      setSelectedVersionId(historicalVersions[0].id);
    }
  }, [evaluationState.kind, historicalVersions, selectedVersionId]);

  useEffect(() => {
    const controller = new AbortController();
    setEvaluationState({ kind: "loading" });
    setSelectedVersionId("");
    setResult(null);
    getV2Evaluations(symbol, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setEvaluationState({ kind: "ready", data }); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setEvaluationState({ kind: "error", message: actionMessage(error, "暂时无法读取历史回放记录。") }); });
    return () => controller.abort();
  }, [symbol, evaluationRefresh]);

  useEffect(() => {
    if (!selectedVersionId) return;
    const controller = new AbortController();
    setResult({ kind: "loading" });
    getV2ForecastVersion(selectedVersionId, controller.signal)
      .then((version) => {
        if (controller.signal.aborted) return;
        const evaluation = historicalVersions.find((item) => item.id === selectedVersionId) ?? null;
        setResult({ kind: "ready", data: { version, evaluation } });
      })
      .catch((error: unknown) => { if (!controller.signal.aborted) setResult({ kind: "error", message: actionMessage(error, "暂时无法读取这条回放详情。") }); });
    return () => controller.abort();
  }, [selectedVersionId, historicalVersions]);

  useEffect(() => {
    if (!job || isTerminal(job.status)) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const updated = await getV2Job(job.id);
        if (stopped) return;
        setJob(updated);
        if (updated.status === "succeeded" || updated.status === "succeeded_no_change") {
          if (updated.result_version_id) setSelectedVersionId(updated.result_version_id);
          setEvaluationRefresh((value) => value + 1);
          return;
        }
        if (isTerminal(updated.status)) return;
      } catch {
        // Keep polling across a temporary API interruption; the job is durable.
      }
      if (!stopped) timer = window.setTimeout(poll, 2_000);
    };
    void poll();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [job?.id, job?.status]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setJobError(null);
    setJob(null);
    setResult(null);
    setSelectedVersionId("");
    try {
      const created = await createV2HistoricalReplayJob(symbol, decisionDate);
      setJob(created);
    } catch (error) {
      setJobError(actionMessage(error, "无法提交历史回放。"));
    } finally {
      setSubmitting(false);
    }
  }

  return <section className="workspace-stack historical-replay-workspace">
    <section className="workspace-intro">
      <div><p className="eyebrow">{<Tx text={"历史研究 · 单独分组"} />}</p><h2>{<Tx text={"回放一个已经到期的预测"} />}</h2><p>{<Tx text={"选择股票和历史决策日，系统会按当时可用行情建立 20 个交易日目标，再读取目标期的真实结果。历史回放单独归档，不会进入当前预测或真实前向表现。"} />}</p></div>
    </section>

    <form className="historical-replay-form" onSubmit={submit}>
      <label>{<Tx text={"股票代码"} />}<select value={symbol} onChange={(event) => setSymbol(event.target.value)}>{SYMBOLS.map((ticker) => <option key={ticker} value={ticker}>{ticker}</option>)}</select></label>
      <label>{<Tx text={"历史决策日"} />}<input type="date" value={decisionDate} max={todayLocal()} onChange={(event) => setDecisionDate(event.target.value)} required /></label>
      <button className="primary-action" type="submit" disabled={submitting || (job !== null && !isTerminal(job.status))}>{submitting ? t("正在提交…") : t("生成历史回放")}</button>
      <p>{<Tx text={"只能选择已收盘的 XNYS 交易日，并且该日之后的 20 个交易日必须已经结束。默认日期选在约一个月前；若遇到较长假期，请再选早一些。"} />}</p>
    </form>

    {jobError && <p className="inventory-status inventory-error" role="alert">{jobError}</p>}
    {job && <section className={`replay-job-status ${job.status === "failed" || job.status === "blocked_data" ? "has-error" : ""}`} aria-live="polite">
      <div><p className="eyebrow">{t(job.time_mode === "historical_research" ? "历史研究任务" : "预测任务")}</p><h3>{jobStatusLabel(job.status)}</h3><p>{job.symbol} · {job.requested_decision_at ? formatDate(job.requested_decision_at) : decisionDate} · {t("阶段")}: {translatePhrase(job.current_stage)}</p></div>
      {job.error?.type && <p className="replay-error-code">{t("未生成回放：")} {job.error.type}</p>}
    </section>}

    <section className="replay-history-section">
      <div className="section-heading"><div><p className="eyebrow">{symbol} · {t("回放样本")}</p><h2>{<Tx text={"历史记录与到期结果"} />}</h2></div><span>{locale === "en-US" ? `${historicalVersions.length} ${historicalVersions.length === 1 ? "replay" : "replays"}` : `${historicalVersions.length} ${t("条")}`}</span></div>
      {evaluationState.kind === "loading" && <p className="inventory-status">{<Tx text={"正在读取历史回放记录…"} />}</p>}
      {evaluationState.kind === "error" && <p className="inventory-status inventory-error" role="status">{evaluationState.message}</p>}
      {evaluationState.kind === "ready" && <>
        {historicalVersions.length === 0 ? <div className="evaluation-empty-state"><h3>{<Tx text={"还没有历史回放"} />}</h3><p>{<Tx text={"提交上方表单后，回放会显示在这里，并在 20 个交易日目标到期后关联真实结果。"} />}</p></div> : <div className="replay-history-list">{historicalVersions.map((item) => <button type="button" className={`replay-history-item ${selectedVersionId === item.id ? "is-selected" : ""}`} key={item.id} onClick={() => setSelectedVersionId(item.id)}>
          <span><strong>{formatDate(item.decision_at)}</strong><small>{t("第")} {item.version_no} {t("版")} · {t(item.trigger_type === "historical_replay" ? "历史回放" : "历史研究")}</small></span>
          <span className={`replay-outcome replay-outcome-${item.latest_evaluation?.status ?? "pending"}`}>{evaluationLabel(item.latest_evaluation?.status ?? "pending")}{item.latest_evaluation?.actual_label ? ` · ${directionLabel(item.latest_evaluation.actual_label)}` : ""}</span>
        </button>)}</div>}
      </>}
    </section>

    {result?.kind === "loading" && <p className="inventory-status">{<Tx text={"正在读取预测与到期结果…"} />}</p>}
    {result?.kind === "error" && <p className="inventory-status inventory-error" role="status">{result.message}</p>}
    {result?.kind === "ready" && <ReplayDetail result={result.data} />}
  </section>;
}

function ReplayDetail({ result }: { result: ReplayResult }) {
  const { t } = useLocale();
  const { version, evaluation } = result;
  const probabilities = version.decision_probabilities ?? version.joint_probabilities ?? version.baseline_probabilities;
  const targetEnd = String(version.target_contract.target_end_date ?? t("未知"));
  const evaluationResult = evaluation?.latest_evaluation ?? null;
  return <section className="replay-detail" aria-labelledby="replay-detail-title">
    <div className="section-heading"><div><p className="eyebrow">{t("历史回放详情")} · {version.symbol}</p><h2 id="replay-detail-title">{t("决策日")} {formatDate(version.decision_at)}</h2></div><span>{t("目标截止")} {targetEnd}</span></div>
    {version.research_brief && <p className="historical-replay-warning">{<Tx text={"回放输入可能含有当时之后才被系统获取的材料。页面会保留材料发布时间与实际获取时间；该预测用于研究复盘，不代表当时真实可得的信息。"} />}</p>}
    {probabilities ? <div className="replay-probabilities">{(["bearish", "neutral", "bullish"] as const).map((key) => <div className="replay-probability" key={key}><span>{directionLabel(key)}</span><div className="replay-meter"><i className={`replay-meter-${key}`} style={{ width: `${Math.max(0, Math.min(100, probabilities[key] * 100))}%` }} /></div><strong>{(probabilities[key] * 100).toFixed(1)}%</strong></div>)}</div> : <p className="inventory-status">{<Tx text={"这条回放没有输出数值概率；请查看研究简报与模型状态。"} />}</p>}
    <div className="replay-result-grid"><article><span>{<Tx text={"预测模型状态"} />}</span><strong>{modelLabel(version.model_status)}</strong><small>{t("任务")}：{jobStatusLabel("succeeded")}</small></article><article><span>{<Tx text={"到期评估"} />}</span><strong>{evaluationLabel(evaluationResult?.status ?? "pending")}</strong><small>{evaluationResult?.label_available_at ? `${t("实际结果于")} ${formatDate(evaluationResult.label_available_at)} ${t("可用")}` : t("等待或无法取得目标期价格")}</small></article><article><span>{<Tx text={"真实走势"} />}</span><strong>{evaluationResult?.actual_label ? directionLabel(evaluationResult.actual_label) : "—"}</strong><small>{evaluationResult?.actual_target_close != null ? `${t("目标期收盘价")} $${evaluationResult.actual_target_close.toFixed(2)}` : t("暂无可用收盘价")}</small></article><article><span>Brier / Log loss</span><strong>{evaluationResult?.brier_score != null ? evaluationResult.brier_score.toFixed(4) : "—"} / {evaluationResult?.log_loss != null ? evaluationResult.log_loss.toFixed(4) : "—"}</strong><small>{<Tx text={"只在可计算时显示"} />}</small></article></div>
    {version.research_brief && <ResearchBriefView
      brief={version.research_brief}
      versionId={version.id}
      probabilities={version.model_status === "experimental_jev" ? version.decision_probabilities ?? null : null}
      modelManifest={version.model_manifest}
      evidenceManifest={version.evidence_version_manifest}
      onOpenAnalysis={openReplayMaterial}
    />}
  </section>;
}

function openReplayMaterial(source: ResearchMaterialReference) {
  try {
    window.sessionStorage.setItem("market-evidence-agent:material-analysis-focus", JSON.stringify({
      symbol: source.symbol,
      source_type: source.source_type,
      source_id: source.source_id,
      analysis_id: source.analysis_id,
    }));
  } catch { /* The material library can still be opened without the focus hint. */ }
  window.location.hash = "materials";
}

function defaultReplayDate() {
  const date = new Date();
  date.setDate(date.getDate() - 32);
  while (date.getDay() === 0 || date.getDay() === 6) date.setDate(date.getDate() - 1);
  return localDate(date);
}
function todayLocal() { return localDate(new Date()); }
function localDate(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function isTerminal(status: V2ForecastJob["status"]) { return ["succeeded", "succeeded_no_change", "blocked_data", "failed"].includes(status); }
function actionMessage(error: unknown, fallback: string) { return translatePhrase(error instanceof ApiError ? error.message : fallback); }
function jobStatusLabel(status: V2ForecastJob["status"] | "succeeded") { return translatePhrase(({ queued: "等待处理", running: "正在生成", succeeded: "回放已生成", succeeded_no_change: "已有相同回放", blocked_data: "资料不足，回放未生成", failed: "回放任务失败" } as const)[status]); }
function evaluationLabel(status: string) { return translatePhrase(({ pending: "等待评估", succeeded: "已完成评估", blocked_price: "行情不足", failed: "评估失败" } as Record<string, string>)[status] ?? "待评估"); }
function directionLabel(direction: string) { return translatePhrase(({ bearish: "看跌", neutral: "中性", bullish: "看涨" } as Record<string, string>)[direction] ?? direction); }
function modelLabel(status: string) { return translatePhrase(({ research_only: "研究简报", experimental_jev: "Jev 实验模型", experimental_joint: "联合研究模型", baseline_only: "基线模型" } as Record<string, string>)[status] ?? status); }
function formatDate(value: string) { const date = new Date(value); return formatLocalizedDate(date, getLocalePreference(), "UTC"); }
