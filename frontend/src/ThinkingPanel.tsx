import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useLocale } from "./i18n";
import { describeOutput, flowRows, formatSeconds, nodeTitle, normalizeTrace, stepCount } from "./turn-flow.mjs";

const TICK_MS = 200;

/** The thinking time and, on demand, the graph of the turn.  The server sends a node when a step
 *  starts and again when it ends, so the graph grows while the answer is still being written; an old
 *  answer has no trace, and then this panel shows only its time (or nothing at all). */
export function ThinkingPanel({ trace, thinkingMs, startedAt, running }: { trace?: unknown; thinkingMs?: number; startedAt?: number; running: boolean }) {
  const { t } = useLocale();
  const nodes = useMemo(() => normalizeTrace(trace), [trace]);
  const [now, setNow] = useState(() => Date.now());
  const [open, setOpen] = useState(running);
  const [toggled, setToggled] = useState(false);
  const wasRunning = useRef(running);
  const bodyId = useId();

  // The header counts while the turn runs, and stops when done brings the number the server kept.
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [running]);

  // A running turn shows its graph; once it finishes the graph folds away, unless the reader has
  // already opened or closed it themselves.  Then their choice stays.
  useEffect(() => {
    if (wasRunning.current === running) return;
    wasRunning.current = running;
    if (!running && !toggled) setOpen(false);
  }, [running, toggled]);

  if (!running && nodes.length === 0 && typeof thinkingMs !== "number") return null;

  const elapsed = typeof startedAt === "number" && Number.isFinite(startedAt) ? Math.max(0, now - startedAt) : 0;
  const seconds = formatSeconds(running ? elapsed : typeof thinkingMs === "number" ? thinkingMs : 0);
  const runningNode = running ? [...nodes].reverse().find(node => node.status === "running") : undefined;
  const heading = running
    ? `${t("思考中")} · ${seconds} ${t("秒")}${runningNode ? ` · ${t(nodeTitle(runningNode).label)}` : ""}`
    : `${t("已思考")} ${seconds} ${t("秒")}${nodes.length > 0 ? ` · ${stepCount(nodes)} ${t("步")}` : ""}`;
  const expandable = nodes.length > 0;
  const caret = <span className="thinking-chevron" aria-hidden="true">›</span>;
  return <section className={`thinking-panel${running ? " is-running" : ""}`}>
    {expandable
      ? <button type="button" className="thinking-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => { setToggled(true); setOpen(value => !value); }}>
        <span className="thinking-pulse" aria-hidden="true" />
        <span className="thinking-title">{heading}</span>
        {caret}
      </button>
      : <p className="thinking-toggle is-static"><span className="thinking-pulse" aria-hidden="true" /><span className="thinking-title">{heading}</span></p>}
    {expandable && open && <ol id={bodyId} className="turn-flow" aria-live="polite">
      {flowRows(nodes).map(row => {
        const item = row.node;
        const step = nodeTitle(item);
        const duration = item.status === "running" ? "…" : typeof item.ms === "number" ? `${formatSeconds(item.ms)} ${t("秒")}` : "";
        const lines = describeOutput(item);
        return <li key={item.id} className={`flow-node kind-${item.kind} status-${item.status} depth-${row.depth} branch-${row.branch}`}>
          <span className="flow-dot" aria-hidden="true" />
          <div className="flow-card">
            <div className="flow-head">
              <span className="flow-name">{t(step.label)}{step.suffix}</span>
              {duration && <span className="flow-time">{duration}</span>}
            </div>
            {lines.length > 0 && <dl className="flow-data">
              {lines.map((line, index) => <div className="flow-line" key={index}>
                <dt>{t(line.label)}</dt>
                <dd>{line.translate ? t(line.value) : line.value}</dd>
              </div>)}
            </dl>}
            {item.output && <details className="flow-raw">
              <summary>{t("原始数据")}</summary>
              <pre>{JSON.stringify(item.output, null, 2)}</pre>
            </details>}
          </div>
        </li>;
      })}
    </ol>}
  </section>;
}
