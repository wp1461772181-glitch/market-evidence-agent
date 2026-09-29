import { Tx, formatDateTime, getLocalePreference, translatePhrase, useLocale } from "./i18n";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, createV2ForecastJob, createV2ManualRevisionJob, getMaterialLibrary, getUploadedEvidence, getV2Evaluations, getV2ForecastRoots, getV2ForecastVersion, getV2Job, getV2MonitorStatus, getV2Timeline, getV2Workspace } from "./api";
import type { FilingInventory, MaterialItem, ResearchMaterialReference, UploadedEvidence, V2EvaluationCohort, V2EvaluationResponse, V2EvaluationRoot, V2EvaluationVersion, V2ForecastJob, V2ForecastRoot, V2ForecastRoots, V2ModelEvaluationCohort, V2MonitorStatus, V2Probabilities, V2SourceRef, V2Timeline, V2TimelineEntry, V2VersionDetail, V2Workspace } from "./types";
import { ResearchBriefView } from "./ResearchBriefView";
import { evidenceTitle, processingStatus, sourceKey } from "./EvidenceProcessing";
import { CandlestickChart } from "./CandlestickChart";
import type { PriceCandle, PriceHistory } from "./types";

type ResourceState<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "error"; message: string };

type SourceOption = V2SourceRef & {
  label: string;
  publishedAt: string | null;
  observedAt: string | null;
  contentHash: string | null;
  reviewStatus: string | null;
  reviewedAt: string | null;
  userRatingStars: number | null;
};

function isTimezoneAwareTimestamp(value: string | null): boolean {
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return false;
  return Number.isFinite(Date.parse(value));
}

function errorMessage(error: unknown, fallback: string) {
  return translatePhrase(error instanceof ApiError ? error.message : fallback);
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

async function getAllMaterialItems(symbol: string, signal: AbortSignal): Promise<MaterialItem[]> {
  const limit = 50;
  const first = await getMaterialLibrary(symbol, { limit, offset: 0, signal });
  const pageCount = Math.ceil(Math.max(0, first.total - first.items.length) / limit);
  if (pageCount === 0) return first.items;
  const rest = await Promise.all(Array.from({ length: pageCount }, (_, index) =>
    getMaterialLibrary(symbol, { limit, offset: (index + 1) * limit, signal }),
  ));
  return [...first.items, ...rest.flatMap((page) => page.items)];
}

function formatTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : formatDateTime(date, getLocalePreference(), "UTC");
}

function modelStatusText(status: V2Workspace["joint_model_status"] | V2TimelineEntry["model_status"]) {
  if (status === "experimental_jev") return translatePhrase("Jev 实验判断（尚未校准）");
  if (status === "experimental_joint") return translatePhrase("联合研究模型");
  if (status === "baseline_only") return translatePhrase("行情基线模型");
  if (status === "research_only") return translatePhrase("研究结果（未输出概率）");
  return translatePhrase("当前模型尚未验证");
}

function jobStatusText(job: V2ForecastJob) {
  if (job.status === "queued") return translatePhrase("已排队，等待处理");
  if (job.status === "running") return translatePhrase("正在处理");
  if (job.status === "succeeded") return translatePhrase("已生成预测版本");
  if (job.status === "succeeded_no_change") return translatePhrase("已完成，但输入没有产生新版本");
  if (job.status === "blocked_data") return `${translatePhrase("未生成预测：")}${jobFailureText(job.error?.type)}`;
  return `${translatePhrase("处理未完成：")}${jobFailureText(job.error?.type)}`;
}

function jobFailureText(code?: string | null) {
  if (!code) return translatePhrase("任务没有返回可识别的失败原因。");
  const known: Record<string, string> = {
    research_brief_failed: "研究简报构建失败，请查看服务日志后重试。",
    research_brief_provider_error: "DeepSeek 简报服务暂不可用，可稍后重新创建任务。",
    research_brief_invalid_model_output: "DeepSeek 简报格式或引用校验未通过，可重新创建任务。",
    research_brief_invalid_background_reference: "模型把新材料当成背景材料引用，未通过来源分类校验；请重新创建预测。",
    research_brief_provider_required: "当前没有可用的 DeepSeek 配置，无法生成简报。",
    research_brief_invalid_brief_output: "简报没有通过结构校验，未发布预测。",
    jev_missing_api_key: "缺少 Jev 服务配置，未生成数值判断。",
    jev_authorization_or_billing_error: "Jev 服务的授权或账单状态需要处理。",
    jev_upstream_unavailable: "Jev 服务暂不可用，可稍后重新创建任务。",
    jev_transport_error: "连接 Jev 服务失败，可稍后重新创建任务。",
    market_refresh_failed: "行情刷新失败，可稍后重试。",
    no_completed_market_session: "没有可用的已完成交易日行情。",
    insufficient_observed_market_history: "可观察行情不足，暂时无法建立预测输入。",
  };
  if (known[code]) return translatePhrase(known[code]);
  if (code.startsWith("research_brief_")) return translatePhrase("研究简报未通过处理或引用校验，未发布预测。");
  if (code.startsWith("jev_")) return translatePhrase("Jev 判断未完成，未发布数值概率。");
  return `${translatePhrase("任务未完成（")}${code}${translatePhrase("），没有发布预测版本。")}`;
}

export function V2ForecastWorkspace({ symbol, filings, priceHistory, mode, onOpenForecast }: { symbol: string; filings: FilingInventory | null; priceHistory?: PriceHistory | null; mode: "forecast" | "revisions"; onOpenForecast: () => void }) {
  const { t } = useLocale();
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
  const [revisionParentDetail, setRevisionParentDetail] = useState<ResourceState<V2VersionDetail> | null>(null);
  const [revisionMaterialMetadata, setRevisionMaterialMetadata] = useState<{ parentVersionId: string; items: MaterialItem[] } | null>(null);
  const [revisionMaterialMetadataError, setRevisionMaterialMetadataError] = useState<string | null>(null);
  const [revisionMaterialMetadataLoading, setRevisionMaterialMetadataLoading] = useState(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const [next, predictionList] = await Promise.all([getV2Workspace(symbol, signal), getV2ForecastRoots(symbol, signal)]);
      setWorkspace({ kind: "ready", data: next });
      setRoots({ kind: "ready", data: predictionList });
      setSelectedRootId((previous) => predictionList.roots.some((root) => root.id === previous) ? previous : predictionList.roots[0]?.id ?? "");
    } catch (error) {
      if (signal?.aborted) return;
      setWorkspace({ kind: "error", message: errorMessage(error, "暂时无法读取预测工作台。") });
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
      if (!signal?.aborted) setEvaluations({ kind: "error", message: errorMessage(error, "暂时无法读取到期评估。") });
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
        .catch((error: unknown) => setActionError(errorMessage(error, "无法刷新预测任务状态。")));
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
    setSourceKey("");
    setRevisionMaterialMetadata(null);
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
        observedAt: filing.content_observed_at ?? filing.observed_at ?? null,
        label: `SEC ${filing.form} · ${filing.accession_number}`,
        contentHash: filing.content_excerpt_sha256 ?? null,
        reviewStatus: filing.review_status ?? null,
        reviewedAt: filing.reviewed_at ?? null,
        userRatingStars: null,
      }));
    const media = uploaded.kind === "ready"
      ? uploaded.data.map((item) => ({
        source_type: "uploaded_media" as const,
        source_id: item.id,
        publishedAt: item.published_at,
        observedAt: item.observed_at ?? null,
        label: `媒体 · ${item.title}`,
        contentHash: item.content_sha256 ?? null,
        reviewStatus: "pending_review",
        reviewedAt: null,
        userRatingStars: item.credibility_stars,
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
  const chosenVersionId = chosenVersion?.id ?? "";
  useEffect(() => {
    if (!revisionOpen || !chosenVersionId) {
      setRevisionParentDetail(null);
      return;
    }
    const controller = new AbortController();
    setRevisionParentDetail({ kind: "loading" });
    getV2ForecastVersion(chosenVersionId, controller.signal)
      .then((detail) => { if (!controller.signal.aborted) setRevisionParentDetail({ kind: "ready", data: detail }); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setRevisionParentDetail({ kind: "error", message: errorMessage(error, "无法读取基准版本的已用材料。") }); });
    return () => controller.abort();
  }, [revisionOpen, chosenVersionId]);

  const matchingRevisionParent = revisionParentDetail?.kind === "ready" && revisionParentDetail.data.id === chosenVersionId
    ? revisionParentDetail.data
    : null;
  useEffect(() => {
    if (!revisionOpen || !chosenVersionId) {
      setRevisionMaterialMetadata(null);
      setRevisionMaterialMetadataError(null);
      setRevisionMaterialMetadataLoading(false);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();
    setRevisionMaterialMetadataLoading(true);
    setRevisionMaterialMetadataError(null);

    async function refreshAnalysisMetadata() {
      try {
        const items = await getAllMaterialItems(symbol, controller.signal);
        if (!cancelled) {
          setRevisionMaterialMetadata({ parentVersionId: chosenVersionId, items });
          setRevisionMaterialMetadataError(null);
        }
      } catch (error) {
        if (!cancelled && !controller.signal.aborted) setRevisionMaterialMetadataError(errorMessage(error, "暂时无法读取材料分析状态。"));
      } finally {
        if (!cancelled) {
          setRevisionMaterialMetadataLoading(false);
          timer = window.setTimeout(() => { void refreshAnalysisMetadata(); }, 10_000);
        }
      }
    }

    void refreshAnalysisMetadata();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      controller.abort();
    };
  }, [revisionOpen, symbol, chosenVersionId]);

  const revisionSourceOptions = useMemo<Array<SourceOption & { revisionType: "new" | "updated" | "reanalyzed" }>>(() => {
    if (!matchingRevisionParent) return [];
    const parentSources = matchingRevisionParent.evidence_version_manifest;
    const priorBySource = new Map<string, Record<string, unknown>>();
    parentSources.forEach((item) => {
      const sourceType = typeof item.source_type === "string" ? item.source_type : null;
      const sourceId = typeof item.source_id === "string" ? item.source_id : null;
      if (sourceType && sourceId) priorBySource.set(`${sourceType}:${sourceId}`, item);
    });
    const priorContentHashes = new Set(parentSources.flatMap((item) => typeof item.content_sha256 === "string" ? [item.content_sha256] : []));
    const parentDecisionAt = Date.parse(matchingRevisionParent.decision_at);
    const analysisItems = revisionMaterialMetadata?.parentVersionId === chosenVersionId ? revisionMaterialMetadata.items : [];
    const analysesBySource = new Map(analysisItems.map((item) => [`${item.source_type}:${item.source_id}`, item]));
    const eligible: Array<SourceOption & { revisionType: "new" | "updated" | "reanalyzed" }> = [];
    for (const source of sourceOptions) {
      const publishedAt = source.publishedAt;
      const observedAt = source.observedAt;
      if (!source.contentHash || typeof publishedAt !== "string" || typeof observedAt !== "string"
        || !isTimezoneAwareTimestamp(publishedAt) || !isTimezoneAwareTimestamp(observedAt)) continue;
      const key = `${source.source_type}:${source.source_id}`;
      const previous = priorBySource.get(key);
      if (!previous) {
        const newlyAvailable = Date.parse(publishedAt) > parentDecisionAt || Date.parse(observedAt) > parentDecisionAt;
        if (newlyAvailable && !priorContentHashes.has(source.contentHash)) eligible.push({ ...source, revisionType: "new" });
        continue;
      }

      const previousContentHash = typeof previous.content_sha256 === "string"
        ? previous.content_sha256
        : typeof previous.content_hash === "string" ? previous.content_hash : null;
      const contentChanged = previousContentHash !== null && source.contentHash !== previousContentHash;
      const reviewChanged = typeof previous.review_status === "string" && source.reviewStatus !== null && source.reviewStatus !== previous.review_status;
      const reviewedAfterParent = source.reviewedAt !== null && Date.parse(source.reviewedAt) > parentDecisionAt;
      const priorRating = typeof previous.user_rating_stars === "number" ? previous.user_rating_stars : null;
      const ratingChanged = source.userRatingStars !== null && source.userRatingStars !== priorRating;
      const analysis = analysesBySource.get(key);
      const previousEventId = typeof previous.event_version_id === "string"
        ? previous.event_version_id
        : typeof previous.id === "string" ? previous.id : null;
      const analysisCompletedAfterParent = Boolean(
        analysis?.latest_analysis_id
        && typeof analysis.latest_analysis_created_at === "string"
        && Date.parse(analysis.latest_analysis_created_at) > parentDecisionAt
        && analysis.latest_analysis_evidence_version_id === previousEventId,
      );
      if (contentChanged || reviewChanged || reviewedAfterParent || ratingChanged) eligible.push({ ...source, revisionType: "updated" });
      else if (analysisCompletedAfterParent) eligible.push({ ...source, revisionType: "reanalyzed" });
    }
    return eligible;
  }, [sourceOptions, matchingRevisionParent, revisionMaterialMetadata, chosenVersionId]);
  const chosenSource = revisionSourceOptions.find((source) => `${source.source_type}:${source.source_id}` === sourceKey);

  async function createForecast() {
    setActionError(null);
    try {
      const job = await createV2ForecastJob(symbol);
      setActiveJob(job);
      saveJob(symbol, job);
      void refresh();
    } catch (error) {
      setActionError(errorMessage(error, "无法创建预测任务。"));
    }
  }

  async function createRevision() {
    if (!chosenVersion || !matchingRevisionParent || !chosenSource) return;
    setActionError(null);
    try {
      const job = await createV2ManualRevisionJob(chosenVersion.id, [{
        source_type: chosenSource.source_type,
        source_id: chosenSource.source_id,
      }]);
      setActiveJob(job);
      saveJob(symbol, job);
      setRevisionOpen(false);
      setSourceKey("");
      void refresh();
    } catch (error) {
      setActionError(errorMessage(error, "无法创建手动修订任务。"));
    }
  }

  const modelStatus = selectedRoot?.model_status ?? (workspace.kind === "ready" ? workspace.data.joint_model_status : "unavailable");
  const selectedCurrentVersion = versionDetails?.kind === "ready" && versionDetails.data.current.id === selectedVersionId ? versionDetails.data.current : null;
  const selectedCalibration = selectedCurrentVersion?.model_manifest.local_calibration as Record<string, unknown> | undefined;
  const rootIsRevisable = selectedRoot?.expired === false;
  const revisionDisabledReason = selectedRoot?.expired === true ? t("该预测的目标已到期，只能查看历史版本。") : selectedRoot?.expired === null ? t("该预测的目标合同无效，只能查看历史版本。") : null;
  const canCreateRevision = Boolean(chosenVersion && matchingRevisionParent && chosenSource && !isActiveJob(activeJob));

  const hasNoRevisionRoot = roots.kind === "ready" && roots.data.roots.length === 0;

  return <section className={`v2-desk v2-desk-${mode}`} aria-labelledby="v2-desk-title">
    <header className="v2-desk-heading">
      <div>
        <p className="eyebrow">{t(mode === "forecast" ? "当前判断" : "版本与来源")}</p>
        <h2 id="v2-desk-title">{t(mode === "forecast" ? "预测工作台" : "预测版本与修订")}</h2>
        <p>{t(mode === "forecast" ? "创建独立预测，查看当前版本、固定目标、行情边界和研究简报。" : "查看同一固定目标下的版本时间线与来源变化；新增合规材料后，可创建保留历史的新版本。")}</p>
      </div>
      {mode === "forecast" && <button className="primary-action" type="button" onClick={createForecast} disabled={isActiveJob(activeJob) || workspace.kind === "loading"}>
        {isActiveJob(activeJob) ? t("任务处理中…") : t("创建预测任务")}
      </button>}
    </header>

    {mode === "revisions" && hasNoRevisionRoot
      ? <section className="v2-revision-empty" aria-label={t("尚无可修订的预测")}>
        <p className="section-label">{<Tx text={"还没有版本历史"} />}</p>
        <h3>{<Tx text={"先创建一项预测"} />}</h3>
        <p>{<Tx text={"发布首个预测版本后，这里会显示版本时间线、来源差异和修订入口。"} />}</p>
        <button className="primary-action" type="button" onClick={onOpenForecast}>{<Tx text={"前往预测工作台"} />}</button>
      </section>
      : <>
        <RootPicker roots={roots} selectedRootId={selectedRootId} onSelect={setSelectedRootId} />
        <VersionPicker timeline={timeline} selectedVersionId={selectedVersionId} onSelect={setSelectedVersionId} />
      </>}

    {mode === "forecast" && <>
      <p className="v2-workflow-note">{<Tx text={"创建预测后，系统会冻结本次行情和材料，并自动为入选材料请求或生成单份 AI 分析，再用成功的分析生成研究简报；启用 Jev 时，只有通过引用校验的简报才会交给 Jev。无需先在“材料库”手动分析。若分析失败或证据不足，系统不会跳过分析直接把原文交给 Jev。"} />}</p>
      <div className="v2-status-grid">
        <article><span>{<Tx text={"预测判断"} />}</span><strong>{selectedRoot ? modelStatusText(modelStatus) : t("尚无预测结果")}</strong><small>{selectedRoot ? modelStatus === "experimental_jev" ? selectedCalibration?.status === "active" ? t("Jev 输出已通过本地时间外校准；原始值可展开查看") : t("当前使用 Jev 原始概率；本地校准器仍在积累到期样本") : modelStatus === "research_only" ? t("当前只提供材料研究简报，没有数值概率") : t("请将模型结果视为研究信息") : t("创建一项研究后，这里会显示对应结果。")}</small></article>
        <MonitorCard monitor={monitor} />
        <article><span>{<Tx text={"待处理任务"} />}</span><strong>{workspace.kind === "ready" ? workspace.data.pending_job_count : "—"}</strong><small>{<Tx text={"排队只表示服务已接收，不表示预测已生成。"} />}</small></article>
      </div>
      <MonitorDetail monitor={monitor} />
    </>}

    {workspace.kind === "error" && <p className="v2-notice error" role="alert">{workspace.message}</p>}
    {activeJob && <JobStatus job={activeJob} />}
    {actionError && <p className="v2-notice error" role="alert">{actionError}</p>}

    {mode === "forecast" && <>
      <ForecastSummary root={selectedRoot} roots={roots} detail={versionDetails} />
      <ForecastPriceContextChart symbol={symbol} history={priceHistory} detail={versionDetails} showHistoricalOnly={roots.kind === "ready" && !selectedRoot} />
      {versionDetails?.kind === "ready" && versionDetails.data.current.research_brief && <ResearchBriefView
        brief={versionDetails.data.current.research_brief}
        versionId={versionDetails.data.current.id}
        probabilities={versionDetails.data.current.model_status === "experimental_jev" ? versionDetails.data.current.decision_probabilities ?? null : null}
        modelManifest={versionDetails.data.current.model_manifest}
        evidenceManifest={versionDetails.data.current.evidence_version_manifest}
        onOpenAnalysis={openResearchMaterial}
      />}
      <EvaluationReadout
        evaluations={evaluations}
        selectedRootId={selectedRootId}
        selectedVersionId={selectedVersionId}
        selectedVersionDetail={versionDetails?.kind === "ready" && versionDetails.data.current.id === selectedVersionId ? versionDetails.data.current : null}
      />
    </>}

    {mode === "revisions" && !hasNoRevisionRoot && <>
      <ForecastSummary root={selectedRoot} roots={roots} detail={versionDetails} />
      <RevisionComparison timeline={timeline} detail={versionDetails} />
      <EvidenceEvents timeline={timeline} detail={versionDetails} selectedVersionId={selectedVersionId} onSelect={setSelectedVersionId} />

      <section className="v2-revision-entry" aria-labelledby="v2-revision-title">
        <div>
          <p className="section-label">{<Tx text={"人工核验入口"} />}</p>
          <h3 id="v2-revision-title">{<Tx text={"用一条新材料发起修订"} />}</h3>
          <p>{<Tx text={"系统会在服务端检查材料的发布时点、观察时点、来源状态变化和固定目标合同；未来材料仍会被拒绝。提交成功只代表已入队，原版本保持不变。"} />}</p>
        </div>
        <button className="secondary-action" type="button" onClick={() => setRevisionOpen((open) => !open)} disabled={!versions.length || !rootIsRevisable || isActiveJob(activeJob)}>
          {revisionOpen ? t("收起修订") : t("选择版本与材料")}
        </button>
      </section>

      {revisionDisabledReason && <p className="v2-root-warning" role="status">{revisionDisabledReason}</p>}

      {revisionOpen && <section className="v2-revision-form" aria-label={t("创建手动修订")}>
        <label>{<Tx text={"修订哪一个预测版本"} />}<select value={parentVersionId} onChange={(event) => { setParentVersionId(event.target.value); setSourceKey(""); }}>
            <option value="">{<Tx text={"默认最新版本"} />}</option>
            {versions.map((version) => <option value={version.id} key={version.id}>{t("第")} {version.version_no} {t("版")} · {formatTime(version.decision_at)}</option>)}
          </select>
        </label>
        <label>{<Tx text={"用于本次修订的材料"} />}<select value={sourceKey} disabled={!matchingRevisionParent || revisionSourceOptions.length === 0} onChange={(event) => setSourceKey(event.target.value)}>
            <option value="">{t(matchingRevisionParent ? "选择新材料、来源更新或补齐分析的旧材料" : revisionParentDetail?.kind === "error" ? "基准版本材料读取失败" : "正在核对基准版本材料…")}</option>
            {revisionSourceOptions.map((source) => <option value={`${source.source_type}:${source.source_id}`} key={`${source.source_type}:${source.source_id}`}>{source.label} · {t(source.revisionType === "new" ? "新材料" : source.revisionType === "reanalyzed" ? "新成功分析" : "来源状态更新")}</option>)}
          </select>
        </label>
        <div className="v2-form-footer">
          {revisionParentDetail?.kind === "loading" && <p>{<Tx text={"正在核对所选版本已经使用的材料…"} />}</p>}
          {revisionParentDetail?.kind === "error" && <p role="alert">{t("无法读取基准版本材料清单：")}{revisionParentDetail.message}</p>}
          {revisionMaterialMetadataLoading && <p>{<Tx text={"正在同步材料分析状态；打开此表单期间会自动刷新。"} />}</p>}
          {revisionMaterialMetadataError && <p role="status">{t("分析状态读取失败，系统会继续重试：")}{revisionMaterialMetadataError}</p>}
          {matchingRevisionParent && uploaded.kind === "loading" && <p>{<Tx text={"正在读取已上传媒体材料…"} />}</p>}
          {matchingRevisionParent && uploaded.kind === "error" && <p role="status">{t("媒体材料列表暂不可用：")}{uploaded.message} {t("SEC 材料仍可继续检查。")}</p>}
          {matchingRevisionParent && uploaded.kind !== "loading" && !revisionMaterialMetadataLoading && revisionSourceOptions.length === 0 && <p>{<Tx text={"当前没有可用于修订的材料。冻结但未进入简报、或分析失败的来源，在材料库补做分析并成功后会自动出现在这里；新发布材料和状态更新也会列出。"} />}</p>}
          <button className="primary-action" type="button" onClick={createRevision} disabled={!canCreateRevision || !rootIsRevisable}>{<Tx text={"创建人工修订任务"} />}</button>
        </div>
      </section>}
    </>}
  </section>;
}

function ForecastPriceContextChart({ symbol, history, detail, showHistoricalOnly }: { symbol: string; history?: PriceHistory | null; detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null; showHistoricalOnly: boolean }) {
  const { locale, t } = useLocale();
  const current = detail?.kind === "ready" ? detail.data.current : null;
  const cutoffDate = current?.market_cutoff_at.slice(0, 10);
  const fixedTargetDate = current ? targetDate(current) : null;
  const candles = useMemo(() => {
    const all = (history?.candles ?? []).filter(isUsablePriceCandle).sort((left, right) => left.trading_date.localeCompare(right.trading_date));
    const anchorIndexes = [cutoffDate, fixedTargetDate].map((date) => date ? all.findIndex((candle) => candle.trading_date === date) : -1).filter((index) => index >= 0);
    if (!anchorIndexes.length) return all.slice(-100);
    const start = Math.max(0, Math.min(...anchorIndexes) - 18);
    const end = Math.min(all.length, Math.max(...anchorIndexes) + 34);
    return all.slice(start, Math.min(end, start + 100));
  }, [history, cutoffDate, fixedTargetDate]);
  const [activeDate, setActiveDate] = useState<string | null>(candles.at(-1)?.trading_date ?? null);
  useEffect(() => setActiveDate(candles.at(-1)?.trading_date ?? null), [symbol, candles]);
  if (!current && !showHistoricalOnly) return null;
  if (!current && !candles.length) return <section className="price-panel price-panel-empty"><div><span className="section-label">{<Tx text={"历史行情"} />}</span><h2>{<Tx text={"暂时没有可展示的历史行情"} />}</h2></div><p>{<Tx text={"本地没有可绘制的 K 线。"} />}</p></section>;
  if (!current) {
    const active = candles.find((candle) => candle.trading_date === activeDate) ?? candles.at(-1)!;
    return <section className="price-panel" aria-label={`${symbol} 历史行情`}>
      <div className="price-panel-heading"><div><span className="section-label">{<Tx text={"历史行情"} />}</span><h2>{symbol} · {t("已保存日线")}</h2></div><span className="tag">{history?.source ?? t("本地行情")}</span></div>
      <p className="price-intro">{<Tx text={"此处仅展示本地保存的历史行情，不包含预测概率、目标日或版本时间标记。"} />}</p>
      <CandlestickChart candles={candles} symbol={symbol} activeDate={active.trading_date} onInspect={setActiveDate} />
      <div className="candle-detail" aria-live="polite"><strong>{active.trading_date}</strong><span>{t("开盘")} {formatPrice(active.open)} · {t("最高")} {formatPrice(active.high)} · {t("最低")} {formatPrice(active.low)} · {t("收盘")} {formatPrice(active.close)}</span>{active.benchmark_close !== null && active.benchmark_close !== undefined && <span>SPY {t("收盘")} {formatPrice(active.benchmark_close)}</span>}</div>
    </section>;
  }
  if (!candles.length) return <section className="price-panel price-panel-empty" aria-label={t("预测行情定位")}><div><span className="section-label">{<Tx text={"固定目标行情"} />}</span><h2>{<Tx text={"暂时没有已保存的 K 线"} />}</h2></div><p>{<Tx text={"预测版本的决策时点与目标日已保存，但本地没有可绘制的 OHLC 行情。"} />}</p><p>{t("行情截止")} {formatTime(current.market_cutoff_at)} · {t("固定目标日")} {targetDate(current) ?? t("未记录")}</p></section>;
  const active = candles.find((candle) => candle.trading_date === activeDate) ?? candles.at(-1)!;
  return <section className="price-panel v3-price-context" aria-label={t("所选预测的固定目标和行情时间边界")}>
    <div className="price-panel-heading"><div><span className="section-label">{<Tx text={"所选预测行情"} />}</span><h2>{symbol} · {t("固定目标与决策行情截止")}</h2></div><span className="tag">{history?.source ?? t("已存行情")}</span></div>
    <p className="price-intro">{<Tx text={"图表只显示本地保存的 K 线。橙线标记该版本固定目标日，实线标记行情截止日；不会把新版判断伪装成历史概率或蜡烛。"} />}</p>
    <CandlestickChart candles={candles} symbol={symbol} cutoffDate={cutoffDate} targetEndDate={fixedTargetDate ?? undefined} activeDate={active.trading_date} onInspect={setActiveDate} />
    <div className="candle-detail" aria-live="polite"><strong>{active.trading_date}</strong><span>{t("开盘")} {formatPrice(active.open)} · {t("最高")} {formatPrice(active.high)} · {t("最低")} {formatPrice(active.low)} · {t("收盘")} {formatPrice(active.close)}</span>{active.benchmark_close !== null && active.benchmark_close !== undefined && <span>SPY {t("收盘")} {formatPrice(active.benchmark_close)}</span>}</div>
    <p className="fine-print price-fine-print">{locale === "en-US" ? `${t("决策时点")} ${formatTime(current.decision_at)} · ${t("行情截止")} ${formatTime(current.market_cutoff_at)} · ${t("固定目标日")} ${fixedTargetDate ?? t("未记录")}. ${t("图表仅定位合同时间边界，不计算未来收益或预测准确率。")}` : `${t("决策时点")} ${formatTime(current.decision_at)} · ${t("行情截止")} ${formatTime(current.market_cutoff_at)} · ${t("固定目标日")} ${fixedTargetDate ?? t("未记录")}。${t("图表仅定位合同时间边界，不计算未来收益或预测准确率。")}`}</p>
  </section>;
}

function targetDate(version: V2VersionDetail): string | null {
  const value = version.target_contract.target_end_date;
  return typeof value === "string" ? value : null;
}

function isUsablePriceCandle(candle: PriceCandle) {
  return Boolean(candle.trading_date) && [candle.open, candle.high, candle.low, candle.close].every((value) => Number.isFinite(value) && value > 0)
    && candle.high >= Math.max(candle.open, candle.close) && candle.low <= Math.min(candle.open, candle.close);
}

function formatPrice(value: number) { return `$${value.toFixed(2)}`; }

function triggerTypeText(value: unknown) {
  if (value === "manual_revision") return translatePhrase("人工发起修订");
  if (value === "automatic_monitor") return translatePhrase("监控发现材料");
  if (value === "new_forecast") return translatePhrase("新建预测");
  return translatePhrase("预测更新");
}

function sourceTypeText(value: unknown) {
  if (value === "official_filing") return translatePhrase("官方申报");
  if (value === "uploaded_media") return translatePhrase("上传材料");
  return translatePhrase("研究来源");
}

function discoveryKindText(value: unknown) {
  if (value === "official_scan") return translatePhrase("官方资料扫描发现");
  if (value === "manual_selection") return translatePhrase("人工选择");
  if (value === "uploaded") return translatePhrase("上传材料");
  return translatePhrase("来源记录");
}

function JobStatus({ job }: { job: V2ForecastJob }) {
  const { t } = useLocale();
  const error = job.error?.type;
  return <section className={`v2-job-status is-${job.status}`} aria-live="polite">
    <div><span className="section-label">{t("当前任务")} · {t(job.kind === "manual_revision" ? "人工修订" : "新预测")}</span><strong>{jobStatusText(job)}</strong><small>{t("阶段")}：{translatePhrase(job.current_stage)} · {t("创建于")} {formatTime(job.created_at)}</small></div>
    <div className="v2-job-result">{job.result_version_id ? <>{<Tx text={"结果版本"} />}<br /><code>{job.result_version_id.slice(0, 8)}</code></> : error ? <>{<Tx text={"安全错误码"} />}<br /><code>{error}</code></> : <>{<Tx text={"任务编号"} />}<br /><code>{job.id.slice(0, 8)}</code></>}</div>
  </section>;
}

function MonitorCard({ monitor }: { monitor: ResourceState<V2MonitorStatus> }) {
  const { t } = useLocale();
  if (monitor.kind === "loading") return <article><span>{<Tx text={"自动监控"} />}</span><strong>{<Tx text={"正在读取记录"} />}</strong><small>{<Tx text={"尚未根据定时器配置判断健康。"} />}</small></article>;
  if (monitor.kind === "error") return <article><span>{<Tx text={"自动监控"} />}</span><strong>{<Tx text={"状态不可读取"} />}</strong><small>{<Tx text={"无法确认监控是否正常运行。"} />}</small></article>;
  const { health, last_run: lastRun } = monitor.data;
  return <article className={`v2-monitor-card is-${health}`}><span>{<Tx text={"自动监控"} />}</span><strong>{monitorHealthText(health)}</strong><small>{lastRun ? `${t("最近扫描")} ${formatTime(lastRun.completed_at ?? lastRun.started_at)}` : t("还没有持久化运行记录")}</small></article>;
}

function MonitorDetail({ monitor }: { monitor: ResourceState<V2MonitorStatus> }) {
  const { locale, t } = useLocale();
  if (monitor.kind === "loading") return null;
  if (monitor.kind === "error") return <p className="v2-notice error" role="alert">{monitor.message}</p>;
  const { health, last_run: lastRun, last_success: lastSuccess, stale_after_seconds: staleAfter } = monitor.data;
  if (!lastRun) return <section className="v2-monitor-detail"><p className="section-label">{<Tx text={"自动监控记录"} />}</p><h3>{<Tx text={"没有运行记录"} />}</h3><p>{<Tx text={"没有实际扫描或成功记录时，页面不会把每小时计划显示为健康。"} />}</p></section>;
  const symbols = Object.entries(lastRun.per_symbol_results);
  return <section className={`v2-monitor-detail is-${health}`} aria-label={t("自动监控状态")}>
    <div><p className="section-label">{<Tx text={"自动监控记录"} />}</p><h3>{monitorHealthText(health)} · {t("最近运行")} {monitorRunStatusText(lastRun.status)}</h3><p>{locale === "en-US" ? `${t("检查开始")} ${formatTime(lastRun.started_at)}; ${t("完成")} ${formatTime(lastRun.completed_at)}; ${t("下一次预期")} ${formatTime(lastRun.next_due_at)}.` : `${t("检查开始")} ${formatTime(lastRun.started_at)}；${t("完成")} ${formatTime(lastRun.completed_at)}；${t("下一次预期")} ${formatTime(lastRun.next_due_at)}。`}</p><small>{locale === "en-US" ? `${t("延迟阈值为")} ${Math.round(staleAfter / 3600)} ${t("小时")}. ${t("最近扫描成功只代表一条持久化记录；定时健康需观察两个实际周期。")}` : `${t("延迟阈值为")} ${Math.round(staleAfter / 3600)} ${t("小时")}。${t("最近扫描成功只代表一条持久化记录；定时健康需观察两个实际周期。")}`}</small></div>
    <div className="v2-monitor-summary"><span>{<Tx text={"最近全成功"} />}</span><strong>{lastSuccess ? formatTime(lastSuccess.completed_at) : t("从未记录")}</strong>{lastRun.retry_reason && <small>{lastRun.retry_reason}</small>}</div>
    <div className="v2-monitor-symbols">{symbols.length ? symbols.map(([ticker, result]) => <article key={ticker} className={`is-${result.status}`}><strong>{ticker}</strong><span>{monitorSymbolStatusText(result.status)}</span><small>{result.error ?? `${t("发现")} ${result.discovered_count ?? 0} · ${t("新增")} ${result.created_count ?? 0} · ${t("入队")} ${result.queued_count ?? 0}`}</small></article>) : <p>{<Tx text={"本次运行没有返回逐股结果。"} />}</p>}</div>
  </section>;
}

function EvaluationReadout({ evaluations, selectedRootId, selectedVersionId, selectedVersionDetail }: { evaluations: ResourceState<V2EvaluationResponse>; selectedRootId: string; selectedVersionId: string; selectedVersionDetail: V2VersionDetail | null }) {
  const { locale, t } = useLocale();
  if (evaluations.kind === "loading") return <section className="v2-evaluation"><p>{<Tx text={"正在读取所选预测的到期评估…"} />}</p></section>;
  if (evaluations.kind === "error") return <section className="v2-evaluation"><p className="v2-notice error" role="alert">{evaluations.message}</p></section>;
  if (selectedVersionDetail?.model_status === "experimental_jev") {
    const jevCohort = jevEvaluationCohort(evaluations.data, selectedVersionDetail);
    if (!jevCohort) return <section className="v2-evaluation"><p className="section-label">{<Tx text={"Jev 到期评估"} />}</p><h3>{t(evaluations.data.model_cohorts ? "尚无匹配的模型分组" : "模型分组评估尚不可用")}</h3><p>{t(evaluations.data.model_cohorts ? "当前评估数据没有与此版本的时间模式、Jev 服务方、实际模型和问题版本完全匹配的 cohort；不会使用混合旧分组代替。" : "API 尚未提供按模型隔离的评估分组；为避免混淆样本分母，不使用旧的混合分组代替。")}</p></section>;
    const root = jevCohort.roots.find((item) => item.root_id === selectedRootId);
    const representative = root?.versions[0];
    const representsSelected = representative?.id === selectedVersionId;
    const evaluation = representsSelected ? representative?.latest_evaluation ?? null : null;
    const state = evaluationStateText(evaluation?.status, root?.target_end_date ?? null);
    const scoreParts = evaluation?.status === "succeeded" ? [
      evaluation.brier_score !== null ? `Brier ${scoreText(evaluation.brier_score)}` : null,
      evaluation.log_loss !== null ? `Log loss ${scoreText(evaluation.log_loss)}` : null,
      evaluation.direction_correct !== null ? `${t("方向")}${t(evaluation.direction_correct ? "正确" : "错误")}` : null,
    ].filter((part): part is string => part !== null) : [];
    return <section className="v2-evaluation" aria-label={t("所选 Jev 预测的分组到期评估")}>
      <div><p className="section-label">{<Tx text={"Jev 到期评估 · 同一模型组"} />}</p><h3>{representsSelected ? state : t("显示该独立预测的最新代表版本")}</h3><p>{t("模型")} {jevCohort.actual_model} · {t("问题")} {jevCohort.question_version ?? t("未记录")} · {root?.target_end_date ?? t("目标日未记录")}</p></div>
      <div className="v2-evaluation-sample"><span>{<Tx text={"独立预测样本"} />}</span><strong>{t("标签")} {jevCohort.sample.labelled_root_count} · {t("评分")} {jevCohort.sample.scored_root_count} / {jevCohort.sample.root_denominator}</strong><small>{locale === "en-US" ? `${cohortStatusText(jevCohort.status).trim()}; ${t("按服务方、模型、问题版本和时间模式分组，每项独立预测只计一个代表版本。")}` : `${cohortStatusText(jevCohort.status)}；${t("按服务方、模型、问题版本和时间模式分组，每项独立预测只计一个代表版本。")}`}</small></div>
      {evaluation?.status === "succeeded" && <div className="v2-evaluation-result"><span>{<Tx text={"真实标签"} />}</span><strong>{labelText(evaluation.actual_label)}</strong><small>{evaluation.actual_target_close === null ? t("目标收盘价未记录") : `${t("目标收盘")} ${evaluation.actual_target_close}`}{evaluation.label_available_at ? ` · ${formatTime(evaluation.label_available_at)} ${t("可用")}` : ""}</small><p>{t("Jev 实验性判断评分（模型未校准）：")} {scoreParts.length ? scoreParts.join(" · ") : t("无可用数值评分")}</p></div>}
      {!representsSelected && <p className="v2-evaluation-note">{<Tx text={"每项独立预测只按最高版本号统计；当前选中的旧版本没有单独计入这组分数。"} />}</p>}
      {representative && <p className="v2-evaluation-note">{t("代表版本：")} {t("第")} {representative.version_no} {t("版")} · {t("分组")} {jevCohort.provider} / {jevCohort.actual_model} / {jevCohort.question_version ?? "—"} · {t("选择规则：")} {evaluations.data.model_cohort_selection_rule ?? t("每项独立预测按最高版本号选一个代表版本")}</p>}
    </section>;
  }
  const match = evaluationRootFor(evaluations.data, selectedRootId);
  if (!match) return <section className="v2-evaluation"><p className="section-label">{<Tx text={"到期评估"} />}</p><h3>{<Tx text={"所选预测尚无评估记录"} />}</h3><p>{<Tx text={"评估区只读已持久化结果；页面不会拉行情或触发计算。"} />}</p></section>;
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
      evaluation.direction_correct !== null ? `${t("方向")}${t(evaluation.direction_correct ? "正确" : "错误")}` : null,
    ].filter((part): part is string => part !== null)
    : [];
  const numericScoring = (selectedVersion.model_status === "experimental_joint" || selectedVersion.model_status === "baseline_only") && scoreParts.length > 0;
  return <section className="v2-evaluation" aria-label={t("所选预测的到期评估")}>
    <div><p className="section-label">{<Tx text={"到期评估 · 所选预测"} />}</p><h3>{state}</h3><p>{t("目标日")} {root.target_end_date ?? t("未记录")} · {t("第")} {selectedVersion.version_no} {t("版")} · {t(selectedVersion.time_mode === "observed" ? "前向观察" : selectedVersion.time_mode === "historical_research" ? "历史研究" : "时间模式未知")}</p></div>
    <div className="v2-evaluation-sample"><span>{<Tx text={"预测样本"} />}</span><strong>{t("标签")} {cohort.sample.labelled_root_count} · {t("评分")} {cohort.sample.scored_root_count} / {cohort.sample.root_denominator}</strong><small>{locale === "en-US" ? `${cohortStatusText(cohort.status).trim()}; ${t("真实标签与可评分预测分开统计，修订版本不重复计入预测样本。")}` : `${cohortStatusText(cohort.status)}；${t("真实标签与可评分预测分开统计，修订版本不重复计入预测样本。")}`}</small></div>
    {evaluation?.status === "succeeded" && <div className="v2-evaluation-result"><span>{<Tx text={"真实标签"} />}</span><strong>{labelText(evaluation.actual_label)}</strong><small>{evaluation.actual_target_close === null ? t("目标收盘价未记录") : `${t("目标收盘")} ${evaluation.actual_target_close}`}{evaluation.label_available_at ? ` · ${formatTime(evaluation.label_available_at)} ${t("可用")}` : ""}</small>{numericScoring ? <p>{t(selectedVersion.model_status === "baseline_only" ? "行情基线模型评分：" : "联合研究模型评分：")}{scoreParts.join(" · ")}</p> : <p>{t(selectedVersion.model_status === "research_only" ? "已到期并记录真实标签，但该版本没有数值概率可评分。" : "真实标签已记录，但当前没有可显示的数值评分。")}</p>}</div>}
    {evaluation && evaluation.status !== "succeeded" && <p className="v2-evaluation-note">{t(evaluation.status === "blocked_price" ? "目标价格尚不可用，保持待评估。" : evaluation.status === "failed" ? "最近一次评估失败，未生成替代分数。" : "评估记录已创建，等待目标标签成熟。")}</p>}
  </section>;
}

function evaluationRootFor(response: V2EvaluationResponse, rootId: string): { cohort: V2EvaluationCohort; root: V2EvaluationRoot } | null {
  for (const cohort of Object.values(response.cohorts)) {
    const root = cohort.roots.find((item) => item.root_id === rootId);
    if (root) return { cohort, root };
  }
  return null;
}

function jevEvaluationCohort(response: V2EvaluationResponse, version: V2VersionDetail): V2ModelEvaluationCohort | null {
  const timeMode = declaredTimeMode(version);
  const manifest = version.model_manifest;
  const provider = asRecord(manifest.decision_provider);
  const providerName = provider.provider;
  const actualModel = provider.actual_model;
  const questionVersion = provider.question_version;
  if (!timeMode || typeof providerName !== "string" || typeof actualModel !== "string" || typeof questionVersion !== "string") return null;
  return response.model_cohorts?.find((cohort) => cohort.time_mode === timeMode
    && cohort.model_status === version.model_status
    && cohort.provider === providerName
    && cohort.actual_model === actualModel
    && cohort.question_version === questionVersion) ?? null;
}

function declaredTimeMode(version: V2VersionDetail): V2ModelEvaluationCohort["time_mode"] {
  for (const metadata of [version.research_report, version.model_manifest]) {
    const value = asRecord(metadata).time_mode;
    if (value === "observed" || value === "historical_research" || value === "unknown") return value;
  }
  return "unknown";
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function evaluationStateText(status: "pending" | "succeeded" | "blocked_price" | "failed" | undefined, targetEndDate: string | null) {
  if (status === "succeeded") return translatePhrase("评估已记录");
  if (status === "blocked_price") return translatePhrase("等待目标价格");
  if (status === "failed") return translatePhrase("评估失败");
  if (targetEndDate && targetEndDate > new Date().toISOString().slice(0, 10)) return translatePhrase("目标未到期 · 待评估");
  return translatePhrase("待评估");
}

function cohortStatusText(status: V2EvaluationCohort["status"]) {
  if (status === "available") return translatePhrase("样本达到最低门槛");
  if (status === "insufficient_samples") return translatePhrase("样本不足");
  return translatePhrase("尚无可评分预测");
}

function labelText(label: "bearish" | "neutral" | "bullish" | null) {
  if (label === "bearish") return translatePhrase("下跌");
  if (label === "neutral") return translatePhrase("持平");
  if (label === "bullish") return translatePhrase("上涨");
  return translatePhrase("未记录");
}

function scoreText(value: number | null) {
  return value === null ? "—" : value.toFixed(4);
}

function monitorHealthText(health: V2MonitorStatus["health"]) {
  switch (health) {
    case "healthy": return translatePhrase("最近扫描成功");
    case "delayed": return translatePhrase("运行延迟");
    case "degraded": return translatePhrase("部分失败或降级");
    default: return translatePhrase("没有运行记录");
  }
}

function monitorRunStatusText(status: "running" | "succeeded" | "partial" | "failed") {
  if (status === "succeeded") return translatePhrase("成功");
  if (status === "partial") return translatePhrase("部分失败");
  if (status === "failed") return translatePhrase("失败");
  return translatePhrase("进行中");
}

function monitorSymbolStatusText(status: "succeeded" | "incomplete" | "failed") {
  if (status === "succeeded") return translatePhrase("成功");
  if (status === "incomplete") return translatePhrase("未完整扫描");
  return translatePhrase("失败");
}

function RootPicker({ roots, selectedRootId, onSelect }: { roots: ResourceState<V2ForecastRoots>; selectedRootId: string; onSelect: (id: string) => void }) {
  const { t } = useLocale();
  if (roots.kind === "loading") return <p className="v2-root-loading">{<Tx text={"正在读取可修订的预测记录…"} />}</p>;
  if (roots.kind === "error") return <p className="v2-notice error" role="alert">{roots.message}</p>;
  if (!roots.data.roots.length) return <p className="v2-root-loading">{<Tx text={"尚无预测记录。任务成功发布后会出现在这里。"} />}</p>;
  return <label className="v2-root-picker">{<Tx text={"选择预测链（固定目标）"} />}<select value={selectedRootId} onChange={(event) => onSelect(event.target.value)}>
      {roots.data.roots.map((root) => <option value={root.id} key={root.id}>{formatTime(root.decision_at)} · {t("目标")} {root.target_end_date} · {t("第")} {root.latest_version_no} {t("版")}{root.expired === true ? ` · ${t("已到期")}` : root.expired === null ? ` · ${t("目标日期无效")}` : ""}</option>)}
    </select>
  </label>;
}

function VersionPicker({ timeline, selectedVersionId, onSelect }: {
  timeline: ResourceState<V2Timeline> | null;
  selectedVersionId: string;
  onSelect: (id: string) => void;
}) {
  const { t } = useLocale();
  if (timeline?.kind === "loading") return <p className="v2-root-loading">{<Tx text={"正在读取此预测链的版本结果…"} />}</p>;
  if (timeline?.kind === "error") return <p className="v2-notice error" role="alert">{t("无法读取版本结果：")}{timeline.message}</p>;
  const versions = timeline?.kind === "ready" ? timeline.data.versions : [];
  if (!versions.length) return null;
  return <label className="v2-root-picker">{<Tx text={"查看哪个版本的预测结果"} />}<select value={selectedVersionId} onChange={(event) => onSelect(event.target.value)}>
      {versions.map((version) => <option value={version.id} key={version.id}>
        {t("第")} {version.version_no} {t("版")} · {t(version.parent_version_id ? "修订" : "初始")} · {formatTime(version.decision_at)} · {versionProbabilityText(version)}
      </option>)}
    </select>
  </label>;
}

function versionProbabilityText(version: V2TimelineEntry) {
  const probabilities = version.model_status === "experimental_jev"
    ? version.decision_probabilities
    : version.model_status === "experimental_joint" ? version.joint_probabilities : null;
  if (!probabilities) return modelStatusText(version.model_status);
  return `${translatePhrase("跌")} ${Math.round(probabilities.bearish * 100)}% · ${translatePhrase("平")} ${Math.round(probabilities.neutral * 100)}% · ${translatePhrase("涨")} ${Math.round(probabilities.bullish * 100)}%`;
}

function percentProbabilities(probabilities: V2Probabilities) {
  return `${translatePhrase("跌")} ${Math.round(probabilities.bearish * 100)}% · ${translatePhrase("平")} ${Math.round(probabilities.neutral * 100)}% · ${translatePhrase("涨")} ${Math.round(probabilities.bullish * 100)}%`;
}

function ForecastSummary({ root, roots, detail }: { root: V2ForecastRoot | null; roots: ResourceState<V2ForecastRoots>; detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null }) {
  const { t } = useLocale();
  if (roots.kind === "loading" || detail?.kind === "loading") return <section className="v2-card"><p>{<Tx text={"正在读取所选预测状态…"} />}</p></section>;
  if (roots.kind === "error") return null;
  if (!root) return <section className="v2-card"><p className="section-label">{<Tx text={"所选预测"} />}</p><h3>{<Tx text={"尚无预测版本"} />}</h3><p>{<Tx text={"可以创建一项新研究；预测成功生成后会出现在这里。"} />}</p></section>;
  const current = detail?.kind === "ready" ? detail.data.current : null;
  if (!current) return <section className="v2-card"><p className="section-label">{<Tx text={"所选预测"} />}</p><h3>{<Tx text={"无法读取所选预测的当前版本"} />}</h3><p>{<Tx text={"可重新选择预测日期或刷新页面；不会用其他预测的版本代替它。"} />}</p></section>;
  const joint = current.model_status === "experimental_joint" ? current.joint_probabilities : null;
  const jev = current.model_status === "experimental_jev" ? current.decision_probabilities : null;
  const calibration = current.model_manifest.local_calibration as Record<string, unknown> | undefined;
  const provider = current.model_manifest.decision_provider as Record<string, unknown> | undefined;
  const rawProbabilities = provider?.raw_probabilities as V2Probabilities | undefined;
  const calibrated = calibration?.status === "active";
  return <section className="v2-card">
    <p className="section-label">{t("所选预测")} · {t("第")} {current.version_no} {t("版")}</p>
    <h3>{modelStatusText(current.model_status)}</h3>
    <dl><div><dt>{<Tx text={"固定目标日"} />}</dt><dd>{root.target_end_date}</dd></div><div><dt>{<Tx text={"决策时点"} />}</dt><dd>{formatTime(current.decision_at)}</dd></div><div><dt>{<Tx text={"行情截止"} />}</dt><dd>{formatTime(current.market_cutoff_at)}</dd></div></dl>
    {jev ? <><ProbabilityStrip probabilities={jev} label={t(calibrated ? "Jev 经本地校准的实验性概率" : "Jev 原始实验性概率 · 未校准")} />{calibrated && rawProbabilities && <div className="v2-raw-probability"><span>{<Tx text={"校准前 Jev 原始输出"} />}</span><ProbabilityStrip probabilities={rawProbabilities} label={t("原始概率")} compact /></div>}<p className="v2-limit">{t(calibrated ? "本地校准器只调整三类概率分布；Jev 原始输出保存在下方，模型和目标版本均有记录。" : "由 Jev 根据下方同一份 DeepSeek 研究简报输出。当前尚无通过时间外验证的本地校准器，因此显示原始概率。")}</p></>
      : joint ? <ProbabilityStrip probabilities={joint} label={t("实验性联合概率")} />
      : <p className="v2-limit">{t(current.model_status === "research_only" ? "研究结果（未输出概率）：无数值预测。DeepSeek 简报用于材料研究，不会回填旧版概率。" : "当前版本没有可显示的数值判断。")}</p>}
  </section>;
}

function RevisionComparison({ timeline, detail }: { timeline: ResourceState<V2Timeline> | null; detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null }) {
  const { locale, t } = useLocale();
  if (timeline?.kind === "loading") return <section className="v2-card"><p>{<Tx text={"正在读取版本链…"} />}</p></section>;
  if (timeline?.kind === "error") return <section className="v2-card"><p>{t("版本链读取失败：")}{timeline.message}</p></section>;
  const versions = timeline?.kind === "ready" ? timeline.data.versions : [];
  const marketChanged = detail?.kind === "ready" && detail.data.parent
    ? marketSummary(detail.data.current.price_input_manifest) !== marketSummary(detail.data.parent.price_input_manifest)
    : false;
  return <section className="v2-card"><p className="section-label">{<Tx text={"修订对比"} />}</p><h3>{versions.length ? locale === "en-US" ? `${versions.length} historical ${versions.length === 1 ? "version" : "versions"}` : `${versions.length} ${t("个历史版本")}` : t("尚无版本链")}</h3>
    {versions.length > 1 ? <p>{<Tx text={"每个子版本保留固定目标合同；版本差异见下方事件记录。"} />}</p> : <p>{<Tx text={"出现新的合规材料后，可以从这里选择版本发起人工修订。"} />}</p>}
    {versions.length > 0 && <dl><div><dt>{<Tx text={"固定目标"} />}</dt><dd>{String(timeline?.kind === "ready" ? timeline.data.target_contract.target_end_date ?? t("已记录") : "—")}</dd></div><div><dt>{<Tx text={"当前版本"} />}</dt><dd>{t("第")} {versions.at(-1)?.version_no} {t("版")}</dd></div></dl>}
    {detail?.kind === "ready" && detail.data.parent && <p className="v2-limit">{locale === "en-US" ? `${t("市场输入：")} ${marketChanged ? t("已更新（见下方截止时间）") : t("与父版清单一致")}. ${t("本系统尚未验证各输入对市场结果的定量归因。")}` : `${t("市场输入：")}${marketChanged ? t("已更新（见下方截止时间）") : t("与父版清单一致")}。${t("本系统尚未验证各输入对市场结果的定量归因。")}`}</p>}
  </section>;
}

function EvidenceEvents({ timeline, detail, selectedVersionId, onSelect }: {
  timeline: ResourceState<V2Timeline> | null;
  detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null;
  selectedVersionId: string;
  onSelect: (id: string) => void;
}) {
  const { locale, t } = useLocale();
  if (timeline?.kind !== "ready" || timeline.data.versions.length === 0) return null;
  return <section className="v2-events" aria-labelledby="v2-events-title"><div className="section-heading"><div><p className="eyebrow">{<Tx text={"版本与来源"} />}</p><h2 id="v2-events-title">{<Tx text={"预测版本记录"} />}</h2></div><span>{<Tx text={"同一固定目标"} />}</span></div><ol>
    {timeline.data.versions.map((version) => <li key={version.id} className={version.id === selectedVersionId ? "is-selected" : ""}>
      <span className="v2-event-dot" aria-hidden="true" />
      <button type="button" className="v2-event-select" onClick={() => onSelect(version.id)}><strong>{t("第")} {version.version_no} {t("版")} · {t(version.parent_version_id ? "修订版本" : "初始版本")}</strong><p>{formatTime(version.decision_at)} · {modelStatusText(version.model_status)} · {triggerTypeText(version.trigger_type)}</p>{version.change_reason && <small>{changeReasonText(version.change_reason, locale)}</small>}</button>
      {version.model_status === "experimental_jev" && version.decision_probabilities && <div className="v2-timeline-probabilities"><ProbabilityStrip probabilities={version.decision_probabilities} label={t(version.local_calibration_status === "active" ? "Jev + 本地校准" : "Jev 原始实验性概率")} compact />{version.local_calibration_status === "active" && version.raw_decision_probabilities && <small>{t("校准前")} {percentProbabilities(version.raw_decision_probabilities)}</small>}</div>}
      {version.model_status === "experimental_joint" && version.joint_probabilities && <ProbabilityStrip probabilities={version.joint_probabilities} label={t("实验性联合概率")} compact />}
    </li>)}
  </ol><EvidenceDetail detail={detail} /></section>;
}

function changeReasonText(reason: string, locale: "zh-CN" | "en-US") {
  if (reason === "new forecast with frozen observed market and evidence inputs") {
    return locale === "en-US"
      ? "New forecast using a frozen snapshot of the available market data and sources."
      : "新建预测：冻结当时可用的行情和来源快照。";
  }
  const revision = reason.match(/^(manual_revision|automatic_revision): inherited frozen evidence plus (\d+) new or changed observed event\(s\)$/);
  if (!revision) return reason;
  const count = Number(revision[2]);
  if (locale === "en-US") {
    const kind = revision[1] === "manual_revision" ? "Manual revision" : "Automatic revision";
    return `${kind}: carried forward the prior frozen sources and added ${count} new or changed observed ${count === 1 ? "event" : "events"}.`;
  }
  const kind = revision[1] === "manual_revision" ? "人工修订" : "自动修订";
  return `${kind}：继承原冻结来源，并新增或更新了 ${count} 项可观察事件。`;
}

function EvidenceDetail({ detail }: { detail: ResourceState<{ current: V2VersionDetail; parent: V2VersionDetail | null }> | null }) {
  const { locale, t } = useLocale();
  if (detail?.kind === "loading") return <p className="v2-detail-loading">{<Tx text={"正在读取所选版本的保存来源和市场输入…"} />}</p>;
  if (detail?.kind === "error") return <p className="v2-notice error" role="alert">{detail.message}</p>;
  if (detail?.kind !== "ready") return null;
  const { current, parent } = detail.data;
  const countLabel = (count: number) => locale === "en-US"
    ? `${count} ${count === 1 ? "material" : "materials"}`
    : `${count} ${t("条")}`;
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
  const explicitlySelected = explicitSourceKeys(current.research_report);
  const explicitCount = currentSources.filter((item) => explicitlySelected.has(sourceIdentity(item))).length;
  const automaticAddedCount = added.filter((item) => !explicitlySelected.has(sourceIdentity(item))).length;
  const market = current.price_input_manifest;
  const researchStatus = researchStatusText(current.research_report?.research_conclusions_status ?? current.research_report?.status);
  const brief = current.research_brief ?? null;
  const includedKeys = new Set((brief?.material_refs ?? []).map(sourceKey));
  const includedCount = currentSources.filter((item) => includedKeys.has(sourceKey(item))).length;
  return <section className="v2-detail" aria-label={t("所选版本的保存的输入")}>
    <div><p className="section-label">{t("所选第")} {current.version_no} {t("版的来源差异")}</p><h3>{parent ? `${t("新增")} ${added.length} · ${t("状态替换")} ${replaced.length} · ${t("移除")} ${removed.length} · ${t("继承")} ${inherited}` : locale === "en-US" ? `Frozen candidates: ${countLabel(currentSources.length)}` : `${t("冻结候选")} ${countLabel(currentSources.length)}`}</h3><p>{parent ? locale === "en-US" ? `Source composition: ${countLabel(inherited)} inherited from the parent, ${countLabel(explicitCount)} manually selected, and ${countLabel(automaticAddedCount)} automatically added by the system.` : `${t("本版来源构成：父版继承")} ${inherited} ${t("条，人工选入")} ${explicitCount} ${t("条，系统自动新增")} ${automaticAddedCount} ${t("条。")}` : t("初始版本没有父版本可比较。")}</p></div>
    <div className="v2-detail-material-counts"><strong>{brief ? locale === "en-US" ? `Actually cited in the brief: ${countLabel(includedCount)}` : `${t("简报实际引用")} ${countLabel(includedCount)}` : t("该版本未保存简报引用")}</strong><span>{brief ? locale === "en-US" ? `Not included in the brief: ${countLabel(Math.max(0, currentSources.length - includedCount))}. The frozen list records candidate sources available at the time; it does not mean every source was analyzed or sent to Jev.` : `${t("未进入简报")} ${countLabel(Math.max(0, currentSources.length - includedCount))}。冻结清单记录当时的候选来源，不表示这些来源都被分析或交给 Jev。` : t("没有简报记录时，无法从此版本确认哪些材料完成了分析。")}</span></div>
    <dl><div><dt>{<Tx text={"行情数据截止"} />}</dt><dd>{formatTime(String(market.market_cutoff_at ?? current.market_cutoff_at))}</dd></div><div><dt>{<Tx text={"最近完整交易日"} />}</dt><dd>{String(market.latest_completed_session ?? "未记录")}</dd></div><div><dt>{<Tx text={"价格输入哈希"} />}</dt><dd>{shortHash(market.price_input_sha256)}</dd></div><div><dt>{<Tx text={"研究报告"} />}</dt><dd>{researchStatus}</dd></div></dl>
    <div className="v2-source-list">{currentSources.length ? currentSources.map((item, index) => {
      const sourceUrl = typeof item.source_url === "string" ? item.source_url : null;
      const publishedAt = item.published_at ?? item.public_at;
      const key = sourceIdentity(item);
      const status = processingStatus(item, brief, t);
      const sourceOrigin = explicitlySelected.has(key)
        ? t("人工选入")
        : parent && parentBySource.has(key)
          ? item.is_new ? t("系统记录状态变化") : t("从父版继承")
          : parent ? t("系统自动纳入") : t("初始预测自动纳入");
      return <article key={`${eventIdentity(item)}-${index}`}><strong>{evidenceTitle(item, brief?.material_refs.find((ref) => sourceKey(ref) === sourceKey(item)))}</strong>{sourceUrl ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">{<Tx text={"打开原始来源"} />}</a> : <span>{<Tx text={"原始来源链接未记录"} />}</span>}<p>{formatTime(typeof publishedAt === "string" ? publishedAt : null)} · {coverageText(item.coverage_incomplete)}</p><span className={`evidence-processing-badge is-${status.tone}`}>{status.label}</span><small>{status.detail}</small><small>{sourceOrigin} · {t(item.is_new ? "本版本新增/状态变更" : "从父版继承")} · {discoveryKindText(item.discovery_kind)}</small></article>;
    }) : <p>{<Tx text={"这个版本没有保存合规证据来源。"} />}</p>}</div>
  </section>;
}

function explicitSourceKeys(report: Record<string, unknown> | null): Set<string> {
  const selection = report?.automatic_selection;
  if (typeof selection !== "object" || selection === null) return new Set();
  const refs = (selection as { explicit_source_refs?: unknown }).explicit_source_refs;
  if (!Array.isArray(refs)) return new Set();
  return new Set(refs.flatMap((ref) => {
    if (typeof ref !== "object" || ref === null) return [];
    const value = ref as { source_type?: unknown; source_id?: unknown };
    return typeof value.source_type === "string" && typeof value.source_id === "string"
      ? [`${value.source_type}:${value.source_id}`]
      : [];
  }));
}

function eventIdentity(item: Record<string, unknown>) {
  return String(item.event_version_id ?? item.id ?? `${item.source_type}:${item.source_id}`);
}

function openResearchMaterial(material: ResearchMaterialReference) {
  try {
    window.sessionStorage.setItem("market-evidence-agent:material-analysis-focus", JSON.stringify({
      symbol: material.symbol,
      source_type: material.source_type,
      source_id: material.source_id,
      analysis_id: material.analysis_id,
    }));
  } catch { /* Deep link still opens the catalog if browser storage is unavailable. */ }
  window.location.hash = "materials";
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
  if (value === true) return translatePhrase("覆盖不完整");
  if (value === false) return translatePhrase("覆盖状态已记录");
  return translatePhrase("覆盖状态未知");
}

function researchStatusText(value: unknown) {
  switch (value) {
    case "ready":
    case "succeeded": return translatePhrase("研究已就绪");
    case "failed":
    case "failed_before_run": return translatePhrase("研究失败");
    case "not_run": return translatePhrase("未运行研究");
    case "no_eligible_frozen_sources": return translatePhrase("没有合规保存来源");
    default: return translatePhrase("研究状态未记录");
  }
}

function ProbabilityStrip({ probabilities, label, compact = false }: { probabilities: Record<"bearish" | "neutral" | "bullish", number>; label: string; compact?: boolean }) {
  const { t } = useLocale();
  return <div className={`v2-probabilities ${compact ? "compact" : ""}`} aria-label={label}>
    {(["bearish", "neutral", "bullish"] as const).map((key) => <span key={key}><small>{t(key === "bearish" ? "跌" : key === "neutral" ? "平" : "涨")}</small><b>{Math.round(probabilities[key] * 100)}%</b></span>)}
  </div>;
}
