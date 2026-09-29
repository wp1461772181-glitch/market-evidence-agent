import type { DashboardResponse, EvidenceRevision, EvidenceRevisionInventory, EvidenceRevisionRequest, FilingContent, FilingInventory, FilingReview, FilingScanResult, ForecastRunResult, JevLearningStatus, LocalizationResult, MaterialAnalysisHistory, MaterialAnalysisJob, MaterialAnalysisVersion, MaterialItem, MaterialLibraryResponse, MaterialOriginal, MaterialSourceType, PriceHistory, UploadedEvidence, UploadedEvidenceInventory, V2EvaluationResponse, V2ForecastJob, V2ForecastRoots, V2MonitorStatus, V2SourceRef, V2Timeline, V2VersionDetail, V2Workspace } from "./types";

const configuredApiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
const accessKeyStorageKey = "market-evidence-agent-api-access-key";
let pendingAccessKeyRequest: { promise: Promise<string | null>; resolve: (value: string | null) => void; invalidPreviousKey: boolean } | null = null;

function apiUrl(path: string): string {
  if (!configuredApiBaseUrl) return path;
  const backendPath = path.startsWith("/api/") ? path.slice("/api".length) : path;
  return `${configuredApiBaseUrl}${backendPath}`;
}

async function fetchApi(path: string, init: RequestInit = {}): Promise<Response> {
  const storedKey = typeof window === "undefined" ? null : window.sessionStorage.getItem(accessKeyStorageKey);
  const send = (key: string | null, retryAfterAccessPrompt = false) => {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (key) headers.set("X-App-Access-Key", key);
    else headers.delete("X-App-Access-Key");
    return fetch(apiUrl(path), {
      ...init,
      ...(retryAfterAccessPrompt ? { signal: AbortSignal.timeout(30_000) } : {}),
      headers,
    });
  };

  let response = await send(storedKey);
  if (response.status !== 401 || typeof window === "undefined") return response;

  if (storedKey) {
    window.sessionStorage.removeItem(accessKeyStorageKey);
  }
  const accessKey = await requestAccessKey(storedKey !== null);
  if (!accessKey) return response;

  response = await send(accessKey, true);
  if (response.status === 401) {
    window.sessionStorage.removeItem(accessKeyStorageKey);
    window.dispatchEvent(new Event("market-evidence-api-access-invalid"));
  }
  return response;
}

export function submitApiAccessKey(value: string | null): void {
  const accessKey = value?.trim() || null;
  if (accessKey) window.sessionStorage.setItem(accessKeyStorageKey, accessKey);
  const pending = pendingAccessKeyRequest;
  pendingAccessKeyRequest = null;
  pending?.resolve(accessKey);
}

export function getPendingAccessKeyRequest(): boolean | null {
  return pendingAccessKeyRequest?.invalidPreviousKey ?? null;
}

function requestAccessKey(invalidPreviousKey = false): Promise<string | null> {
  if (pendingAccessKeyRequest) return pendingAccessKeyRequest.promise;
  let resolve!: (value: string | null) => void;
  const promise = new Promise<string | null>((finish) => { resolve = finish; });
  pendingAccessKeyRequest = { promise, resolve, invalidPreviousKey };
  window.dispatchEvent(new CustomEvent("market-evidence-api-access-required", { detail: invalidPreviousKey }));
  return promise;
}

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
    response = await fetchApi(`/api/dashboard/${encodeURIComponent(symbol)}`, {
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

export async function fetchFilingContent(symbol: string, accessionNumber: string, refresh = false): Promise<FilingContent> {
  const query = refresh ? "?refresh=true" : "";
  return requestJson<FilingContent>(`/api/filing-inventories/${encodeURIComponent(symbol)}/${encodeURIComponent(accessionNumber)}/fetch${query}`, { method: "POST" });
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

export async function getV2Prices(symbol: string, signal?: AbortSignal): Promise<{ symbol: string; price_history: PriceHistory }> {
  return requestJson<{ symbol: string; price_history: PriceHistory }>(`/api/v2/stocks/${encodeURIComponent(symbol)}/prices`, { signal });
}

export async function getMaterialLibrary(
  symbol: string,
  options: { sourceType?: MaterialSourceType | "all"; analysisStatus?: string; reviewStatus?: string; offset?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<MaterialLibraryResponse> {
  const params = new URLSearchParams({ symbol, limit: String(options.limit ?? 10), offset: String(options.offset ?? 0) });
  if (options.sourceType && options.sourceType !== "all") params.set("source_type", options.sourceType);
  if (options.analysisStatus && options.analysisStatus !== "all") params.set("analysis_status", options.analysisStatus);
  if (options.reviewStatus && options.reviewStatus !== "all") params.set("review_status", options.reviewStatus);
  return requestJson<MaterialLibraryResponse>(`/api/v3/materials?${params}`, { signal: options.signal });
}

export async function createMaterialAnalysisJob(
  sourceType: MaterialSourceType,
  sourceId: string,
  force = false,
): Promise<MaterialAnalysisJob> {
  const idempotencyKey = newIdempotencyKey();
  return requestJson<MaterialAnalysisJob>(`/api/v3/materials/${sourceType}/${encodeURIComponent(sourceId)}/analysis-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ idempotency_key: idempotencyKey, force }),
  });
}

export async function getMaterialAnalysisJob(jobId: string, signal?: AbortSignal): Promise<MaterialAnalysisJob> {
  return requestJson<MaterialAnalysisJob>(`/api/v3/material-analysis-jobs/${encodeURIComponent(jobId)}`, { signal });
}

export async function getMaterialAnalysisHistory(
  sourceType: MaterialSourceType,
  sourceId: string,
  signal?: AbortSignal,
): Promise<MaterialAnalysisHistory> {
  return requestJson<MaterialAnalysisHistory>(`/api/v3/materials/${sourceType}/${encodeURIComponent(sourceId)}/analyses`, { signal });
}

export async function getMaterialAnalysis(analysisId: string, signal?: AbortSignal): Promise<MaterialAnalysisVersion> {
  return requestJson<MaterialAnalysisVersion>(`/api/v3/material-analyses/${encodeURIComponent(analysisId)}`, { signal });
}

export async function localizeMaterialAnalysis(analysisId: string, signal?: AbortSignal): Promise<LocalizationResult> {
  return requestJson<LocalizationResult>(`/api/v3/material-analyses/${encodeURIComponent(analysisId)}/localization`, { method: "POST", signal });
}

export async function localizeForecastBrief(versionId: string, signal?: AbortSignal): Promise<LocalizationResult> {
  return requestJson<LocalizationResult>(`/api/v2/forecast-versions/${encodeURIComponent(versionId)}/brief-localization`, { method: "POST", signal });
}

export async function getMaterialOriginal(
  sourceType: MaterialSourceType,
  sourceId: string,
  signal?: AbortSignal,
): Promise<MaterialOriginal> {
  return requestJson<MaterialOriginal>(`/api/v3/materials/${sourceType}/${encodeURIComponent(sourceId)}/original`, { signal });
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

export async function getJevLearningStatus(symbol: string, signal?: AbortSignal): Promise<JevLearningStatus> {
  return requestJson<JevLearningStatus>(`/api/v2/jev-learning/status?symbol=${encodeURIComponent(symbol)}`, { signal });
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

export async function createV2HistoricalReplayJob(symbol: string, decisionDate: string): Promise<V2ForecastJob> {
  return requestJson<V2ForecastJob>("/api/v2/historical-replays", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ symbol, decision_date: decisionDate }),
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
    response = await fetchApi(url, init);
  } catch (error) {
    throw error;
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw apiErrorFromPayload(payload, response.status);
  return payload as T;
}

function apiErrorFromPayload(payload: unknown, status: number): ApiError {
  const rawDetail = typeof payload === "object" && payload !== null && "detail" in payload
    ? (payload as { detail: unknown }).detail
    : null;
  const detail = typeof rawDetail === "object" && rawDetail !== null && "message" in rawDetail
    ? String((rawDetail as { message: unknown }).message)
    : typeof rawDetail === "string" ? rawDetail : `请求失败（${status}）`;
  return new ApiError(detail, status);
}

function isDashboard(value: unknown): value is DashboardResponse {
  return typeof value === "object" && value !== null
    && "symbol" in value && "snapshots" in value && Array.isArray((value as { snapshots: unknown }).snapshots)
    && "refresh_reports" in value && Array.isArray((value as { refresh_reports: unknown }).refresh_reports);
}
