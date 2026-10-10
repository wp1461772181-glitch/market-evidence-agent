import test from "node:test";
import assert from "node:assert/strict";
import { AgentChatLifecycle, chatContext } from "../src/agentChatLifecycle.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

for (const transition of ["config", "cancel", "clear", "close"]) {
  test(`late success and failure after ${transition} cannot mutate current session`, async () => {
    const life = new AgentChatLifecycle();
    let session = ["new config/session"];
    const old = deferred();
    const token = life.begin(new AbortController());
    const callback = old.promise.then(
      value => { if (life.isCurrent(token)) session.push(value); },
      () => { if (life.isCurrent(token)) session.push("error"); },
    ).finally(() => { if (life.isCurrent(token)) session.push("finally"); });
    life.invalidate();
    old.resolve("stale hello or assistant");
    await callback;
    assert.deepEqual(session, ["new config/session"]);
  });
}

test("context retains recent complete rounds and current user, respecting UTF-8 body limits", () => {
  const history = [];
  for (let i = 0; i < 14; i++) history.push({ role: "user", content: `用户 ${i}` }, { role: "assistant", content: `答复 ${i}` });
  const current = { role: "user", content: "当前问题 中文" };
  const context = chatContext(history, current);
  assert.equal(context.at(-1), current);
  assert.ok(context.length <= 20);
  assert.ok(new TextEncoder().encode(JSON.stringify(context)).length <= 30000);
  assert.equal(context[0].content, "用户 5");
  assert.throws(() => chatContext([], { role: "user", content: "中文当前消息" }, { maxBytes: 10 }), /缩短/);
});
