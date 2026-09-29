import { Tx, formatDateTime, getLocalePreference, translatePhrase, useLocale } from "./i18n";
import { useAiContentLocalization, withLocalizedFields } from "./useAiContentLocalization";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { ApiError, getUploadedEvidence, getV2ForecastRoots, getV2ForecastVersion, getV2Timeline, uploadEvidence } from "./api";
import type { ResearchBrief, ResearchEvidencePointer, ResearchMaterialReference, UploadedEvidence, V2ForecastRoots, V2Timeline, V2VersionDetail } from "./types";

type LoadState<T> = { kind: "loading" } | { kind: "ready"; data: T } | { kind: "error"; message: string };
type EvidenceRoute = { versionId: string; sourceType: ResearchMaterialReference["source_type"]; sourceId: string };
type EventGroup = { key: ResearchEvidencePointer["section"] | "background" | "conflicts" | "unknowns" | "changes"; label: string; tone: string; rows: Array<{ text: string; citations: ResearchEvidencePointer[] }> };
type UploadAction = { kind: "idle" } | { kind: "running" } | { kind: "success"; message: string } | { kind: "error"; message: string };

const BASE_HASH = "evidence";

function errorMessage(error: unknown, fallback: string) {
  return translatePhrase(error instanceof ApiError ? error.message : fallback);
}

function readEvidenceRoute(): EvidenceRoute | null {
  const [section, versionId, sourceType, ...sourceParts] = window.location.hash.replace(/^#/, "").split("/");
  if (section !== BASE_HASH || !versionId || (sourceType !== "official_filing" && sourceType !== "uploaded_media") || sourceParts.length === 0) return null;
  try {
    return { versionId: decodeURIComponent(versionId), sourceType, sourceId: decodeURIComponent(sourceParts.join("/")) };
  } catch { return null; }
}

function detailHash(versionId: string, source: ResearchMaterialReference) {
  return `${BASE_HASH}/${encodeURIComponent(versionId)}/${source.source_type}/${encodeURIComponent(source.source_id)}`;
}

function formatTime(value: string | null | undefined) {
  if (!value) return getLocalePreference() === "en-US" ? "Time not recorded" : "时间未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : formatDateTime(date, getLocalePreference(), "UTC");
}

function materialEventGroups(brief: ResearchBrief, analysisId: string, t: (text: string) => string, locale: "zh-CN" | "en-US"): EventGroup[] {
  const groups: EventGroup[] = [
    { key: "supporting", label: t("潜在利好"), tone: "positive", rows: brief.supporting.map((row) => ({ text: row.statement, citations: row.citations })) },
    { key: "counter", label: t("潜在利空 / 反证"), tone: "negative", rows: brief.counter.map((row) => ({ text: row.statement, citations: row.citations })) },
    { key: "facts", label: t("新增事实"), tone: "fact", rows: brief.new_facts.map((row) => ({ text: row.statement, citations: row.citations })) },
    { key: "background", label: t("持续背景"), tone: "background", rows: brief.background.map((row) => ({ text: locale === "en-US" ? `${row.statement} (Continuing reason: ${row.continuing_reason})` : `${row.statement}（持续原因：${row.continuing_reason}）`, citations: row.citations })) },
    { key: "conflicts", label: t("来源冲突"), tone: "uncertain", rows: brief.conflicts.map((row) => ({ text: row.description, citations: row.citations })) },
    { key: "unknowns", label: t("不确定项"), tone: "uncertain", rows: brief.unknowns.map((row) => ({ text: locale === "en-US" ? `${row.question}: ${row.reason}` : `${row.question}：${row.reason}`, citations: row.citations })) },
    { key: "changes", label: t("版本变化"), tone: "fact", rows: brief.changes.map((row) => ({ text: `${t(changeLabel(row.change_type))}: ${row.description}`, citations: row.citations })) },
  ];
  return groups.flatMap((group) => {
    const rows = group.rows.flatMap((row) => {
      const citations = row.citations.filter((citation) => citation.analysis_id === analysisId);
      return citations.length ? [{ ...row, citations }] : [];
    });
    return rows.length ? [{ ...group, rows }] : [];
  });
}

function rootLabel(root: V2ForecastRoots["roots"][number], t: (text: string) => string) {
  return `${formatTime(root.decision_at)} · ${t("最新第")} ${root.latest_version_no} ${t("版")}`;
}

function versionLabel(version: V2Timeline["versions"][number], t: (text: string) => string) {
  const model = version.model_status === "experimental_jev" ? "Jev" : version.model_status === "research_only" ? t("研究简报") : version.model_status === "experimental_joint" ? t("联合研究") : t("行情基线");
  return `${t("第")} ${version.version_no} ${t("版")} · ${formatTime(version.decision_at)} · ${model}`;
}

function sourceTypeLabel(sourceType: ResearchMaterialReference["source_type"], locale: "zh-CN" | "en-US") {
  return locale === "en-US" ? sourceType === "official_filing" ? "SEC filing" : "Uploaded media" : sourceType === "official_filing" ? "SEC 官方文件" : "上传媒体材料";
}

export function EvidenceCenter({ symbol, supported, onOpenForecast }: { symbol: string; supported: boolean; onOpenForecast: () => void }) {
  const { locale, t } = useLocale();
  const [roots, setRoots] = useState<LoadState<V2ForecastRoots>>({ kind: "loading" });
  const [selectedRootId, setSelectedRootId] = useState("");
  const [timeline, setTimeline] = useState<LoadState<V2Timeline> | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState(() => readEvidenceRoute()?.versionId ?? "");
  const [version, setVersion] = useState<LoadState<V2VersionDetail> | null>(null);
  const [route, setRoute] = useState<EvidenceRoute | null>(() => readEvidenceRoute());
  const [uploadOpen, setUploadOpen] = useState(() => window.location.hash.replace(/^#/, "") === `${BASE_HASH}/upload`);
  const [uploaded, setUploaded] = useState<LoadState<UploadedEvidence[]>>({ kind: "loading" });

  useEffect(() => {
    const handleHashChange = () => {
      const next = readEvidenceRoute();
      setRoute(next);
      setUploadOpen(window.location.hash.replace(/^#/, "") === `${BASE_HASH}/upload`);
      if (next?.versionId) setSelectedVersionId(next.versionId);
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setRoots({ kind: "loading" });
    setSelectedRootId("");
    setTimeline(null);
    setVersion(null);
    getV2ForecastRoots(symbol, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setRoots({ kind: "ready", data });
      if (!readEvidenceRoute()?.versionId) setSelectedRootId(data.roots[0]?.id ?? "");
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setRoots({ kind: "error", message: errorMessage(error, "暂时无法读取预测版本。") });
    });
    setUploaded({ kind: "loading" });
    getUploadedEvidence(symbol, controller.signal).then((data) => {
      if (!controller.signal.aborted) setUploaded({ kind: "ready", data: data.items ?? [] });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setUploaded({ kind: "error", message: errorMessage(error, "暂时无法读取已上传材料。") });
    });
    return () => controller.abort();
  }, [symbol]);

  useEffect(() => {
    if (!selectedRootId) { setTimeline(null); return; }
    const controller = new AbortController();
    setTimeline({ kind: "loading" });
    const latestVersionId = roots.kind === "ready" ? roots.data.roots.find((root) => root.id === selectedRootId)?.latest_version_id : undefined;
    getV2Timeline(selectedRootId, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setTimeline({ kind: "ready", data });
      setSelectedVersionId((previous) => data.versions.some((item) => item.id === previous)
        ? previous
        : latestVersionId && data.versions.some((item) => item.id === latestVersionId)
          ? latestVersionId
          : data.versions.at(-1)?.id ?? data.versions[0]?.id ?? "");
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setTimeline({ kind: "error", message: errorMessage(error, "暂时无法读取所选预测的版本链。") });
    });
    return () => controller.abort();
  }, [selectedRootId, roots]);

  useEffect(() => {
    if (!selectedVersionId) { setVersion(null); return; }
    const controller = new AbortController();
    setVersion({ kind: "loading" });
    getV2ForecastVersion(selectedVersionId, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setVersion({ kind: "ready", data });
      setSelectedRootId((previous) => previous || data.root_id);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setVersion({ kind: "error", message: errorMessage(error, "暂时无法读取该预测版本的证据。") });
    });
    return () => controller.abort();
  }, [selectedVersionId]);

  const selectedVersion = version?.kind === "ready" && version.data.id === selectedVersionId ? version.data : null;
  const sourceBrief = selectedVersion?.research_brief ?? null;
  const localization = useAiContentLocalization("forecast_brief", selectedVersion?.id);
  const brief = sourceBrief && localization.kind === "ready" ? withLocalizedFields(sourceBrief, localization.fields) : sourceBrief;
  const sources = brief?.material_refs ?? [];
  const sourceByRoute = useMemo(() => route && selectedVersion?.id === route.versionId
    ? sources.find((source) => source.source_type === route.sourceType && source.source_id === route.sourceId) ?? null
    : null, [route, selectedVersion, sources]);
  const selectedTimeline = timeline?.kind === "ready" ? timeline.data.versions : [];
  function selectRoot(rootId: string) {
    window.location.hash = BASE_HASH;
    setRoute(null);
    setTimeline({ kind: "loading" });
    setVersion(null);
    setSelectedVersionId("");
    setSelectedRootId(rootId);
  }

  function selectVersion(versionId: string) {
    window.location.hash = BASE_HASH;
    setRoute(null);
    setVersion({ kind: "loading" });
    setSelectedVersionId(versionId);
  }

  function openMaterial(source: ResearchMaterialReference) {
    if (selectedVersion) window.location.hash = detailHash(selectedVersion.id, source);
  }

  function openFullAnalysis(source: ResearchMaterialReference) {
    try {
      window.sessionStorage.setItem("market-evidence-agent:material-analysis-focus", JSON.stringify({
        symbol: source.symbol,
        source_type: source.source_type,
        source_id: source.source_id,
        analysis_id: source.analysis_id,
      }));
    } catch { /* Browser storage is optional; the material catalogue remains available. */ }
    window.location.hash = "materials";
  }

  const closeUploadDialog = useCallback(() => {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${BASE_HASH}`);
    setUploadOpen(false);
  }, []);

  async function handleUpload(input: Parameters<typeof uploadEvidence>[1]) {
    const item = await uploadEvidence(symbol, input);
    setUploaded((previous) => ({ kind: "ready", data: sortUploaded([item, ...(previous.kind === "ready" ? previous.data : [])]) }));
  }

  return <section className="workspace-stack evidence-center" aria-labelledby="evidence-center-title">
    <header className="workspace-intro evidence-center-intro">
      <div><p className="eyebrow">{<Tx text={"预测版本 · 材料事件 · 原文引用"} />}</p><h2 id="evidence-center-title">{t(route ? "事件与判断" : "本次预测引用的证据")}</h2><p>{<Tx text={"按预测版本查看简报实际引用的材料及其事件判断。候选冻结材料只有被简报引用后才会出现在这里。"} />}</p></div>
      <a className="primary-action" href={`#${BASE_HASH}/upload`} aria-haspopup="dialog" style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", textDecoration: "none" }}>{<Tx text={"上传媒体材料"} />}</a>
    </header>

    <section className="evidence-version-controls" aria-label={t("选择要查看的预测版本")}>
      <label>{<Tx text={"预测任务"} />}<select value={selectedRootId} disabled={roots.kind !== "ready" || roots.data.roots.length === 0} onChange={(event) => selectRoot(event.target.value)}>
          {roots.kind === "ready" && roots.data.roots.length === 0 && <option value="">{<Tx text={"暂无预测任务"} />}</option>}
          {roots.kind === "ready" && roots.data.roots.map((root) => <option value={root.id} key={root.id}>{rootLabel(root, t)}</option>)}
        </select>
      </label>
      <label>{<Tx text={"预测版本"} />}<select value={selectedVersionId} disabled={timeline?.kind !== "ready" || selectedTimeline.length === 0} onChange={(event) => selectVersion(event.target.value)}>
          {timeline?.kind === "ready" && selectedTimeline.length === 0 && <option value="">{<Tx text={"该预测没有版本"} />}</option>}
          {timeline?.kind === "ready" && selectedTimeline.map((item) => <option value={item.id} key={item.id}>{versionLabel(item, t)}</option>)}
        </select>
      </label>
      {selectedVersion && <span className="evidence-version-meta">{t("决策时间")} {formatTime(selectedVersion.decision_at)}</span>}
    </section>

    {roots.kind === "loading" && <p className="inventory-status" role="status">{<Tx text={"正在读取预测任务…"} />}</p>}
    {roots.kind === "error" && <p className="inventory-status inventory-error" role="alert">{roots.message}</p>}
    {roots.kind === "ready" && roots.data.roots.length === 0 && <div className="evidence-empty-state"><span className="section-label">{<Tx text={"暂无预测版本"} />}</span><h3>{<Tx text={"创建预测后，这里会整理实际引用的材料"} />}</h3><p>{<Tx text={"SEC 原始材料和 AI 分析历史仍在材料库中管理。媒体材料可以先上传保存，分析后再由预测版本引用。"} />}</p><button className="primary-action" type="button" onClick={onOpenForecast}>{<Tx text={"前往预测工作台"} />}</button></div>}
    {selectedRootId && timeline?.kind === "loading" && <p className="inventory-status" role="status">{<Tx text={"正在读取预测版本链…"} />}</p>}
    {timeline?.kind === "error" && <p className="inventory-status inventory-error" role="alert">{timeline.message}</p>}
    {selectedVersionId && version?.kind === "loading" && <p className="inventory-status" role="status">{<Tx text={"正在读取所选版本的研究简报…"} />}</p>}
    {version?.kind === "error" && <p className="inventory-status inventory-error" role="alert">{version.message}</p>}
    {selectedVersion && localization.kind === "loading" && <p className="localization-status" role="status">{t("Translating saved research brief…")}</p>}
    {selectedVersion && localization.kind === "error" && <p className="localization-status" role="status">{t("English translation is temporarily unavailable; showing the saved original.")}</p>}

    {selectedVersion && route && <>
      <a className="evidence-back-link" href={`#${BASE_HASH}`}>{<Tx text={"← 返回证据列表"} />}</a>
      {sourceByRoute
        ? <MaterialEvidenceDetail version={selectedVersion} brief={brief} source={sourceByRoute} onOpenAnalysis={() => openFullAnalysis(sourceByRoute)} />
        : <div className="evidence-empty-state"><h3>{<Tx text={"该版本没有引用这份材料"} />}</h3><p>{<Tx text={"证据页只展示研究简报实际引用的材料。可以返回列表选择其他材料。"} />}</p><a className="secondary-action" href={`#${BASE_HASH}`}>{<Tx text={"返回列表"} />}</a></div>}
    </>}

    {selectedVersion && !route && <>
      {!brief && <div className="evidence-empty-state"><span className="section-label">{<Tx text={"没有保存简报"} />}</span><h3>{<Tx text={"这个版本没有可展示的材料事件"} />}</h3><p>{<Tx text={"版本记录和冻结候选材料可以在预测工作台查看。"} />}</p></div>}
      {brief && sources.length === 0 && <div className="evidence-empty-state"><span className="section-label">{<Tx text={"简报没有材料引用"} />}</span><h3>{<Tx text={"该版本没有引用已分析材料"} />}</h3><p>{<Tx text={"只有进入研究简报并带有对应事件的材料，才会显示为此版本的证据卡片。"} />}</p></div>}
      {brief && sources.length > 0 && <div className="evidence-source-grid" aria-label={`${symbol} ${t("第")} ${selectedVersion.version_no} ${t("版引用材料")}`}>
        {sources.map((source) => <EvidenceMaterialCard key={`${source.source_type}:${source.source_id}:${source.analysis_id}`} source={source} brief={brief} onClick={() => openMaterial(source)} />)}
      </div>}
    </>}

    {uploadOpen && <UploadMediaDialog symbol={symbol} supported={supported} uploaded={uploaded} onUpload={handleUpload} onClose={closeUploadDialog} />}
  </section>;
}

function EvidenceMaterialCard({ source, brief, onClick }: { source: ResearchMaterialReference; brief: ResearchBrief; onClick: () => void }) {
  const { locale, t } = useLocale();
  const groups = materialEventGroups(brief, source.analysis_id, t, locale);
  const eventCount = groups.reduce((count, group) => count + group.rows.length, 0);
  return <button type="button" className="evidence-source-card" onClick={onClick}>
    <span className="evidence-card-top"><span>{source.source_type === "official_filing" ? "SEC" : t("媒体")}</span><small>{source.explicitly_selected ? t("人工选入") : t("简报引用")}</small></span>
    <strong>{source.title || sourceTypeLabel(source.source_type, locale)}</strong>
    <span className="evidence-card-meta">{sourceTypeLabel(source.source_type, locale)} · {t("发布")} {formatTime(source.published_at)}</span>
    {source.source_type === "uploaded_media" && typeof source.user_rating_stars === "number" && <span className="evidence-card-stars" aria-label={`${t("用户可信度")} ${source.user_rating_stars}/5`}>{"★".repeat(clampStars(source.user_rating_stars))}{"☆".repeat(5 - clampStars(source.user_rating_stars))}<small>{source.user_rating_stars}/5</small></span>}
    <span className="evidence-card-bottom"><span>{eventCount} {t("条关联判断")}</span><span>{groups.map((group) => group.label).join(" · ") || t("无事件分类")}</span></span>
    <span className="evidence-card-open">{<Tx text={"查看事件详情 "} />}<span aria-hidden="true">↗</span></span>
  </button>;
}

function MaterialEvidenceDetail({ version, brief, source, onOpenAnalysis }: { version: V2VersionDetail; brief: ResearchBrief | null; source: ResearchMaterialReference; onOpenAnalysis: () => void }) {
  const { locale, t } = useLocale();
  const groups = brief ? materialEventGroups(brief, source.analysis_id, t, locale) : [];
  const eventCount = groups.reduce((count, group) => count + group.rows.length, 0);
  const officialUrl = source.source_url && isHttpsUrl(source.source_url) ? source.source_url : null;
  return <article className="material-evidence-detail">
    <header className="material-evidence-header">
      <div><p className="eyebrow">{sourceTypeLabel(source.source_type, locale)} · {t("第")} {version.version_no} {t("版")}</p><h3>{source.title || sourceTypeLabel(source.source_type, locale)}</h3><p>{t("发布")} {formatTime(source.published_at)} · {t("观察")} {formatTime(source.observed_at)} · {eventCount} {t("条关联判断")}</p></div>
      <div className="material-evidence-actions">
        {officialUrl ? <a className="secondary-action" href={officialUrl} target="_blank" rel="noopener noreferrer">{<Tx text={"打开原始来源 ↗"} />}</a> : <span>{<Tx text={"原始来源链接未记录"} />}</span>}
        <button className="secondary-action" type="button" onClick={onOpenAnalysis}>{<Tx text={"查看完整材料分析"} />}</button>
      </div>
    </header>
    {source.source_type === "uploaded_media" && typeof source.user_rating_stars === "number" && <p className="material-evidence-rating"><span>{"★".repeat(clampStars(source.user_rating_stars))}{"☆".repeat(5 - clampStars(source.user_rating_stars))}</span> {t("可信度")} {source.user_rating_stars}/5 · {source.user_rating_label ? t(source.user_rating_label) : t("上传时的用户判断")}</p>}
    {source.coverage_incomplete || source.truncated ? <p className="evidence-limitation">{<Tx text={"该材料只覆盖了部分原文，事件判断受可用内容范围限制。"} />}</p> : null}
    {groups.length ? <div className="evidence-event-groups">{groups.map((group) => <section className="evidence-event-group" key={group.key}>
      <div className="evidence-event-group-heading"><h4>{group.label}</h4><span>{group.rows.length}</span></div>
      {group.rows.map((row, index) => <article className="evidence-event" key={`${group.key}:${index}`}>
        <span className={`evidence-event-tag is-${group.tone}`}>{group.label}</span>
        <p>{row.text}</p>
        <small>{t("依据该材料分析中的")} {row.citations.map((citation) => `${sectionLabel(citation.section, t)} #${citation.item_id}`).join(" · ")}</small>
      </article>)}
    </section>)}</div> : <div className="evidence-empty-state"><h3>{<Tx text={"简报没有从这份材料中提取事件判断"} />}</h3><p>{<Tx text={"该材料被简报引用，但没有关联到新增事实、利好、利空或不确定项。可在材料库查看完整分析。"} />}</p><button className="secondary-action" type="button" onClick={onOpenAnalysis}>{<Tx text={"打开材料分析"} />}</button></div>}
    <p className="evidence-version-footnote">{t("标签表示该事件在第")} {version.version_no} {t("版研究简报中的作用；“潜在利好 / 利空”对应这版研究判断，不是脱离预测方向的通用情绪分类。")}</p>
  </article>;
}

function UploadMediaDialog({ symbol, supported, uploaded, onUpload, onClose }: {
  symbol: string;
  supported: boolean;
  uploaded: LoadState<UploadedEvidence[]>;
  onUpload: (input: Parameters<typeof uploadEvidence>[1]) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useLocale();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [publishedAt, setPublishedAt] = useState("");
  const [stars, setStars] = useState(3);
  const [impactSeverity, setImpactSeverity] = useState<"low" | "medium" | "high">("medium");
  const [reason, setReason] = useState("");
  const [action, setAction] = useState<UploadAction>({ kind: "idle" });
  const fileInput = useRef<HTMLInputElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape" && action.kind !== "running") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [action.kind, onClose]);

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !isAllowedEvidenceFile(file) || !isHttpsUrl(sourceUrl) || !title.trim() || !publishedAt || !reason.trim()) return;
    setAction({ kind: "running" });
    try {
      await onUpload({ file, title: title.trim(), sourceUrl: sourceUrl.trim(), publishedAt: new Date(publishedAt).toISOString(), credibilityStars: stars, credibilityReason: reason.trim(), impactSeverity });
      setAction({ kind: "success", message: t("材料已保存。它仍需经过 AI 分析并被预测简报引用，才会出现在对应版本的证据卡片中。") });
      setFile(null); setTitle(""); setSourceUrl(""); setPublishedAt(""); setStars(3); setImpactSeverity("medium"); setReason("");
      if (fileInput.current) fileInput.current.value = "";
    } catch (error: unknown) {
      setAction({ kind: "error", message: errorMessage(error, "上传材料失败。") });
    }
  }

  const invalidFile = file !== null && !isAllowedEvidenceFile(file);
  const closeIfBackdrop = (event: MouseEvent<HTMLDivElement>) => { if (event.target === event.currentTarget && action.kind !== "running") onClose(); };
  return <div className="media-dialog-backdrop" role="presentation" onMouseDown={closeIfBackdrop}>
    <section className="media-dialog" role="dialog" aria-modal="true" aria-labelledby="media-dialog-title" aria-describedby="media-dialog-description">
      <header className="media-dialog-header"><div><p className="eyebrow">{symbol} / {t("来源记录")}</p><h3 id="media-dialog-title">{<Tx text={"上传媒体材料"} />}</h3><p id="media-dialog-description">{<Tx text={"保存报道文件、原始链接和你的来源可信度判断。"} />}</p></div><button ref={closeButton} className="media-dialog-close" type="button" onClick={onClose} aria-label={t("关闭上传窗口")} disabled={action.kind === "running"}>×</button></header>
      <div className="media-dialog-content">
        <form className="evidence-form" onSubmit={submitUpload}>
          <label>{<Tx text={"材料文件"} />}<input ref={fileInput} type="file" accept=".txt,.md,.markdown,.pdf,text/plain,text/markdown,application/pdf" required disabled={!supported || action.kind === "running"} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
          {invalidFile && <p className="content-error" role="alert">{<Tx text={"请选择 TXT、Markdown 或 PDF 文件。"} />}</p>}
          <label>{<Tx text={"标题"} />}<input value={title} required maxLength={240} disabled={!supported || action.kind === "running"} onChange={(event) => setTitle(event.target.value)} placeholder={t("例如：权威媒体报道产品重大问题")} /></label>
          <label>{<Tx text={"原始 HTTPS 来源"} />}<input value={sourceUrl} required type="url" inputMode="url" disabled={!supported || action.kind === "running"} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" /></label>
          <label>{<Tx text={"发布时间"} />}<input value={publishedAt} required type="datetime-local" disabled={!supported || action.kind === "running"} onChange={(event) => setPublishedAt(event.target.value)} /></label>
          <fieldset className="credibility-picker"><legend>{<Tx text={"用户评估的消息可信度"} />}</legend><div>{[1, 2, 3, 4, 5].map((value) => <label key={value} className={value <= stars ? "is-active" : ""} title={`${value} ${t("星")}`}><input type="radio" name={`credibility-${symbol}`} value={value} checked={stars === value} disabled={!supported || action.kind === "running"} onChange={() => setStars(value)} /><span aria-hidden="true">★</span><span className="sr-only">{value} {t("星")}</span></label>)}</div><small>{stars} / 5 {t("星")} · {t("记录你的来源判断，不是精确概率，也不代表内容已获官方证实。")}</small></fieldset>
          <label>{<Tx text={"潜在影响程度"} />}<select value={impactSeverity} disabled={!supported || action.kind === "running"} onChange={(event) => setImpactSeverity(event.target.value as "low" | "medium" | "high")}><option value="low">{<Tx text={"低 · 可能影响有限"} />}</option><option value="medium">{<Tx text={"中 · 值得关注"} />}</option><option value="high">{<Tx text={"高 · 可能显著影响市场预期"} />}</option></select><small className="field-note">{<Tx text={"这是对潜在重要性的判断，不是涨跌方向或概率预测。"} />}</small></label>
          <label>{<Tx text={"评分理由"} />}<textarea value={reason} required rows={3} maxLength={1000} disabled={!supported || action.kind === "running"} onChange={(event) => setReason(event.target.value)} placeholder={t("例如：媒体的一手采访、交叉报道情况，或尚待确认的原因")} /></label>
          <button className="action-button" type="submit" disabled={!supported || action.kind === "running" || !file || invalidFile || !isHttpsUrl(sourceUrl) || !title.trim() || !publishedAt || !reason.trim()}>{action.kind === "running" ? t("正在保存…") : t("保存媒体材料")}</button>
          {action.kind !== "idle" && action.kind !== "running" && <p className={`action-notice ${action.kind}`} role={action.kind === "error" ? "alert" : "status"}>{action.message}</p>}
        </form>
        <UploadedEvidenceList state={uploaded} />
      </div>
    </section>
  </div>;
}

function UploadedEvidenceList({ state }: { state: LoadState<UploadedEvidence[]> }) {
  const { t } = useLocale();
  if (state.kind === "loading") return <p className="material-status">{<Tx text={"正在读取已上传媒体材料…"} />}</p>;
  if (state.kind === "error") return <p className="content-error" role="alert">{state.message}</p>;
  if (!state.data.length) return <p className="material-status">{<Tx text={"还没有手动上传的媒体材料。"} />}</p>;
  return <div className="uploaded-list" aria-label={t("最近上传的媒体材料")}><p className="list-caption">{t("最近保存")} {Math.min(state.data.length, 4)} / {state.data.length} {t("份")}</p>
    {sortUploaded(state.data).slice(0, 4).map((item) => <article className="uploaded-item" key={item.id}>
      <div><strong>{item.title}</strong><span aria-label={`用户评估 ${item.credibility_stars} 星`}>{"★".repeat(clampStars(item.credibility_stars))}{"☆".repeat(5 - clampStars(item.credibility_stars))}</span></div>
      <small>{formatTime(item.published_at)} · {t("潜在影响")}{t(severityLabel(item.impact_severity ?? "medium"))}</small>
      {isHttpsUrl(item.source_url) && <a className="source-link" href={item.source_url} target="_blank" rel="noopener noreferrer">{<Tx text={"打开原始来源 ↗"} />}</a>}
    </article>)}
  </div>;
}

function isHttpsUrl(value: string) {
  try { return new URL(value.trim()).protocol === "https:"; } catch { return false; }
}

function isAllowedEvidenceFile(file: File) {
  return /\.(txt|md|markdown|pdf)$/i.test(file.name) || ["text/plain", "text/markdown", "application/pdf"].includes(file.type);
}

function clampStars(value: number) { return Math.min(5, Math.max(1, Math.round(value || 1))); }
function severityLabel(value: "low" | "medium" | "high") { return { low: "低", medium: "中", high: "高" }[value]; }
function changeLabel(value: string) { return ({ added: "新增", withdrawn: "撤回", modified: "修订", source_status_changed: "来源状态变化" } as Record<string, string>)[value] ?? value; }
function sectionLabel(value: ResearchEvidencePointer["section"], t: (text: string) => string) { return ({ facts: t("事实"), supporting: t("利好"), counter: t("利空"), uncertainties: t("不确定项") })[value]; }
function sortUploaded(items: UploadedEvidence[]) { return [...items].sort((left, right) => Date.parse(right.published_at) - Date.parse(left.published_at) || right.id.localeCompare(left.id)); }
