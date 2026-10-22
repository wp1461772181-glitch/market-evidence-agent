import test from "node:test";
import assert from "node:assert/strict";

import { FLOW_LABELS, cancelRunning, describeOutput, flowRows, formatSeconds, nodeTitle, normalizeTrace, stepCount, upsertNode } from "../src/turn-flow.mjs";

/** A finished node with the fields every rule reads, so each test only states what it is about. */
function flowNode(id, kind, patch = {}) {
  return { id, kind, status: "done", after: [], start_ms: 0, output: null, ...patch };
}

test("upsertNode replaces by id and keeps order", () => {
  const nodes = normalizeTrace([
    { id: "n1", kind: "input", output: { question: "苹果最近一期营收是多少" } },
    { id: "n2", kind: "resolve" },
  ]);
  const running = upsertNode(nodes, { id: "n1", kind: "input", status: "running", output: { question: "苹果最近一期营收是多少" } });
  assert.deepEqual(running.map(node => node.id), ["n1", "n2"]);
  assert.equal(running[0].status, "running");
  assert.equal(running[0].output.question, "苹果最近一期营收是多少");
  assert.equal(nodes[0].status, "done", "the array handed in is not changed");
  const appended = upsertNode(nodes, { id: "n3", kind: "tool", name: "query_financials" });
  assert.deepEqual(appended.map(node => node.id), ["n1", "n2", "n3"]);
  assert.equal(appended[2].name, "query_financials");
  assert.equal(upsertNode(nodes, null), nodes);
  assert.equal(upsertNode(nodes, { kind: "input" }), nodes);
  assert.equal(upsertNode(undefined, { id: "n1", kind: "input" }), undefined);
});

test("normalizeTrace drops malformed nodes", () => {
  const nodes = normalizeTrace([
    { id: "n1", kind: "input", after: "nope", status: "surprising", start_ms: 5, output: { question: "苹果最近一期营收是多少" } },
    7,
    { id: 2, kind: "resolve" },
    { id: "n2" },
    { id: "n3", kind: "tool", name: "search_knowledge", status: "running" },
    { id: "n4", kind: "tool", name: "search_knowledge", status: "cancelled" },
  ]);
  assert.deepEqual(nodes.map(node => node.id), ["n1", "n3", "n4"]);
  assert.deepEqual(nodes[0].after, []);
  assert.equal(nodes[0].status, "done");
  assert.equal(nodes[0].start_ms, 5);
  assert.equal(nodes[1].name, "search_knowledge");
  assert.equal(nodes[1].status, "running");
  assert.equal(nodes[2].status, "cancelled");
  assert.deepEqual(normalizeTrace("nope"), []);
  assert.deepEqual(normalizeTrace(undefined), []);
});

test("flowRows nests tools under the step that called them", () => {
  const nodes = normalizeTrace([
    { id: "n1", kind: "input" },
    { id: "n2", kind: "resolve" },
    { id: "n3", kind: "classify" },
    { id: "n4", kind: "tool", name: "query_financials", after: ["n3"] },
    { id: "n5", kind: "tool", name: "search_knowledge", after: ["n3"] },
    { id: "n6", kind: "decide", step: 1, after: ["n4", "n5"] },
  ]);
  const rows = flowRows(nodes);
  assert.deepEqual(rows.map(row => row.node.id), ["n1", "n2", "n3", "n4", "n5", "n6"]);
  assert.deepEqual(rows.map(row => row.depth), [0, 0, 0, 1, 1, 0]);
  assert.deepEqual(rows.map(row => row.branch), ["none", "none", "none", "first", "last", "none"]);

  const single = flowRows(normalizeTrace([
    { id: "n1", kind: "resolve" },
    { id: "n2", kind: "tool", name: "query_financials", after: ["n1"] },
    { id: "n3", kind: "verify", after: ["n2"] },
  ]));
  assert.deepEqual(single.map(row => [row.node.id, row.depth, row.branch]), [
    ["n1", 0, "none"],
    ["n2", 1, "only"],
    ["n3", 0, "none"],
  ]);

  const three = flowRows(normalizeTrace([
    { id: "n1", kind: "decide" },
    { id: "n2", kind: "tool", name: "query_financials", after: ["n1"] },
    { id: "n3", kind: "tool", name: "query_filings", after: ["n1"] },
    { id: "n4", kind: "tool", name: "search_knowledge", after: ["n1"] },
  ]));
  assert.deepEqual(three.map(row => row.branch), ["none", "first", "middle", "last"]);

  const orphan = flowRows(normalizeTrace([
    { id: "n1", kind: "resolve" },
    { id: "n2", kind: "tool", name: "query_financials", after: ["missing"] },
  ]));
  assert.deepEqual(orphan.map(row => [row.node.id, row.depth, row.branch]), [
    ["n1", 0, "none"],
    ["n2", 0, "none"],
  ]);
});

test("describeOutput shows the canonical question and the referents", () => {
  assert.deepEqual(describeOutput(flowNode("n2", "resolve", {
    output: { canonical_question: "AAPL 最近一期财报的营收", relationship: "follow_up", referents: ["它", "那家公司"], passed: true, skipped: false },
  })), [
    { label: "规范问题", value: "AAPL 最近一期财报的营收" },
    { label: "指代", value: "它、那家公司" },
  ]);
  assert.deepEqual(describeOutput(flowNode("n3", "resolve", { output: { passed: false } })), [
    { label: "校验", value: "未通过", translate: true },
  ]);
  assert.deepEqual(describeOutput(flowNode("n4", "resolve", { output: { passed: false, rejected_reason: "指代不明" } })), [
    { label: "校验", value: "指代不明" },
  ]);
  assert.deepEqual(describeOutput(flowNode("n5", "input", { output: { question: "苹果最近一期营收是多少" } })), [
    { label: "问题", value: "苹果最近一期营收是多少" },
  ]);
});

test("describeOutput shows a failed step's error", () => {
  assert.deepEqual(describeOutput(flowNode("n6", "decide", { status: "failed", output: { error: "provider_auth_failed" } })), [
    { label: "失败原因", value: "provider_auth_failed" },
  ]);
  assert.deepEqual(describeOutput(flowNode("n7", "verify", { output: { error: "not_finished", status: "failed", citations: 2 } })), [
    { label: "失败原因", value: "not_finished" },
  ]);
  assert.deepEqual(describeOutput(flowNode("n8", "verify", { status: "running", output: null })), []);
});

test("describeOutput translates the category", () => {
  const rows = describeOutput(flowNode("n9", "classify", { output: {
    category: "D", symbol: "AAPL", company: "Apple", metrics: ["营收", "每股收益"],
    period: "最新一期", days_back: 30, requested_actions: ["add_to_research_queue", "unmapped_action"],
  } }));
  assert.deepEqual(rows, [
    { label: "类别", value: "执行操作或系统任务", translate: true },
    { label: "公司", value: "Apple AAPL" },
    { label: "指标", value: "营收、每股收益" },
    { label: "期间", value: "最新一期" },
    { label: "新闻时间窗（天）", value: "30" },
    { label: "要求的操作", value: "加入研究队列、unmapped action" },
  ]);
  assert.deepEqual(describeOutput(flowNode("n10", "classify", { output: { category: "A", company: "Apple" } }))[0], {
    label: "类别", value: "查资料作答", translate: true,
  });
  assert.deepEqual(describeOutput(flowNode("n11", "classify", { output: { company: "Apple" } })), [
    { label: "公司", value: "Apple" },
  ]);
});

test("formatSeconds rounds the way the header shows it", () => {
  assert.equal(formatSeconds(3200), "3.2");
  assert.equal(formatSeconds(999), "1.0");
  assert.equal(formatSeconds(0), "0.0");
  assert.equal(formatSeconds(14200), "14");
  assert.equal(formatSeconds(-200), "0.0");
  assert.equal(formatSeconds(Number.NaN), "0.0");
  assert.equal(formatSeconds(undefined), "0.0");
});

test("cancelRunning stops only the running nodes", () => {
  const nodes = normalizeTrace([
    { id: "n1", kind: "input", status: "done" },
    { id: "n2", kind: "resolve", status: "running" },
    { id: "n3", kind: "tool", name: "query_financials", status: "running" },
    { id: "n4", kind: "output", status: "done" },
  ]);
  const cancelled = cancelRunning(nodes, "cancelled");
  assert.notEqual(cancelled, nodes);
  assert.deepEqual(cancelled.map(node => node.status), ["done", "cancelled", "cancelled", "done"]);
  assert.deepEqual(nodes.map(node => node.status), ["done", "running", "running", "done"]);
  assert.deepEqual(cancelRunning(nodes, "failed").map(node => node.status), ["done", "failed", "failed", "done"]);
});

test("stepCount leaves out the question and the answer", () => {
  const nodes = normalizeTrace([
    { id: "n1", kind: "input" },
    { id: "n2", kind: "resolve" },
    { id: "n3", kind: "classify" },
    { id: "n4", kind: "tool", name: "query_financials", after: ["n3"] },
    { id: "n5", kind: "decide", step: 1, after: ["n4"] },
    { id: "n6", kind: "verify", after: ["n5"] },
    { id: "n7", kind: "output", after: ["n6"] },
    { id: "n8", kind: "ask", after: ["n5"] },
  ]);
  assert.equal(stepCount(nodes), 5);
  assert.equal(stepCount([]), 0);
});

test("nodeTitle names the step and numbers a decision", () => {
  assert.deepEqual(nodeTitle(flowNode("n1", "tool", { name: "query_financials" })), { label: "已披露财务数字", suffix: "" });
  assert.deepEqual(nodeTitle(flowNode("n2", "decide", { step: 2 })), { label: "模型决策", suffix: "#2" });
  assert.deepEqual(nodeTitle(flowNode("n3", "repair", { step: 1 })), { label: "修复回答", suffix: "#1" });
  assert.deepEqual(nodeTitle(flowNode("n4", "decide")), { label: "模型决策", suffix: "" });
  assert.deepEqual(nodeTitle(flowNode("n5", "output")), { label: "输出回答", suffix: "" });
  for (const kind of ["input", "consent", "resolve", "classify", "general", "out_of_scope", "decide", "closing", "verify", "repair", "ask", "output"]) {
    assert.equal(typeof FLOW_LABELS[kind], "string", kind + " needs a label");
    assert.notEqual(FLOW_LABELS[kind], "", kind + " needs a non-empty label");
  }
});
