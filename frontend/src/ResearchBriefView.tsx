import type { ResearchBrief, ResearchEvidencePointer, ResearchMaterialReference, V2Probabilities } from "./types";

export function ResearchBriefView({
  brief,
  probabilities,
  modelManifest,
  onOpenAnalysis,
}: {
  brief: ResearchBrief;
  probabilities: V2Probabilities | null;
  modelManifest: Record<string, unknown>;
  onOpenAnalysis: (material: ResearchMaterialReference) => void;
}) {
  const target = brief.target_contract;
  const market = brief.market_summary;
  const provider = record(modelManifest.decision_provider);
  const confidence = provider.confidence;
  return <section className="research-brief" aria-labelledby="research-brief-title">
    <header className="research-brief-header"><div><p className="section-label">DeepSeek · 综合材料与行情</p><h2 id="research-brief-title">股票研究简报</h2><p>{brief.symbol} · 截止 {formatTime(brief.decision_at)}</p></div><span className={`brief-quality quality-${brief.input_quality.status}`}>{qualityLabel(brief.input_quality.status)} · 输入质量</span></header>

    <section className="brief-market" aria-label="固定目标与市场快照">
      <div><small>固定目标日</small><strong>{text(target.target_end_date) ?? "未记录"}</strong></div>
      <div><small>原始锚点收盘</small><strong>{money(target.anchor_close)}</strong></div>
      <div><small>最近收盘</small><strong>{money(market.latest_close)}</strong></div>
      <div><small>行情截止</small><strong>{formatTime(text(market.as_of))}</strong></div>
      <div><small>近 5 个交易日</small><strong>{percent(market.return_5_sessions)}</strong></div>
      <div><small>近 20 个交易日</small><strong>{percent(market.return_20_sessions)}</strong></div>
      <div><small>20 日波动</small><strong>{percent(market.volatility_20_sessions)}</strong></div>
      <div><small>距目标剩余交易日</small><strong>{numberText(market.remaining_sessions)}</strong></div>
    </section>

    {probabilities ? <section className="brief-jev" aria-label="Jev 实验性概率">
      <div className="brief-jev-heading"><div><p className="section-label">OpenRouter · Jev</p><h3>实验性三分类判断</h3></div><strong>未校准</strong></div>
      <p>以下是 Jev 对固定目标合同的实验性输出，不代表已验证概率。DeepSeek 负责整理研究材料与简报，不生成这些数值。</p>
      <div className="brief-jev-probabilities"><Probability name="看涨" value={probabilities.bullish} tone="bullish" /><Probability name="中性" value={probabilities.neutral} tone="neutral" /><Probability name="看跌" value={probabilities.bearish} tone="bearish" /></div>
      <small>模型 {text(provider.actual_model) ?? "Jev"}{text(provider.question_version) ? ` · ${text(provider.question_version)}` : ""}{typeof confidence === "number" ? ` · 分布集中度 ${Math.round(confidence * 100)}%（不是准确率）` : ""}</small>
    </section> : <p className="brief-no-probability">该版本为仅研究模式，没有保存 Jev 数值判断；页面不会回填旧版概率。</p>}

    {brief.input_quality.reasons.length > 0 && <ul className="brief-quality-reasons">{brief.input_quality.reasons.map((reason, index) => <li key={`${reason}:${index}`}>{reason}</li>)}</ul>}

    <section className="brief-materials"><div className="brief-section-heading"><div><p className="section-label">分析版本引用</p><h3>纳入简报的材料</h3></div><span>{brief.material_refs.length} 份</span></div>
      {brief.material_refs.length ? <div className="brief-material-list">{brief.material_refs.map((material) => <article key={material.analysis_id}>
        <div><strong>{material.title || material.source_type}</strong><small>{material.source_type === "official_filing" ? "SEC 官方来源" : "用户上传材料"} · 发布 {formatTime(material.published_at)} · 观察 {formatTime(material.observed_at)}</small><small>{reviewText(material.review_status)}{material.user_rating_label ? ` · ${material.user_rating_label}` : ""}{material.coverage_incomplete || material.truncated ? " · 仅覆盖部分材料" : ""}</small></div>
        <button type="button" onClick={() => onOpenAnalysis(material)}>查看这版分析 ↗</button>
      </article>)}</div> : <p className="brief-empty">此简报没有引用已成功分析的材料。</p>}
    </section>

    <div className="brief-sections">
      <BriefList title="新增事实" rows={brief.new_facts} text={(row) => row.statement} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} />
      <BriefList title="潜在利好" rows={brief.supporting} text={(row) => row.statement} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} />
      <BriefList title="潜在利空 / 反证" rows={brief.counter} text={(row) => row.statement} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} />
      <BriefList title="持续背景" rows={brief.background} text={(row) => `${row.statement}（持续原因：${row.continuing_reason}）`} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} />
      <BriefList title="来源冲突" rows={brief.conflicts} text={(row) => row.description} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} />
      <BriefList title="未知问题" rows={brief.unknowns} text={(row) => `${row.question}：${row.reason}`} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} />
      <BriefList title="版本变化" rows={brief.changes} text={(row) => `${changeLabel(row.change_type)}：${row.description}`} materials={brief.material_refs} onOpenAnalysis={onOpenAnalysis} />
    </div>
    {brief.omitted.length > 0 && <details className="brief-omitted"><summary>未纳入材料（{brief.omitted.length}）</summary><ul>{brief.omitted.map((row, index) => <li key={`${row.source_type}:${row.source_id}:${index}`}>{row.source_type} · {row.reason}</li>)}</ul></details>}
  </section>;
}

function BriefList<T extends { citations: ResearchEvidencePointer[] }>({
  title, rows, text: getText, materials, onOpenAnalysis,
}: {
  title: string;
  rows: T[];
  text: (row: T) => string;
  materials: ResearchMaterialReference[];
  onOpenAnalysis: (material: ResearchMaterialReference) => void;
}) {
  if (rows.length === 0) return null;
  const byId = new Map(materials.map((material) => [material.analysis_id, material]));
  return <section className="brief-section"><h3>{title}<span>{rows.length}</span></h3><ul>{rows.map((row, index) => <li key={`${getText(row)}:${index}`}><p>{getText(row)}</p><div className="brief-citation-links">{row.citations.map((citation, citationIndex) => {
    const material = byId.get(citation.analysis_id);
    return material ? <button type="button" key={`${citation.analysis_id}:${citation.section}:${citation.item_id}:${citationIndex}`} onClick={() => onOpenAnalysis(material)}>{material.title || material.source_type} · {sectionText(citation.section)} {citation.item_id} ↗</button> : null;
  })}</div></li>)}</ul></section>;
}

function Probability({ name, value, tone }: { name: string; value: number; tone: "bullish" | "neutral" | "bearish" }) {
  return <div className={`brief-probability probability-${tone}`}><span>{name}</span><strong>{Math.round(value * 100)}%</strong><i style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></div>;
}

function formatTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(date);
}
function text(value: unknown): string | null { return typeof value === "string" ? value : null; }
function numberText(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? String(value) : "—"; }
function money(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "—"; }
function percent(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—"; }
function qualityLabel(value: ResearchBrief["input_quality"]["status"]) { return value === "ready" ? "可用" : value === "limited" ? "受限" : "不足"; }
function reviewText(value: string) { return value === "accepted" ? "已人工接受" : value === "rejected" ? "已驳回" : "待核验"; }
function sectionText(value: ResearchEvidencePointer["section"]) { return ({ facts: "事实", supporting: "利好", counter: "利空", uncertainties: "不确定项" })[value]; }
function changeLabel(value: string) { return ({ added: "新增", withdrawn: "撤回", modified: "修订", source_status_changed: "来源状态变化" })[value] ?? value; }
function record(value: unknown): Record<string, unknown> { return typeof value === "object" && value !== null ? value as Record<string, unknown> : {}; }
