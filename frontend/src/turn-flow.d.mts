export type FlowStatus = "running" | "done" | "warn" | "failed" | "cancelled";
export type FlowNode = {
  id: string; kind: string; name?: string | null; step?: number | null; status: FlowStatus;
  after: string[]; start_ms: number; ms?: number | null; output?: Record<string, unknown> | null;
};
export type FlowRow = { node: FlowNode; depth: 0 | 1; branch: "none" | "only" | "first" | "middle" | "last" };
export type FlowLine = { label: string; value: string; translate?: boolean };
export const FLOW_LABELS: Record<string, string>;
export const CODE_LABELS: Record<string, string>;
export function normalizeTrace(value: unknown): FlowNode[];
export function upsertNode(nodes: FlowNode[], incoming: unknown): FlowNode[];
export function cancelRunning(nodes: FlowNode[], status: "cancelled" | "failed"): FlowNode[];
export function flowRows(nodes: FlowNode[]): FlowRow[];
export function nodeTitle(node: FlowNode): { label: string; suffix: string };
export function describeOutput(node: FlowNode): FlowLine[];
export function formatSeconds(ms: number): string;
export function stepCount(nodes: FlowNode[]): number;
