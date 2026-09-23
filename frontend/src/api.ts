import type { DashboardResponse, FilingContent, FilingInventory, FilingReview, FilingScanResult, ForecastRunResult } from "./types";

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
