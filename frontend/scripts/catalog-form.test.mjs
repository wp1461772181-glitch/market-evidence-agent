import assert from "node:assert/strict";
import test from "node:test";
import { catalogName, entryMoved, entryProblem, probeFor, saveBody, siblingsToInert } from "../src/catalog-form.mjs";

const blank = (patch = {}) => ({ name: "main", display_name: "", provider: "deepseek", model: "deepseek-chat", base_url: "https://api.deepseek.com",
  key_mode: "vault", api_key: "", key_env: "", is_default: false, editing: null, ...patch });
const stored = { name: "main", provider: "deepseek", base_url: "https://api.deepseek.com", key_source: "vault", key_status: "configured" };

test("a check uses the typed key, the environment variable, or the edited entry's stored key", () => {
  assert.deepEqual(probeFor(blank({ api_key: "sk-0123456789" })), { provider: "deepseek", base_url: "https://api.deepseek.com", model: "deepseek-chat", api_key: "sk-0123456789" });
  assert.equal(probeFor(blank({ key_mode: "env", key_env: "DEEPSEEK_API_KEY" })).key_env, "DEEPSEEK_API_KEY");
  assert.equal(probeFor(blank({ editing: stored })).name, "main");
  assert.equal(probeFor(blank()), null);
  // A stored key never follows the entry to another provider or endpoint.
  const moved = blank({ editing: stored, provider: "custom", base_url: "https://llm.example.com/v1" });
  assert.equal(entryMoved(moved), true);
  assert.equal(probeFor(moved), null);
  assert.equal(entryProblem(moved), "换了服务或接口地址，需要重新填写 key。");
  // OpenRouter lists its models without a key; testing still needs one.
  assert.deepEqual(probeFor(blank({ provider: "openrouter", base_url: "https://openrouter.ai/api/v1" }), { forDiscovery: true }).provider, "openrouter");
  assert.equal(probeFor(blank({ provider: "openrouter" })), null);
});

test("测试并保存 explains what is missing, and saves a key only when one was given", () => {
  assert.equal(entryProblem(blank({ model: " " })), "请填写 Model ID。");
  assert.equal(entryProblem(blank({ name: "Main Model", api_key: "sk-0123456789" })), "名字只能用小写字母、数字和 ._-。");
  assert.equal(entryProblem(blank({ provider: "custom", base_url: "", api_key: "sk-0123456789" })), "自定义服务必须填写 Base URL。");
  assert.equal(entryProblem(blank({ api_key: "sk-0123456789" })), "");
  assert.deepEqual(saveBody(blank({ display_name: " 主力 ", api_key: "sk-0123456789", is_default: true })),
    { display_name: "主力", provider: "deepseek", model: "deepseek-chat", base_url: "https://api.deepseek.com", api_key: "sk-0123456789", is_default: true });
  assert.equal("api_key" in saveBody(blank({ editing: stored })), false);
});

test("a catalog name is derived from what was typed and never collides", () => {
  assert.equal(catalogName("DeepSeek Chat"), "deepseek-chat");
  assert.equal(catalogName("deepseek/deepseek-v3.2:free"), "deepseek-deepseek-v3.2-free");
  assert.equal(catalogName("财报助手"), "model");
  assert.equal(catalogName("main", ["main", "main-2"]), "main-3");
});

test("the modal's own ancestor is never made inert", () => {
  const modal = {}, nav = { contains: (x) => x === nav }, card = { contains: (x) => x === card || x === modal };
  assert.deepEqual(siblingsToInert({ children: [nav, card] }, modal), [nav]);
  assert.deepEqual(siblingsToInert(null, modal), []);
});
