import { Tx } from "./i18n";
import { formatDateTime, useLocale } from "./i18n";
import type { ResearchBrief, ResearchEvidencePointer, ResearchMaterialReference, V2Probabilities } from "./types";
import { EvidenceProcessingSummary } from "./EvidenceProcessing";
import { useAiContentLocalization, withLocalizedFields } from "./useAiContentLocalization";

export function ResearchBriefView({
  brief,
  versionId,
  probabilities,
  modelManifest,
  evidenceManifest,
  onOpenAnalysis,
}: {
  brief: ResearchBrief;
  versionId: string;
  probabilities: V2Probabilities | null;
  modelManifest: Record<string, unknown>;
  evidenceManifest: Array<Record<string, unknown>>;
  onOpenAnalysis: (material: ResearchMaterialReference) => void;
}) {
  const { locale, t } = useLocale();
  const localization = useAiContentLocalization("forecast_brief", versionId);
  if (localization.kind === "ready") brief = withLocalizedFields(brief, localization.fields);
  const target = brief.target_contract;
  const market = brief.market_summary;
  const provider = record(modelManifest.decision_provider);
  const calibration = record(modelManifest.local_calibration);
  const rawProbabilities = probabilityVector(provider.raw_probabilities);
  const calibrated = calibration.status === "active";
  const confidence = provider.confidence;
  return <section className="research-brief" aria-labelledby="research-brief-title">
    <header className="research-brief-header"><div><p className="section-label">{brief.time_mode === "historical_research" ? t("历史回放 · 回填材料研究") : "DeepSeek · material analysis summary"}</p><h2 id="research-brief-title">{<Tx text={"股票研究简报"} />}</h2><p>{brief.symbol} · {t("截止")} {formatTime(brief.decision_at, locale)}</p></div><span className={`brief-quality quality-${brief.input_quality.status}`}>{qualityLabel(brief.input_quality.status, t)} · {t("输入质量")}</span></header>
    {locale === "en-US" && localization.kind === "loading" && <p className="localization-status" role="status">{t("Translating saved research brief…")}</p>}
    {locale === "en-US" && localization.kind === "error" && <p className="localization-status" role="status">{t("English translation is temporarily unavailable; showing the saved original.")}</p>}
    {brief.time_mode === "historical_research" && <p className="historical-replay-warning">{<Tx text={"这是使用现行流程对历史时点进行的回放；材料和行情可能在当时之后才被系统获取，结果属于历史研究，不计入真实前向表现。"} />}</p>}

    <section className="brief-market" aria-label={t("固定目标与市场快照")}>
      <div><small>{<Tx text={"固定目标日"} />}</small><strong>{text(target.target_end_date) ?? t("未记录")}</strong></div>
      <div><small>{<Tx text={"原始锚点收盘"} />}</small><strong>{money(target.anchor_close)}</strong></div>
      <div><small>{<Tx text={"最近收盘"} />}</small><strong>{money(market.latest_close)}</strong></div>
      <div><small>{<Tx text={"行情截止"} />}</small><strong>{formatTime(text(market.as_of), locale)}</strong></div>
      <div><small>{<Tx text={"近 5 个交易日"} />}</small><strong>{percent(market.return_5_sessions)}</strong></div>
      <div><small>{<Tx text={"近 20 个交易日"} />}</small><strong>{percent(market.return_20_sessions)}</strong></div>
      <div><small>{<Tx text={"20 日波动"} />}</small><strong>{percent(market.volatility_20_sessions)}</strong></div>
      <div><small>{<Tx text={"距目标剩余交易日"} />}</small><strong>{numberText(market.remaining_sessions)}</strong></div>
    </section>

    {probabilities ? <section className="brief-jev" aria-label={t("Jev 实验性概率")}>
      <div className="brief-jev-heading"><div><p className="section-label">OpenRouter · Jev</p><h3>{calibrated ? t("Jev 判断 · 本地校准") : t("Jev 原始三分类判断")}</h3></div><strong>{calibrated ? t("时间外验证通过") : t("未校准")}</strong></div>
      <p>{calibrated ? t("主结果经过本地校准器调整；原始 Jev 输出在下方保留以便比较。校准器不会更新 Jev 的内部参数。") : t("以下是 Jev 对固定目标合同的实验性原始输出，不代表已验证概率。DeepSeek 只分析单份材料；系统复用这些已保存的分析并在本地汇总简报。")}</p>
      <div className="brief-jev-probabilities"><Probability name={t("看涨")} value={probabilities.bullish} tone="bullish" /><Probability name={t("中性")} value={probabilities.neutral} tone="neutral" /><Probability name={t("看跌")} value={probabilities.bearish} tone="bearish" /></div>
      {calibrated && rawProbabilities && <details className="brief-raw-jev"><summary>{<Tx text={"查看校准前 Jev 原始输出"} />}</summary><div className="brief-jev-probabilities"><Probability name={t("看涨")} value={rawProbabilities.bullish} tone="bullish" /><Probability name={t("中性")} value={rawProbabilities.neutral} tone="neutral" /><Probability name={t("看跌")} value={rawProbabilities.bearish} tone="bearish" /></div></details>}
      <small>{t("模型")} {text(provider.actual_model) ?? "Jev"}{text(provider.question_version) ? ` · ${text(provider.question_version)}` : ""}{typeof confidence === "number" ? ` · ${t("分布集中度")} ${Math.round(confidence * 100)}%${locale === "en-US" ? ` (${t("不是准确率")})` : `（${t("不是准确率")}）`}` : ""}</small>
    </section> : <p className="brief-no-probability">{<Tx text={"该版本为仅研究模式，没有保存 Jev 数值判断；页面不会回填旧版概率。"} />}</p>}

    {brief.input_quality.reasons.length > 0 && <ul className="brief-quality-reasons">{brief.input_quality.reasons.map((reason, index) => <li key={`${reason}:${index}`}>{reason}</li>)}</ul>}

    <EvidenceProcessingSummary evidenceManifest={evidenceManifest} brief={brief} />

    <section className="brief-materials"><div className="brief-section-heading"><div><p className="section-label">{<Tx text={"分析版本引用"} />}</p><h3>{<Tx text={"纳入简报的材料"} />}</h3></div><span>{brief.material_refs.length} {locale === "en-US" ? t("份材料") : "份"}</span></div>
      {brief.material_refs.length ? <div className="brief-material-list">{brief.material_refs.map((material) => <article key={material.analysis_id}>
        <div><strong>{material.title || sourceTypeLabel(material.source_type, locale)}</strong><small>{sourceTypeLabel(material.source_type, locale)} · {t("发布时间")} {formatTime(material.published_at, locale)} · {t("观察时间")} {formatTime(material.observed_at, locale)}</small><small>{material.source_type === "uploaded_media" && material.user_rating_label ? `${material.user_rating_label} · ` : ""}{material.coverage_incomplete || material.truncated ? t("仅覆盖部分材料") : ""}</small>{material.analysis_summary && <small>{t("分析摘要")}: {material.analysis_summary}</small>}{material.key_numbers?.length ? <small title={material.key_numbers.flatMap((item) => item.citations.map((citation) => citation.quote)).join("\n")}>{t("关键数字")}: {material.key_numbers.map((item) => `${item.name} ${item.value_text}${item.period ? ` (${item.period})` : ""}`).join(" · ")}</small> : null}</div>
        <div className="brief-material-actions">{material.source_url && <a href={material.source_url} target="_blank" rel="noopener noreferrer">{<Tx text={"原始来源 ↗"} />}</a>}<button type="button" onClick={() => onOpenAnalysis(material)}>{<Tx text={"查看这版分析 ↗"} />}</button></div>
      </article>)}</div> : <p className="brief-empty">{<Tx text={"此简报没有引用已成功分析的材料。"} />}</p>}
    </section>

    <div className="brief-sections">
      <BriefList title={t("新增事实")} rows={brief.new_facts} text={(row) => row.statement} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} locale={locale} />
      <BriefList title={t("潜在利好")} rows={brief.supporting} text={(row) => row.statement} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} locale={locale} />
      <BriefList title={t("潜在利空 / 反证")} rows={brief.counter} text={(row) => row.statement} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} locale={locale} />
      <BriefList title={t("持续背景")} rows={brief.background} text={(row) => locale === "en-US" ? `${row.statement} (Continuing reason: ${row.continuing_reason})` : `${row.statement}（持续原因：${row.continuing_reason}）`} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} locale={locale} />
      <BriefList title={t("来源冲突")} rows={brief.conflicts} text={(row) => row.description} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} locale={locale} />
      <BriefList title={t("未知问题")} rows={brief.unknowns} text={(row) => locale === "en-US" ? `${row.question}: ${row.reason}` : `${row.question}：${row.reason}`} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} locale={locale} />
      <BriefList title={t("版本变化")} rows={brief.changes} text={(row) => `${changeLabel(row.change_type, t)}: ${row.description}`} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} locale={locale} />
    </div>
    {brief.omitted.length > 0 && <details className="brief-omitted"><summary>{t("未纳入材料")} ({brief.omitted.length})</summary><ul>{brief.omitted.map((row, index) => <li key={`${row.source_type}:${row.source_id}:${index}`}>{sourceTypeLabel(row.source_type, locale)} · {row.reason}</li>)}</ul></details>}
  </section>;
}

function BriefList<T extends { citations: ResearchEvidencePointer[] }>({
  title, rows, text: getText, materials, onOpenAnalysis, locale,
}: {
  title: string;
  rows: T[];
  text: (row: T) => string;
  materials: ResearchMaterialReference[];
  onOpenAnalysis: (material: ResearchMaterialReference) => void;
  locale: "zh-CN" | "en-US";
}) {
  if (rows.length === 0) return null;
  const byId = new Map(materials.map((material) => [material.analysis_id, material]));
  return <section className="brief-section"><h3>{title}<span>{rows.length}</span></h3><ul>{rows.map((row, index) => <li key={`${getText(row)}:${index}`}><p>{getText(row)}</p><div className="brief-citation-links">{row.citations.map((citation, citationIndex) => {
    const material = byId.get(citation.analysis_id);
    return material ? <button type="button" key={`${citation.analysis_id}:${citation.section}:${citation.item_id}:${citationIndex}`} onClick={() => onOpenAnalysis(material)}>{material.title || sourceTypeLabel(material.source_type, locale)} · {sectionText(citation.section, locale)} {citation.item_id} ↗</button> : null;
  })}</div></li>)}</ul></section>;
}

function Probability({ name, value, tone }: { name: string; value: number; tone: "bullish" | "neutral" | "bearish" }) {
  return <div className={`brief-probability probability-${tone}`}><span>{name}</span><strong>{Math.round(value * 100)}%</strong><i style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></div>;
}

function formatTime(value: string | null | undefined, locale: "zh-CN" | "en-US") {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : formatDateTime(date, locale, "UTC");
}
function text(value: unknown): string | null { return typeof value === "string" ? value : null; }
function numberText(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? String(value) : "—"; }
function money(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "—"; }
function percent(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—"; }
function qualityLabel(value: ResearchBrief["input_quality"]["status"], t: (text: string) => string) { return ({ ready: t("可用"), limited: t("受限"), insufficient: t("不足") })[value]; }
function sectionText(value: ResearchEvidencePointer["section"], locale: "zh-CN" | "en-US") { return (locale === "en-US" ? { facts: "facts", supporting: "positives", counter: "negatives", uncertainties: "uncertainties" } : { facts: "事实", supporting: "利好", counter: "利空", uncertainties: "不确定项" })[value]; }
function changeLabel(value: string, t: (text: string) => string) { return ({ added: t("新增"), withdrawn: t("撤回"), modified: t("修订"), source_status_changed: t("来源状态变化") } as Record<string, string>)[value] ?? value; }
function sourceTypeLabel(value: string, locale: "zh-CN" | "en-US") { return locale === "en-US" ? value === "official_filing" ? "SEC filing" : "Uploaded material" : value === "official_filing" ? "SEC 官方来源" : "用户上传材料"; }
function record(value: unknown): Record<string, unknown> { return typeof value === "object" && value !== null ? value as Record<string, unknown> : {}; }
function probabilityVector(value: unknown): V2Probabilities | null {
  const candidate = record(value);
  if ([candidate.bearish, candidate.neutral, candidate.bullish].every((item) => typeof item === "number" && Number.isFinite(item))) {
    return { bearish: candidate.bearish as number, neutral: candidate.neutral as number, bullish: candidate.bullish as number };
  }
  return null;
}
