import { useEffect, useRef, useState } from "react";
import { getContextUsage, type ContextUsage } from "./api";
import { useLocale } from "./i18n";
import { barSegments, formatKb, formatTokens, meterTone } from "./context-usage.mjs";

const PART_LABEL: Record<string, string> = {
  system: "系统提示", tools: "工具定义", summary: "早前摘要", compressed: "半压历史", recent: "近期原文", free: "剩余空间",
};
const RING = 2 * Math.PI * 7;

/** The context the next message will carry, next to the model picker: how much of the budget is
 *  left, and on click which parts of it are used.  It hides itself when the numbers cannot be read. */
export function ContextMeter({ conversationId, refreshKey }: { conversationId: string | null; refreshKey: string }) {
  const { t } = useLocale();
  const [usage, setUsage] = useState<ContextUsage | null>(null);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    getContextUsage(conversationId, controller.signal)
      .then(next => { if (!controller.signal.aborted) setUsage(next); })
      .catch(() => { if (!controller.signal.aborted) setUsage(null); });
    return () => controller.abort();
  }, [conversationId, refreshKey]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => { if (root.current && !root.current.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  if (!usage) return null;
  const tone = meterTone(usage);
  const used = Math.min(1, Math.max(0, 1 - usage.remainingPercent / 100));
  return <div className="context-meter" ref={root}>
    <button type="button" className={`context-pill is-${tone}`} aria-expanded={open} aria-haspopup="dialog"
            title={t("上下文用量")} onClick={() => setOpen(value => !value)}>
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <circle cx="9" cy="9" r="7" className="context-ring-track" />
        <circle cx="9" cy="9" r="7" className="context-ring-used" strokeDasharray={`${(RING * used).toFixed(1)} ${RING.toFixed(1)}`} transform="rotate(-90 9 9)" />
      </svg>
      <span><span className="context-pill-label">{t("上下文剩余")} </span>{usage.remainingPercent}%</span>
    </button>
    {open && <div className="context-card" role="dialog" aria-label={t("上下文用量")}>
      <header>
        <strong>{t("上下文用量")}</strong>
        <span>{t("已用")} {formatKb(usage.usedBytes)} / {formatKb(usage.capacityBytes)}</span>
      </header>
      <div className="context-bar" role="img" aria-label={`${t("上下文剩余")} ${usage.remainingPercent}%`}>
        {barSegments(usage).map(segment => <i key={segment.key} className={`context-seg is-${segment.key}`} style={{ width: `${segment.percent}%` }} />)}
      </div>
      <ul className="context-parts">
        {usage.parts.map(part => <li key={part.key}>
          <i className={`context-dot is-${part.key}`} aria-hidden="true" />
          <span className="context-part-name">{t(PART_LABEL[part.key] ?? part.key)}
            {part.key === "summary" && part.segments ? <small> · {part.segments} {t("段摘要")}</small> : null}
            {(part.key === "compressed" || part.key === "recent") && part.turns ? <small> · {part.turns} {t("轮")}</small> : null}
          </span>
          <span className="context-part-size">{formatKb(part.bytes)} <small>≈ {formatTokens(part.tokens)} token</small></span>
        </li>)}
      </ul>
      <p className="context-note">{t("历史预算")} {usage.budgetKb} KB</p>
      {usage.compressing && <p className="context-note">{t("更早的内容已压缩：半压历史保留你的原话和答复的开头，更早的只剩摘要。")}</p>}
      {usage.hiddenTurns > 0 && <p className="context-note is-warn">{t("有更早的对话暂时不在上下文里，等待摘要。")}</p>}
      <p className="context-note">{t("“剩余”指本产品的上下文预算，不是模型窗口；token 为按 字节÷4 的估算。")}</p>
    </div>}
  </div>;
}
