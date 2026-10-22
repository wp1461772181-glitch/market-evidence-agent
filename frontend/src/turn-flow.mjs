// The turn trace arrives as a flat list of nodes; what makes it readable is the graph.  Everything
// that decides what a node says lives here as pure functions, so the panel only renders and ticks.
import { toolLabel } from "./agent-tool-labels.mjs";

// Node kinds the backend emits (app/agent_workflow/trace.py).  An unknown kind keeps raw text.
export const FLOW_LABELS = {
  input: "接收问题",
  consent: "处理卡片回复",
  resolve: "指代消解",
  classify: "意图识别",
  general: "一般知识作答",
  out_of_scope: "超出范围说明",
  decide: "模型决策",
  closing: "收尾作答",
  verify: "证据核验",
  repair: "修复回答",
  ask: "等待补充",
  output: "输出回答",
};

const STATUSES = ["running", "done", "warn", "failed", "cancelled"];
const NOT_STEPS = ["input", "output", "ask"];
const NUMBERED_KINDS = ["decide", "repair"];

function text(value) {
  if (typeof value === "string") return value;
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

function textList(value) {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : [];
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// One wire node in, one node the panel can trust.  Anything the server did not shape is dropped or
// defaulted, so a truncated or older trace never crashes the graph.
export function normalizeTrace(value) {
  if (!Array.isArray(value)) return [];
  const nodes = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const id = typeof item.id === "string" ? item.id : "";
    if (!id || typeof item.kind !== "string") continue;
    nodes.push({
      id,
      kind: item.kind,
      name: typeof item.name === "string" ? item.name : null,
      step: finite(item.step),
      status: STATUSES.includes(item.status) ? item.status : "done",
      after: Array.isArray(item.after) ? item.after.filter(parent => typeof parent === "string") : [],
      start_ms: finite(item.start_ms) ?? 0,
      ms: finite(item.ms),
      output: item.output && typeof item.output === "object" && !Array.isArray(item.output) ? item.output : null,
    });
  }
  return nodes;
}

// A node is sent once when it starts and again when it ends; the second copy replaces the first in
// place, so the graph keeps its order while the node grows its output.
export function upsertNode(nodes, incoming) {
  if (!Array.isArray(nodes)) return nodes;
  const next = normalizeTrace([incoming])[0];
  if (!next) return nodes;
  const index = nodes.findIndex(existing => existing && existing.id === next.id);
  if (index === -1) return [...nodes, next];
  const copy = nodes.slice();
  copy[index] = next;
  return copy;
}

// A cancelled or failed turn must not leave a node spinning forever.
export function cancelRunning(nodes, status) {
  if (!Array.isArray(nodes)) return [];
  return nodes.map(existing => (existing && existing.status === "running" ? { ...existing, status } : existing));
}

// Tools are attached under the step that called them: parallel calls fan out as first/middle/last,
// and a tool with no parent we know is drawn on the main line instead of vanishing.
export function flowRows(nodes) {
  const all = Array.isArray(nodes) ? nodes.filter(Boolean) : [];
  const byId = new Map(all.map(existing => [existing.id, existing]));
  const children = new Map();
  const attached = new Set();
  for (const existing of all) {
    if (existing.kind !== "tool") continue;
    const parent = existing.after.find(id => byId.get(id) && byId.get(id).kind !== "tool");
    if (parent === undefined) continue;
    attached.add(existing.id);
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(existing);
  }
  const rows = [];
  for (const existing of all) {
    if (attached.has(existing.id)) continue;
    rows.push({ node: existing, depth: 0, branch: "none" });
    const kids = children.get(existing.id) ?? [];
    kids.forEach((kid, index) => {
      const branch = kids.length === 1 ? "only" : index === 0 ? "first" : index === kids.length - 1 ? "last" : "middle";
      rows.push({ node: kid, depth: 1, branch });
    });
  }
  return rows;
}

export function nodeTitle(value) {
  const kind = value && typeof value.kind === "string" ? value.kind : "";
  const label = kind === "tool" ? toolLabel(value && value.name) : FLOW_LABELS[kind] ?? kind;
  const step = value ? finite(value.step) : null;
  const numbered = NUMBERED_KINDS.includes(kind) && step !== null;
  return { label, suffix: numbered ? "#" + step : "" };
}

const CATEGORIES = { A: "查资料作答", B: "一般知识解释", C: "超出产品范围", D: "执行操作或系统任务" };
const SOURCES = {
  model_knowledge: "模型一般知识",
  model_scope_notice: "范围说明（模型补充）",
  fixed_scope_notice: "范围说明（固定文案）",
};
const OUTPUT_STATUSES = { succeeded: "成功", partially_succeeded: "部分完成", failed: "失败", waiting_for_input: "等待输入" };

// The data card under a node.  Labels are always i18n source phrases; translate marks a value that
// is one too.  An empty value means there is nothing to say, so the row is not drawn.
export function describeOutput(value) {
  const output = value && value.output && typeof value.output === "object" && !Array.isArray(value.output) ? value.output : null;
  if (!output) return [];
  const error = text(output.error);
  if (error) return [{ label: "失败原因", value: error }];
  const lines = [];
  const add = (label, shown, translate = false) => {
    const phrase = text(shown);
    if (!phrase) return;
    lines.push(translate ? { label, value: phrase, translate: true } : { label, value: phrase });
  };
  switch (value.kind) {
    case "input":
    case "ask":
      add("问题", output.question);
      break;
    case "consent":
      add("结果", output.outcome);
      break;
    case "resolve": {
      add("规范问题", output.canonical_question);
      add("指代", textList(output.referents).join("、"));
      if (output.passed === false) {
        add("校验", text(output.rejected_reason) || "未通过", !text(output.rejected_reason));
      }
      break;
    }
    case "classify": {
      const category = text(output.category);
      if (CATEGORIES[category]) add("类别", CATEGORIES[category], true);
      else add("类别", category);
      add("公司", [text(output.company), text(output.symbol)].filter(Boolean).join(" "));
      add("指标", textList(output.metrics).join("、"));
      add("期间", output.period);
      add("新闻时间窗（天）", output.days_back);
      add("要求的操作", textList(output.requested_actions).map(action => toolLabel(action)).filter(Boolean).join("、"));
      break;
    }
    case "decide": {
      const calls = textList(output.calls).map(call => toolLabel(call)).filter(Boolean);
      if (calls.length) add("调用", calls.join("、"));
      else add("决定", "开始作答", true);
      break;
    }
    case "tool": {
      const count = finite(output.count);
      if (output.ok === true) {
        if (count === null) add("结果", "已完成", true);
        else if (count > 0) add("命中条数", count);
        else add("结果", "没有匹配记录", true);
      } else {
        add("失败原因", output.error_code);
      }
      break;
    }
    case "closing":
      add("停止原因", output.stop_reason);
      break;
    case "verify":
    case "repair": {
      add("引用数", output.citations);
      const issues = textList(output.issues);
      if (issues.length === 0) add("结果", "核对通过", true);
      else add("未核对上", issues.join("、"));
      break;
    }
    case "general":
    case "out_of_scope": {
      const source = text(output.source);
      if (SOURCES[source]) add("来源", SOURCES[source], true);
      else add("来源", source);
      break;
    }
    case "output": {
      const status = text(output.status);
      if (OUTPUT_STATUSES[status]) add("状态", OUTPUT_STATUSES[status], true);
      else add("状态", status);
      add("引用数", output.citations);
      break;
    }
    default:
      break;
  }
  return lines;
}

// The header reads seconds the way a person counts them: one decimal under ten seconds, whole
// numbers after that.  Sub-second and missing values are shown as zero rather than as nothing.
export function formatSeconds(ms) {
  const value = typeof ms === "number" && Number.isFinite(ms) && ms > 0 ? ms : 0;
  return value < 10000 ? (value / 1000).toFixed(1) : String(Math.round(value / 1000));
}

// The question, the answer and a clarification request are turns of the conversation, not work the
// agent did, so they do not count as steps.
export function stepCount(nodes) {
  if (!Array.isArray(nodes)) return 0;
  return nodes.filter(existing => existing && typeof existing.kind === "string" && !NOT_STEPS.includes(existing.kind)).length;
}
