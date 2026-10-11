export function apiErrorMessage(payload, status, { ownerWrite = false, hasOwnerKey = false } = {}) {
  const rawDetail = typeof payload === "object" && payload !== null && "detail" in payload ? payload.detail : null;
  if (status === 401 && ownerWrite) return hasOwnerKey
    ? "操作口令无效。聊天个人身份与站点操作授权相互独立，请更新操作口令后手动重试。"
    : "尚未设置操作口令。聊天个人身份与站点操作授权相互独立，请填写操作口令后手动重试。";
  if (status === 401) return "请求未通过身份验证，请检查对应的登录或服务凭据。";
  if (typeof rawDetail === "object" && rawDetail !== null && "message" in rawDetail) return String(rawDetail.message);
  return typeof rawDetail === "string" ? rawDetail : `请求失败（${status}）`;
}
