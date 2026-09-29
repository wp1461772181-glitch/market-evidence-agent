import { Tx, useLocale } from "./i18n";
import type { ResearchBrief, ResearchMaterialReference } from "./types";

type FrozenEvidence = Record<string, unknown>;

type ProcessingStatus = {
  label: string;
  detail: string;
  tone: "included" | "omitted" | "candidate" | "unknown";
};

export function EvidenceProcessingSummary({
  evidenceManifest,
  brief,
}: {
  evidenceManifest: FrozenEvidence[];
  brief: ResearchBrief;
}) {
  const { t } = useLocale();
  const includedKeys = new Set(brief.material_refs.map(sourceKey));
  const includedCount = evidenceManifest.filter((item) => includedKeys.has(sourceKey(item))).length;
  const notIncludedCount = evidenceManifest.length - includedCount;

  return <section className="evidence-processing" aria-label={t("冻结候选与简报材料范围")}>
    <div className="brief-section-heading"><div><p className="section-label">{<Tx text={"本次预测的材料范围"} />}</p><h3>{<Tx text={"冻结候选不等于 Jev 实际输入"} />}</h3></div></div>
    <div className="evidence-processing-counts">
      <div><strong>{evidenceManifest.length}</strong><span>{<Tx text={"冻结候选"} />}</span></div>
      <div><strong>{includedCount}</strong><span>{<Tx text={"分析成功并进入简报"} />}</span></div>
      <div><strong>{notIncludedCount}</strong><span>{<Tx text={"未进入简报"} />}</span></div>
    </div>
      <p className="evidence-processing-note">{<Tx text={"冻结清单用于保存预测当时可用的来源快照。Jev（若本版启用）读取的是通过校验的研究简报及其引用，不会把冻结清单中的所有候选都直接当作输入。"} />}</p>
    <div className="evidence-processing-list">
      {evidenceManifest.length ? evidenceManifest.map((item, index) => {
        const ref = brief.material_refs.find((candidate) => sourceKey(candidate) === sourceKey(item));
        const status = processingStatus(item, brief, t);
        const sourceUrl = asText(item.source_url) ?? ref?.source_url ?? null;
        return <article key={`${sourceKey(item)}:${index}`}>
          <div className="evidence-processing-title"><strong>{evidenceTitle(item, ref)}</strong><span className={`evidence-processing-badge is-${status.tone}`}>{status.label}</span><small>{status.detail}</small></div>
          {sourceUrl && <a href={sourceUrl} target="_blank" rel="noopener noreferrer">{<Tx text={"打开原始来源 ↗"} />}</a>}
        </article>;
      }) : <p className="brief-empty">{<Tx text={"该版本没有冻结材料候选。"} />}</p>}
    </div>
  </section>;
}

export function processingStatus(item: FrozenEvidence, brief: ResearchBrief | null, t: (text: string) => string = (text) => text): ProcessingStatus {
  if (!brief) return { label: t("无简报状态记录"), detail: t("此版本没有保存研究简报，无法判断材料的分析与纳入结果。"), tone: "unknown" };

  const included = brief.material_refs.find((ref) => sourceKey(ref) === sourceKey(item));
  if (included) return { label: t("已分析 · 已进入简报"), detail: t("这份分析被 DeepSeek 简报引用；如本版启用 Jev，它属于 Jev 的简报输入。"), tone: "included" };

  const omitted = brief.omitted.find((row) => sourceKey(row) === sourceKey(item));
  if (!omitted) return { label: t("冻结候选 · 未进入材料分析"), detail: t("材料保存在预测快照中，但未进入本轮简报分析范围。"), tone: "candidate" };

  const reason = omitted.reason;
  if (reason === "analysis_unavailable") return { label: t("未取得成功分析"), detail: t("预测截止时没有可用的成功分析版本，因此未进入简报。"), tone: "omitted" };
  if (reason === "analysis_not_succeeded") return { label: t("材料分析未成功"), detail: t("本次材料分析没有成功完成，因此未进入简报。"), tone: "omitted" };
  if (reason === "new_material_limit") return { label: t("已分析 · 超出新增材料上限"), detail: t("分析已完成，但本轮简报的新增材料数量有限。"), tone: "omitted" };
  if (reason === "background_material_limit") return { label: t("已分析 · 超出背景材料上限"), detail: t("分析已完成，但本轮简报的背景材料数量有限。"), tone: "omitted" };
  if (reason === "duplicate_source_content") return { label: t("已分析 · 重复内容已去重"), detail: t("相同来源和内容只保留一个分析版本进入简报。"), tone: "omitted" };
  if (reason === "source_rejected") return { label: t("来源已驳回"), detail: t("该来源状态为驳回，本轮未纳入简报。"), tone: "omitted" };
  if (reason === "inactive_source") return { label: t("来源已失效"), detail: t("该来源已撤回或失效，本轮未纳入简报。"), tone: "omitted" };
  if (reason === "future_not_observable") return { label: t("晚于预测截止"), detail: t("该材料在预测决策时点尚不可观察，因此未纳入简报。"), tone: "omitted" };
  if (reason === "symbol_mismatch") return { label: t("股票代码不匹配"), detail: t("材料所属股票与本次预测不一致，因此未纳入简报。"), tone: "omitted" };
  return { label: t("未进入简报"), detail: `${t("处理原因")}: ${reason}`, tone: "omitted" };
}

export function sourceKey(item: { source_type?: unknown; source_id?: unknown }) {
  return `${String(item.source_type ?? "")}:${String(item.source_id ?? "")}`;
}

export function evidenceTitle(item: FrozenEvidence, ref?: ResearchMaterialReference) {
  if (ref?.title) return ref.title;
  const type = asText(item.source_type);
  const eventKey = asText(item.event_key);
  if (type === "official_filing") {
    const accession = eventKey?.split(":").at(-1);
    return accession ? `SEC 原文 · ${accession}` : "SEC 官方文件";
  }
  if (type === "uploaded_media") return `上传材料 · ${String(item.source_id ?? "").slice(0, 8)}`;
  return type ?? "研究来源";
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
