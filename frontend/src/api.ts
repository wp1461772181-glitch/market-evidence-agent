import type { DashboardResponse, EvidenceRevision, EvidenceRevisionInventory, EvidenceRevisionRequest, FilingContent, FilingInventory, FilingReview, FilingScanResult, ForecastRunResult, UploadedEvidence, UploadedEvidenceInventory, V2EvaluationResponse, V2ForecastJob, V2ForecastRoots, V2MonitorStatus, V2SourceRef, V2Timeline, V2VersionDetail, V2Workspace } from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

export async function getDashboard(symbol: string, signal: AbortSignal): Promise<DashboardResponse> {
  const timeout = AbortSignal.timeout(12_000);
  let response: Response;
  try {
    response = await fetch(`/api/dashboard/${encodeURIComponent(symbol)}`, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.any([signal, timeout]),
    });
  } catch (error) {
    if (timeout.aborted) throw new ApiError("读取超过 12 秒，请确认本地 API 已启动后重试。");
    throw error;
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : `请求失败（${response.status}）`;
    throw new ApiError(detail, response.status);
  }
  if (!isDashboard(payload)) throw new ApiError("服务返回了无法识别的数据格式。");
  return payload;
}

export async function getFilingInventory(symbol: string, signal: AbortSignal): Promise<FilingInventory> {
  return requestJson<FilingInventory>(`/api/filing-inventories/${encodeURIComponent(symbol)}`, { signal });
}

export async function scanOfficialFilings(symbol: string): Promise<FilingScanResult> {
  return requestJson<FilingScanResult>(`/api/filing-inventories/${encodeURIComponent(symbol)}/scan`, { method: "POST" });
}

export async function fetchFilingContent(symbol: string, accessionNumber: string): Promise<FilingContent> {
  return requestJson<FilingContent>(`/api/filing-inventories/${encodeURIComponent(symbol)}/${encodeURIComponent(accessionNumber)}/fetch`, { method: "POST" });
}

export async function reviewFiling(symbol: string, accessionNumber: string, decision: "accepted" | "rejected", note: string): Promise<FilingReview> {
  return requestJson<FilingReview>(`/api/filing-inventories/${encodeURIComponent(symbol)}/${encodeURIComponent(accessionNumber)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, note }),
  });
}

export async function createForecastRun(symbol: string): Promise<ForecastRunResult> {
  return requestJson<ForecastRunResult>("/api/forecast-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
}

export async function getUploadedEvidence(symbol: string, signal?: AbortSignal): Promise<UploadedEvidenceInventory> {
  return requestJson<UploadedEvidenceInventory>(`/api/uploaded-evidence/${encodeURIComponent(symbol)}`, { signal });
}

export async function uploadEvidence(
  symbol: string,
  input: {
    file: File;
    title: string;
    sourceUrl: string;
    publishedAt: string;
    credibilityStars: number;
    credibilityReason: string;
    impactSeverity: "low" | "medium" | "high";
  },
): Promise<UploadedEvidence> {
  const form = new FormData();
  form.set("file", input.file);
  form.set("title", input.title);
  form.set("source_url", input.sourceUrl);
  form.set("published_at", input.publishedAt);
  form.set("credibility_stars", String(input.credibilityStars));
  form.set("credibility_reason", input.credibilityReason);
  form.set("impact_severity", input.impactSeverity);
  return requestJson<UploadedEvidence>(`/api/uploaded-evidence/${encodeURIComponent(symbol)}`, { method: "POST", body: form });
}

export async function getEvidenceRevisions(symbol: string, signal?: AbortSignal): Promise<EvidenceRevisionInventory> {
  return requestJson<EvidenceRevisionInventory>(`/api/evidence-revisions/${encodeURIComponent(symbol)}`, { signal });
}

export async function createEvidenceRevision(symbol: string, input: EvidenceRevisionRequest): Promise<EvidenceRevision> {
  return requestJson<EvidenceRevision>(`/api/evidence-revisions/${encodeURIComponent(symbol)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function getV2Workspace(symbol: string, signal?: AbortSignal): Promise<V2Workspace> {
  return requestJson<V2Workspace>(`/api/v2/stocks/${encodeURIComponent(symbol)}/workspace`, { signal });
}

export async function getV2Job(jobId: string, signal?: AbortSignal): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>(`/api/v2/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export async function getV2Timeline(rootId: string, signal?: AbortSignal): Promise<V2Timeline> {
  return requestJson<V2Timeline>(`/api/v2/forecast-roots/${encodeURIComponent(rootId)}/timeline`, { signal });
}

export async function getV2ForecastRoots(symbol: string, signal?: AbortSignal): Promise<V2ForecastRoots> {
  return requestJson<V2ForecastRoots>(`/api/v2/stocks/${encodeURIComponent(symbol)}/forecast-roots`, { signal });
}

export async function getV2MonitorStatus(signal?: AbortSignal): Promise<V2MonitorStatus> {
  return requestJson<V2MonitorStatus>("/api/v2/monitor/status", { signal });
}

export async function getV2Evaluations(symbol: string, signal?: AbortSignal): Promise<V2EvaluationResponse> {
  return requestJson<V2EvaluationResponse>(`/api/v2/evaluations?symbol=${encodeURIComponent(symbol)}`, { signal });
}

export async function getV2ForecastVersion(versionId: string, signal?: AbortSignal): Promise<V2VersionDetail> {
  return requestJson<V2VersionDetail>(`/api/v2/forecast-versions/${encodeURIComponent(versionId)}`, { signal });
}

export async function createV2ForecastJob(symbol: string, sourceRefs: V2SourceRef[] = []): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>("/api/v2/forecast-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ symbol, kind: "new", source_refs: sourceRefs }),
  });
}

export async function createV2ManualRevisionJob(versionId: string, sourceRefs: V2SourceRef[]): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>(`/api/v2/forecast-versions/${encodeURIComponent(versionId)}/revision-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ source_refs: sourceRefs }),
  });
}

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { headers: { Accept: "application/json", ...init.headers }, ...init });
  } catch (error) {
    throw error;
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw apiErrorFromPayload(payload, response.status);
  return payload as T;
}

function apiErrorFromPayload(payload: unknown, status: number): ApiError {
  const detail = typeof payload === "object" && payload !== null && "detail" in payload
    ? String((payload as { detail: unknown }).detail)
    : `请求失败（${status}）`;
  return new ApiError(detail, status);
}

function isDashboard(value: unknown): value is DashboardResponse {
  return typeof value === "object" && value !== null
    && "symbol" in value && "snapshots" in value && Array.isArray((value as { snapshots: unknown }).snapshots)
    && "refresh_reports" in value && Array.isArray((value as { refresh_reports: unknown }).refresh_reports);
}
