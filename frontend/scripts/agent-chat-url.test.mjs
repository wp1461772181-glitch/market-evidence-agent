import test from "node:test";
import assert from "node:assert/strict";
import { agentChatUrl, rewriteAgentChatProxyPath } from "../src/agent-chat-url.mjs";

test("hello and messages map to canonical backend paths through local proxy and external origin", () => {
  for (const action of ["hello", "messages"]) {
    const localRequest = agentChatUrl(action);
    assert.equal(localRequest, `/api/agent-chat/${action}`);
    assert.equal(rewriteAgentChatProxyPath(localRequest), `/agent-chat/${action}`);
    assert.equal(agentChatUrl(action, "https://api.example.test/"), `https://api.example.test/agent-chat/${action}`);
  }
});

test("proxy rewrite leaves unrelated API routes under their existing rule", () => {
  assert.equal(rewriteAgentChatProxyPath("/api/dashboard/AAPL"), "/api/dashboard/AAPL");
});
