export function apiErrorMessage(payload, status, { ownerWrite = false } = {}) {
  const rawDetail = typeof payload === "object" && payload !== null && "detail" in payload ? payload.detail : null;
  if (status === 401 && ownerWrite) return "请登录个人账户后，手动重试刚才未完成的操作。";
  if (status === 401) return "请求未通过身份验证，请检查对应的登录或服务凭据。";
  if (typeof rawDetail === "object" && rawDetail !== null && "message" in rawDetail) return String(rawDetail.message);
  return typeof rawDetail === "string" ? rawDetail : `请求失败（${status}）`;
}
