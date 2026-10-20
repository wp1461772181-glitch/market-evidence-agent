import { Tx, formatDate as formatLocalizedDate, translatePhrase, useLocale } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { ApiError, configuredApiBaseUrl, createMaterialAnalysisJob, fetchFilingContent, getChatMode, getMaterialAnalysis, getMaterialAnalysisHistory, getMaterialAnalysisJob } from "./api";
import type { AgentChatProvider } from "./api";
import type { MaterialAnalysisJob, MaterialAnalysisVersion, MaterialItem, MaterialCitation, MaterialCitationVerification } from "./types";
import { loadProfiles } from "./model-profiles.mjs";
import { useAiContentLocalization, withLocalizedFields } from "./useAiContentLocalization";

type DetailTab = "analysis" | "original" | "history";
type Load<T> = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; value: T } | { kind: "error"; message: string };
type SavedProfile = { id: string; display_name: string; provider: AgentChatProvider; base_url: string; model: string; api_key: string };
type CitationSelection = { quote: string; analysisVersion: number } | null;

export function MaterialAnalysisDetail({ material, initialAnalysisId = null, onUpdated }: { material: MaterialItem; initialAnalysisId?: string | null; onUpdated: () => void }) {
  const { locale, t } = useLocale();
  const [tab, setTab] = useState<DetailTab>(material.latest_analysis_id ? "analysis" : "analysis");
  const [analysisId, setAnalysisId] = useState<string | null>(material.latest_analysis_id);
  const [focusedAnalysisId, setFocusedAnalysisId] = useState<string | null>(initialAnalysisId);
  const [analysis, setAnalysis] = useState<Load<MaterialAnalysisVersion>>({ kind: "idle" });
  const [history, setHistory] = useState<Load<MaterialAnalysisVersion[]>>({ kind: "idle" });
  const [originalFetchState, setOriginalFetchState] = useState<"idle" | "loading">("idle");
  const [job, setJob] = useState<MaterialAnalysisJob | null>(material.latest_job);
  const [actionError, setActionError] = useState<string | null>(null);
  const analysisRequestSequence = useRef(0);
  const [citationSelection, setCitationSelection] = useState<CitationSelection>(null);
  const [profiles, setProfiles] = useState<SavedProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [profileError, setProfileError] = useState<string | null>(null);

  useEffect(() => {
    setFocusedAnalysisId(initialAnalysisId);
  }, [initialAnalysisId, material.source_type, material.source_id]);

  useEffect(() => {
    let cancelled = false;
    loadProfiles(globalThis, configuredApiBaseUrl, getChatMode())
      .then((result) => {
        if (cancelled) return;
        const usable = (result.data.profiles as SavedProfile[]).filter((profile) => Boolean(profile.api_key?.trim()));
        setProfiles(usable);
      })
      .catch((error: unknown) => {
        if (!cancelled) setProfileError(error instanceof Error ? error.message : "无法读取已保存的模型配置。");
      });
    return () => { cancelled = true; };
    // Profiles are intentionally loaded once per material screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const requestSequence = ++analysisRequestSequence.current;
    const selectedAnalysisId = focusedAnalysisId ?? material.latest_analysis_id;
    setAnalysisId(selectedAnalysisId);
    setJob(material.latest_job);
    setAnalysis(selectedAnalysisId ? { kind: "loading" } : { kind: "idle" });
    setHistory({ kind: "idle" });
    setOriginalFetchState("idle");
    setActionError(null);
    setCitationSelection(null);
    if (selectedAnalysisId) {
      getMaterialAnalysis(selectedAnalysisId, controller.signal)
        .then((value) => { if (!controller.signal.aborted && requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "ready", value }); })
        .catch((error: unknown) => { if (!controller.signal.aborted && requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "error", message: actionMessage(error, "暂时无法读取这版分析。") }); });
    }
    return () => { controller.abort(); analysisRequestSequence.current += 1; };
  }, [material.source_type, material.source_id, material.latest_analysis_id, focusedAnalysisId]);

  useEffect(() => {
    if (!job || !job.job_id || !isActive(job.status)) return;
    let stopped = false;
    let busy = false;
    const selectionSequence = analysisRequestSequence.current;
    const timer = window.setInterval(async () => {
      if (busy) return;
      busy = true;
      try {
        const next = await getMaterialAnalysisJob(job.job_id);
        if (stopped) return;
        if (isActive(next.status)) {
          setJob(next);
        } else if (next.status === "succeeded" && next.analysis_id) {
          const saved = await getMaterialAnalysis(next.analysis_id);
          if (stopped) return;
          setJob(next);
          if (selectionSequence === analysisRequestSequence.current) {
            setAnalysisId(next.analysis_id);
            setAnalysis({ kind: "ready", value: saved });
          }
          onUpdated();
        } else {
          setJob(next);
          if (next.status === "failed" || next.status === "blocked_data") {
            setActionError(jobError(next.safe_error_code));
          }
          onUpdated();
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

  async function startAnalysis(force: boolean) {
    setFocusedAnalysisId(null);
    setActionError(null);
    setJob({ job_id: "", status: "queued", analysis_id: null, cache_hit: false });
    const selectedProfile = profiles.find((profile) => profile.id === profileId);
    const modelOverride = selectedProfile ? {
      provider: selectedProfile.provider,
      base_url: selectedProfile.base_url,
      model: selectedProfile.model,
      api_key: selectedProfile.api_key,
    } : undefined;
    try {
      const created = await createMaterialAnalysisJob(material.source_type, material.source_id, force, modelOverride);
      setJob(created);
      if (created.status === "succeeded" && created.analysis_id) {
        setAnalysisId(created.analysis_id);
        setAnalysis({ kind: "ready", value: await getMaterialAnalysis(created.analysis_id) });
        onUpdated();
      } else if (created.status === "blocked_data") {
        setActionError(t("这份材料目前没有可分析的正文。请先读取 SEC 原文，或核对已上传文件。"));
        onUpdated();
      }
    } catch (error) {
      setJob(material.latest_job);
      setActionError(actionMessage(error, "提交分析任务失败。"));
    }
  }

  async function fetchOriginal() {
    if (material.source_type !== "official_filing") return;
    setOriginalFetchState("loading");
    setActionError(null);
    try {
      const accession = material.title.split(" ").slice(1).join(" ");
      await fetchFilingContent(material.symbol, accession);
    } catch (error) {
      setActionError(actionMessage(error, "读取 SEC 原文失败。请打开来源网站核对状态。"));
    }
    finally { setOriginalFetchState("idle"); onUpdated(); }
  }

  async function openHistoryVersion(id: string) {
    const requestSequence = ++analysisRequestSequence.current;
    setAnalysisId(id);
    setCitationSelection(null);
    setAnalysis({ kind: "loading" });
    setTab("analysis");
    try {
      const value = await getMaterialAnalysis(id);
      if (requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "ready", value });
    } catch (error) {
      if (requestSequence === analysisRequestSequence.current) setAnalysis({ kind: "error", message: actionMessage(error, "暂时无法读取选中的历史版本。") });
    }
  }

  function locateCitation(citation: MaterialCitation) {
    if (!citation.quote.trim() || analysis.kind !== "ready") return;
    setCitationSelection({ quote: citation.quote, analysisVersion: analysis.value.version_no });
    setTab("original");
  }

  const selectedAnalysis = analysis.kind === "ready" ? analysis.value : null;
  const localization = useAiContentLocalization("material_analysis", selectedAnalysis?.analysis_id);
  const displayAnalysis = selectedAnalysis && localization.kind === "ready"
    ? { ...selectedAnalysis, payload: withLocalizedFields(selectedAnalysis.payload, localization.fields) }
    : selectedAnalysis;
  const hasActiveJob = Boolean(job && isActive(job.status));
  const canAnalyze = material.can_view_original;
  const cachedInvalidOutput = job?.status === "failed" && job.safe_error_code === "invalid_model_output";
  return <section className="material-detail" aria-label={`${material.title} ${t("详情")}`}>
    <div className="material-detail-heading"><div><p className="eyebrow">{t(material.source_type === "official_filing" ? "SEC 官方材料" : "用户上传材料")} · {material.symbol}</p><h2>{material.title}</h2><p>{t("发布")} {formatDate(material.published_at, locale)} · {t("发现")} {formatDate(material.observed_at, locale)}</p></div><a href={material.source_url} target="_blank" rel="noopener noreferrer">{<Tx text={"打开来源 ↗"} />}</a></div>
    <div className="material-tabs" role="tablist" aria-label={t("材料详情")}>
      <button id="material-tab-analysis" role="tab" aria-selected={tab === "analysis"} aria-controls="material-panel" onClick={() => setTab("analysis")}>{<Tx text={"AI 分析"} />}</button>
      <button id="material-tab-original" role="tab" aria-selected={tab === "original"} aria-controls="material-panel" onClick={() => { setCitationSelection(null); setTab("original"); }}>{<Tx text={"原始材料"} />}</button>
      <button id="material-tab-history" role="tab" aria-selected={tab === "history"} aria-controls="material-panel" onClick={() => setTab("history")}>{<Tx text={"分析历史"} />}</button>
    </div>
    <div className="material-tab-panel" id="material-panel" role="tabpanel" aria-labelledby={`material-tab-${tab}`}>
      {actionError && <p className="material-error" role="alert">{actionError}</p>}
      {tab === "analysis" && <>
        {canAnalyze && <div className="analysis-model-choice" style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", margin: "0 0 12px" }}>
          <span className="section-label">{t("分析模型")}</span>
          <select value={profileId} onChange={(event) => setProfileId(event.target.value)} disabled={hasActiveJob} aria-label={t("分析模型")} style={{ maxWidth: "100%" }}>
            <option value="">{t("系统默认（后台配置）")}</option>
            {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.display_name} · {profile.model}</option>)}
          </select>
          {profileId && <small className="analysis-quiet">{t("仅本次分析使用；默认模型在 Agent 管理页配置。")}</small>}
          {profileError && <small className="analysis-quiet">{profileError}</small>}
        </div>}
        {hasActiveJob && <div className="material-job-status" role="status"><span className="loading-mark" aria-hidden="true" />{job?.status === "queued" ? t("分析任务排队中…") : t("正在分析这份材料…")}</div>}
        {job && (job.status === "failed" || job.status === "blocked_data") && <p className="material-error" role="status">{t("最近一次分析尝试")}{job.status === "failed" ? t("失败") : t("暂不可执行")}（{jobError(job.safe_error_code)}）。{t("如有既有成功版本，仍可在此查看。")}</p>}
        {analysis.kind === "loading" && <p className="material-empty">{<Tx text={"正在读取保存的分析版本…"} />}</p>}
        {analysis.kind === "error" && <p className="material-error" role="alert">{analysis.message}</p>}
          {displayAnalysis && <AnalysisView analysis={displayAnalysis} onLocateCitation={locateCitation} />}
          {locale === "en-US" && localization.kind === "loading" && <p className="localization-status" role="status">{t("Translating saved AI analysis…")}</p>}
          {locale === "en-US" && localization.kind === "error" && <p className="localization-status" role="status">{t("English translation is temporarily unavailable; showing the saved original.")}</p>}
        {!selectedAnalysis && analysis.kind !== "loading" && canAnalyze && <div className="material-start-card"><span className="section-label">{<Tx text={"单份材料分析"} />}</span><h3>{cachedInvalidOutput ? t("上次分析未通过校验") : t("这份材料还没有成功的分析版本")}</h3><p>{cachedInvalidOutput ? t("这次失败结果被暂存了。重新分析会跳过该失败记录，再请求 DeepSeek；不会删除其他成功版本。") : t("分析会引用本次冻结的材料文本。保存后重新打开可直接查看，不会重复调用模型。")}</p><button className="primary-action" disabled={hasActiveJob} onClick={() => startAnalysis(cachedInvalidOutput)}>{hasActiveJob ? t("任务处理中…") : cachedInvalidOutput ? t("跳过失败记录并重新分析") : t("开始分析")}</button></div>}
        {!canAnalyze && material.source_type === "official_filing" && <div className="material-start-card"><span className="section-label">{<Tx text={"先准备正文"} />}</span><h3>{<Tx text={"SEC 目录记录还没有可分析的正文"} />}</h3><p>{<Tx text={"读取 SEC 正文后才能运行分析；也可以在“原始材料”页直接打开来源网站阅读。"} />}</p><button className="primary-action" disabled={hasActiveJob || originalFetchState === "loading"} onClick={() => { void fetchOriginal(); }}>{originalFetchState === "loading" ? t("正在读取 SEC 正文…") : t("读取 SEC 正文供 AI 分析")}</button></div>}
        {!canAnalyze && material.source_type !== "official_filing" && <div className="material-start-card"><span className="section-label">{<Tx text={"原始来源"} />}</span><h3>{<Tx text={"这份上传材料暂无可分析正文"} />}</h3><p>{<Tx text={"可以在“原始材料”页打开来源链接核对。"} />}</p></div>}
        {selectedAnalysis && <button className="secondary-action material-reanalyze" disabled={hasActiveJob} onClick={() => startAnalysis(true)}>{hasActiveJob ? t("重新分析中…") : t("重新分析")}</button>}
      </>}
      {tab === "original" && <OriginalView material={material} citation={citationSelection} />}
      {tab === "history" && <HistoryView state={history} selectedAnalysisId={analysisId} onSelect={openHistoryVersion} locale={locale} />}
    </div>
  </section>;
}

function AnalysisView({ analysis, onLocateCitation }: { analysis: MaterialAnalysisVersion; onLocateCitation: (citation: MaterialCitation) => void }) {
  const { locale, t } = useLocale();
  const payload = analysis.payload;
  const truncated = Boolean(analysis.source_manifest.truncated || analysis.source_manifest.coverage_incomplete);
  const verification = analysis.citation_verification ?? verificationFromPayload(payload);
  return <article className="analysis-version">
    <div className="analysis-version-meta"><span>{t("分析")} v{analysis.version_no}</span><span>{formatDate(analysis.created_at, locale)}</span>{truncated && <strong>{<Tx text={"仅分析可用摘录"} />}</strong>}{verification.total > 0 && <span>{t("引用已核对")} {verification.verified}/{verification.total}</span>}</div>
    <p className="analysis-summary">{payload.summary}</p>
    {verification.unverified > 0 && <p className="analysis-quiet" role="status">{t("有")} {verification.unverified} {t("条引文未能在原文中逐字定位，已保留结论并标记为“引用待核对”。")}</p>}
    <AnalysisSection title={t("原文事实")} rows={payload.facts} empty={t("未提取到有引用支持的事实。")} onLocateCitation={onLocateCitation} />
    <AnalysisSection title={t("潜在利好")} rows={payload.supporting} empty={t("当前材料没有足够依据支持利好判断。")} onLocateCitation={onLocateCitation} />
    <AnalysisSection title={t("潜在利空 / 反证")} rows={payload.counter} empty={t("当前材料没有足够依据支持利空判断。")} onLocateCitation={onLocateCitation} />
    <AnalysisSection title={t("未知与不确定")} rows={payload.uncertainties} empty={t("没有记录额外的不确定项。")} onLocateCitation={onLocateCitation} />
    {payload.key_numbers.length > 0 && <section className="analysis-section"><h3>{<Tx text={"关键数字"} />}</h3>{payload.key_numbers.map((item, index) => <div className="analysis-number" key={`${item.name}:${index}`}><strong>{item.name}</strong><span>{item.value_text}{item.period ? ` · ${item.period}` : ""}</span><CitationList citations={item.citations} onLocateCitation={onLocateCitation} /></div>)}</section>}
  </article>;
}

function AnalysisSection({ title, rows, empty, onLocateCitation }: { title: string; rows: Array<{ id: string; statement: string; rationale?: string; reason?: string; citations: MaterialCitation[] }>; empty: string; onLocateCitation: (citation: MaterialCitation) => void }) {
  return <section className="analysis-section"><h3>{title}<span>{rows.length}</span></h3>{rows.length ? rows.map((row) => <article key={row.id}><p>{row.statement}</p>{(row.rationale || row.reason) && <small>{row.rationale || row.reason}</small>}<CitationList citations={row.citations} onLocateCitation={onLocateCitation} /></article>) : <p className="analysis-quiet">{empty}</p>}</section>;
}
function CitationList({ citations, onLocateCitation }: { citations: MaterialCitation[]; onLocateCitation: (citation: MaterialCitation) => void }) {
  const { t } = useLocale();
  if (!citations?.length) return null;
  return <blockquote className="material-citation">{citations.map((citation, index) => {
    const kind = citation.match_kind ?? "exact";
    const badge = kind === "fuzzy"
      ? `${t("近似引用")} ${Math.round((citation.similarity ?? 0) * 100)}%`
      : kind === "normalized" ? t("规范化匹配") : kind === "unverified" ? t("引用待核对") : null;
    return <p key={`${citation.quote}:${index}`}>
      <button type="button" onClick={() => onLocateCitation(citation)} aria-label={t("查看这条引文的原始来源")}>“{citation.quote}” ↗</button>
      {badge && <small className="analysis-quiet" style={{ marginLeft: "6px" }}>{badge}</small>}
      {kind === "unverified" && citation.original_quote && citation.original_quote !== citation.quote && <small className="analysis-quiet"> {t("模型原文：")}{citation.original_quote}</small>}
    </p>;
  })}</blockquote>;
}

function OriginalView({ material, citation }: { material: MaterialItem; citation: CitationSelection }) {
  const { t } = useLocale();
  return <article className="material-source-card">
    <span className="section-label">{t(material.source_type === "official_filing" ? "SEC 官方原文" : "原始来源")}</span>
    <h3>{<Tx text={"在来源网站阅读材料"} />}</h3>
    {citation && <blockquote className="material-source-quote"><p>{t("分析")} v{citation.analysisVersion} {t("引用")}: “{citation.quote}”</p><small>{<Tx text={"请打开来源核对这段原文。"} />}</small></blockquote>}
    <a className="material-source-url" href={material.source_url} target="_blank" rel="noopener noreferrer"><span>{material.source_url}</span><strong aria-hidden="true">↗</strong></a>
    <p className="material-source-help">{<Tx text={"原文在新标签页打开，应用内不再重复展示或重新排版整篇正文。"} />}</p>
    {material.can_download_original && <a className="secondary-action download-original" href={`/api/v3/materials/${material.source_type}/${encodeURIComponent(material.source_id)}/download`}>{<Tx text={"下载上传的原始文件"} />}</a>}
  </article>;
}

function HistoryView({ state, selectedAnalysisId, onSelect, locale }: { state: Load<MaterialAnalysisVersion[]>; selectedAnalysisId: string | null; onSelect: (id: string) => void; locale: "zh-CN" | "en-US" }) {
  const { t } = useLocale();
  if (state.kind === "loading" || state.kind === "idle") return <p className="material-empty">{<Tx text={"正在读取分析历史…"} />}</p>;
  if (state.kind === "error") return <p className="material-error" role="alert">{state.message}</p>;
  if (state.value.length === 0) return <p className="material-empty">{<Tx text={"还没有保存成功的分析版本。失败的尝试只记录在最近任务状态中。"} />}</p>;
  return <ol className="analysis-history">{state.value.map((version) => <li key={version.analysis_id}><button className={selectedAnalysisId === version.analysis_id ? "is-selected" : ""} onClick={() => onSelect(version.analysis_id)}><span><strong>{t("分析")} v{version.version_no}</strong><small>{formatDate(version.created_at, locale)} · {version.actual_model}</small></span><span>{<Tx text={"查看 →"} />}</span></button></li>)}</ol>;
}

function isActive(status: string) { return status === "queued" || status === "running"; }
function jobError(code?: string | null) {
  const message = code === "no_content" ? "没有可分析正文，请先读取 SEC 原文" : code === "invalid_model_output" ? "分析输出未通过原文引用或格式校验" : code === "provider_error" ? "分析服务暂时不可用" : code === "analysis_model_unconfigured" ? "所选分析模型尚未配置 API key，请先在 Agent 管理页设置默认模型" : code === "credential_storage_unavailable" ? "服务端凭据加密密钥未配置，暂时无法保存分析模型" : code === "config_version_changed" ? "分析配置已更新，请新建任务" : code || "请检查正文后重试";
  return translatePhrase(message);
}

function verificationFromPayload(payload: MaterialAnalysisVersion["payload"]): MaterialCitationVerification {
  const counts = { total: 0, verified: 0, unverified: 0, exact: 0, normalized: 0, fuzzy: 0 };
  const groups = [payload.facts, payload.supporting, payload.counter, payload.uncertainties, payload.key_numbers];
  for (const group of groups) for (const item of group) for (const citation of item.citations) {
    const kind = citation.match_kind ?? "exact";
    counts.total += 1;
    if (kind === "unverified") counts.unverified += 1;
    else {
      counts.verified += 1;
      if (kind === "fuzzy") counts.fuzzy += 1;
      else if (kind === "normalized") counts.normalized += 1;
      else counts.exact += 1;
    }
  }
  return { ...counts, verified_ratio: counts.total ? counts.verified / counts.total : 1 };
}
function actionMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) return translatePhrase(error.status === 402 ? "模型服务余额或账单状态需要处理后再试。" : error.status === 429 ? "模型服务暂时限流，请稍后重试。" : error.message);
  return translatePhrase(fallback);
}
function formatDate(value: string | null | undefined, locale: "zh-CN" | "en-US" = "zh-CN") {
  if (!value) return locale === "en-US" ? "Time not recorded" : "时间未记录";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value.slice(0, 16) : formatLocalizedDate(date, locale, "UTC");
}
