import test from "node:test";
import assert from "node:assert/strict";
import { apiErrorMessage } from "../src/api-errors.mjs";

test("owner write denial asks for personal login and explicit retry", () => {
  assert.match(apiErrorMessage(null, 401, { ownerWrite: true }), /登录个人账户/);
  assert.match(apiErrorMessage(null, 401, { ownerWrite: true }), /手动重试/);
});

test("non-owner 401 keeps a credential-neutral classification", () => {
  assert.match(apiErrorMessage({ detail: "owner_only" }, 401), /身份验证/);
  assert.doesNotMatch(apiErrorMessage({ detail: "owner_only" }, 401), /站点所有者/);
});
