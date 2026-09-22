import type { DashboardResponse } from "./types";

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

function isDashboard(value: unknown): value is DashboardResponse {
  return typeof value === "object" && value !== null
    && "symbol" in value && "snapshots" in value && Array.isArray((value as { snapshots: unknown }).snapshots)
    && "refresh_reports" in value && Array.isArray((value as { refresh_reports: unknown }).refresh_reports);
}
