import test from "node:test";
import assert from "node:assert/strict";
import { apiErrorMessage } from "../src/api-errors.mjs";

test("owner write 401 distinguishes absent and rejected operation keys", () => {
  assert.match(apiErrorMessage(null, 401, { ownerWrite: true }), /尚未设置操作口令/);
  assert.match(apiErrorMessage(null, 401, { ownerWrite: true, hasOwnerKey: true }), /操作口令无效/);
});

test("non-owner 401 keeps a credential-neutral classification", () => {
  assert.match(apiErrorMessage({ detail: "owner_only" }, 401), /身份验证/);
  assert.doesNotMatch(apiErrorMessage({ detail: "owner_only" }, 401), /站点所有者/);
});
