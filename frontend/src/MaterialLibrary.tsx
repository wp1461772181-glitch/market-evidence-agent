import { Tx, formatDate as formatLocalizedDate, getLocalePreference, translatePhrase, useLocale } from "./i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getMaterialLibrary } from "./api";
import { MaterialAnalysisDetail } from "./MaterialAnalysisDetail";
import type { FilingScanResult, MaterialAnalysisStatus, MaterialItem, MaterialSourceType } from "./types";

type MaterialFocus = { symbol: string; source_type: MaterialSourceType; source_id: string; analysis_id: string };

function takeMaterialFocus(symbol: string): MaterialFocus | null {
  try {
    const raw = window.sessionStorage.getItem("market-evidence-agent:material-analysis-focus");
    window.sessionStorage.removeItem("market-evidence-agent:material-analysis-focus");
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<MaterialFocus>;
    return value.symbol === symbol && (value.source_type === "official_filing" || value.source_type === "uploaded_media")
      && typeof value.source_id === "string" && typeof value.analysis_id === "string"
      ? value as MaterialFocus : null;
  } catch { return null; }
}

const STATUS_LABEL: Record<MaterialAnalysisStatus, string> = {
  not_started: "待分析", queued: "排队中", running: "分析中", succeeded: "已分析", failed: "最近失败", blocked_data: "缺少正文",
};

export function MaterialLibrary({ symbol, onScan }: { symbol: string; onScan: () => Promise<FilingScanResult> }) {
  const { t } = useLocale();
  const [sourceType, setSourceType] = useState<MaterialSourceType | "all">("all");
  const [analysisStatus, setAnalysisStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [materialFocus, setMaterialFocus] = useState<MaterialFocus | null>(() => takeMaterialFocus(symbol));
  const [focusAnalysisId, setFocusAnalysisId] = useState<string | null>(null);
  const [focusMessage, setFocusMessage] = useState<string | null>(null);
  const [scanState, setScanState] = useState<{ kind: "idle" } | { kind: "running" } | { kind: "success"; message: string } | { kind: "error"; message: string }>({ kind: "idle" });
  const [page, setPage] = useState<{ kind: "loading" } | { kind: "ready"; items: MaterialItem[]; total: number } | { kind: "error"; message: string }>({ kind: "loading" });
  const [selected, setSelected] = useState<MaterialItem | null>(null);
  const selectedRef = useRef<MaterialItem | null>(null);
  const pageRequestRef = useRef(0);
  selectedRef.current = selected;

  useEffect(() => {
    const controller = new AbortController();
    const requestNumber = ++pageRequestRef.current;
    setPage({ kind: "loading" });
    getMaterialLibrary(symbol, { sourceType, analysisStatus, offset, signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted || requestNumber !== pageRequestRef.current) return;
        setPage({ kind: "ready", items: result.items, total: result.total });
        const previous = selectedRef.current;
        setSelected(previous?.symbol === symbol
          ? result.items.find((item) => item.source_id === previous.source_id && item.source_type === previous.source_type) ?? result.items[0] ?? null
          : result.items[0] ?? null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || requestNumber !== pageRequestRef.current) return;
        const message = translatePhrase(error instanceof ApiError ? error.message : "暂时无法读取材料目录，请确认本地 API 和材料分析迁移状态。");
        setPage({ kind: "error", message });
      });
    return () => controller.abort();
  }, [symbol, sourceType, analysisStatus, offset, refreshKey]);

  useEffect(() => {
    if (!materialFocus) return;
    const controller = new AbortController();
    const requestNumber = ++pageRequestRef.current;
    setFocusMessage(null);
    async function locateFocusedMaterial() {
      try {
        let total = 0;
        for (let pageOffset = 0; pageOffset === 0 || pageOffset < total; pageOffset += 10) {
          const result = await getMaterialLibrary(symbol, { sourceType: materialFocus!.source_type, offset: pageOffset, signal: controller.signal });
          total = result.total;
          const match = result.items.find((item) => item.source_type === materialFocus!.source_type && item.source_id === materialFocus!.source_id);
          if (controller.signal.aborted || requestNumber !== pageRequestRef.current) return;
          if (match) {
            setPage({ kind: "ready", items: result.items, total });
            setOffset(pageOffset);
            setSelected(match);
            setFocusAnalysisId(materialFocus!.analysis_id);
            setMaterialFocus(null);
            return;
          }
          if (pageOffset + 10 >= total) break;
        }
        if (!controller.signal.aborted && requestNumber === pageRequestRef.current) {
          setFocusMessage(translatePhrase("简报引用的材料当前不在目录中，仍可从预测版本返回查看。"));
          setMaterialFocus(null);
        }
      } catch (error) {
        if (!controller.signal.aborted && requestNumber === pageRequestRef.current) {
          setFocusMessage(translatePhrase(error instanceof ApiError ? error.message : "无法定位简报引用的材料。"));
          setMaterialFocus(null);
        }
      }
    }
    void locateFocusedMaterial();
    return () => controller.abort();
  }, [materialFocus, symbol]);

  const refresh = useCallback(() => { setRefreshKey((current) => current + 1); }, []);
  const scan = useCallback(async () => {
    setScanState({ kind: "running" });
    try {
      const result = await onScan();
      refresh();
      setScanState({ kind: "success", message: `${t("扫描完成")}：${t("发现")} ${result.discovered_count} ${t("份")}，${t("新增")} ${result.created_count} ${t("份")}。` });
    } catch (error: unknown) {
      setScanState({ kind: "error", message: translatePhrase(error instanceof ApiError ? error.message : error instanceof Error ? error.message : "扫描 SEC 资料失败。") });
    }
  }, [onScan, refresh, t]);

  return <main className="workspace material-library" aria-labelledby="workspace-title">
    <header className="workspace-heading">
      <div><p className="eyebrow">{symbol} / {t("研究材料")}</p><h1 id="workspace-title">{<Tx text={"材料库"} />}</h1></div>
      <div className="material-tools"><button className="secondary-action" type="button" disabled={scanState.kind === "running"} onClick={() => { void scan(); }}>{scanState.kind === "running" ? t("正在扫描…") : t("扫描 SEC 资料")}</button><span>{<Tx text={"每页 10 条 · 分析版本与原文分开保存"} />}</span></div>
    </header>
    {scanState.kind !== "idle" && scanState.kind !== "running" && <p className={`action-notice ${scanState.kind}`} role={scanState.kind === "error" ? "alert" : "status"}>{scanState.message}</p>}
    <section className="material-library-panel">
      {focusMessage && <p className="material-error" role="status">{focusMessage}</p>}
      <div className="material-filterbar">
        <label>{<Tx text={"来源"} />}<select value={sourceType} onChange={(event) => { setMaterialFocus(null); setFocusAnalysisId(null); setSourceType(event.target.value as MaterialSourceType | "all"); setOffset(0); }}><option value="all">{<Tx text={"全部来源"} />}</option><option value="official_filing">{<Tx text={"SEC 官方"} />}</option><option value="uploaded_media">{<Tx text={"上传材料"} />}</option></select></label>
        <label>{<Tx text={"分析状态"} />}<select value={analysisStatus} onChange={(event) => { setMaterialFocus(null); setFocusAnalysisId(null); setAnalysisStatus(event.target.value); setOffset(0); }}><option value="all">{<Tx text={"全部状态"} />}</option><option value="not_started">{<Tx text={"待分析"} />}</option><option value="queued">{<Tx text={"排队中"} />}</option><option value="running">{<Tx text={"分析中"} />}</option><option value="succeeded">{<Tx text={"已分析"} />}</option><option value="failed">{<Tx text={"最近失败"} />}</option><option value="blocked_data">{<Tx text={"缺少正文"} />}</option></select></label>
        <span className="material-count">{page.kind === "ready" ? `${page.total} ${t("份材料")}` : ""}</span>
      </div>
      <div className="material-library-grid">
        <section className="material-list" aria-label={`${symbol} ${t("的研究材料")}`}>
          {page.kind === "loading" && <p className="material-empty" role="status">{<Tx text={"正在读取材料…"} />}</p>}
          {page.kind === "error" && <div className="material-empty" role="alert"><p>{page.message}</p><button className="secondary-action" onClick={refresh}>{<Tx text={"重试"} />}</button></div>}
          {page.kind === "ready" && page.items.length === 0 && <p className="material-empty">{<Tx text={"没有匹配材料。可在证据页上传媒体材料，或扫描 SEC 资料。"} />}</p>}
          {page.kind === "ready" && page.items.map((item) => <button key={`${item.source_type}:${item.source_id}`} type="button" className={`material-row ${selected?.source_id === item.source_id && selected.source_type === item.source_type ? "is-selected" : ""}`} onClick={() => { setFocusAnalysisId(null); setSelected(item); }}>
            <span className="material-row-top"><strong>{item.title}</strong><span className={`material-status status-${item.analysis_status}`}>{translatePhrase(STATUS_LABEL[item.analysis_status])}</span></span>
            <span className="material-row-meta">{t(item.source_type === "official_filing" ? "SEC 官方" : "上传材料")} · {t("发布")} {formatDate(item.published_at)}</span>
            <span className="material-row-meta">{item.latest_analysis_version_no ? `${t("AI 分析")} v${item.latest_analysis_version_no}` : t("尚无 AI 分析记录")}</span>
          </button>)}
          {page.kind === "ready" && <div className="material-pagination"><button className="secondary-action" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 10))}>{<Tx text={"上一页"} />}</button><span>{page.total ? `${offset + 1}–${Math.min(offset + 10, page.total)} / ${page.total}` : `0 ${t("条")}`}</span><button className="secondary-action" disabled={offset + 10 >= page.total} onClick={() => setOffset(offset + 10)}>{<Tx text={"下一页"} />}</button></div>}
        </section>
        <div className="material-detail-pane">
          {selected ? <MaterialAnalysisDetail key={`${symbol}:${selected.source_type}:${selected.source_id}`} material={selected} initialAnalysisId={focusAnalysisId} onUpdated={refresh} /> : <div className="material-empty material-detail-empty">{<Tx text={"选择一份材料查看分析与原文。"} />}</div>}
        </div>
      </div>
    </section>
  </main>;
}

function formatDate(value: string | null) {
  if (!value) return getLocalePreference() === "en-US" ? "Unknown" : "未知";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value.slice(0, 10) : formatLocalizedDate(date, getLocalePreference(), "UTC");
}
