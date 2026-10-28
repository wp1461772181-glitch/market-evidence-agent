import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_LAYOUT, addCard, botSymbol, cleanLayout, loadLayout, moveCard, pinCardSymbol, priceSummary, removeCard, resetLayout, saveLayout, storeBotSymbol, symbolChoices } from "../src/workspace-cards.mjs";

const memory = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; };

test("six default cards; edit, save, reload and reset per bot", () => {
  const s = memory();
  assert.deepEqual(loadLayout("b1", s).map((x) => x.card), ["price", "forecast", "materials", "filings", "confirm", "routines"]);
  let layout = loadLayout("b1", s);
  layout = removeCard(layout, layout.findIndex((x) => x.card === "filings"));
  layout = addCard(layout, "brief");
  layout = moveCard(layout, layout.length - 1, -1);
  layout = pinCardSymbol(layout, 0, "nvda");
  saveLayout("b1", layout, s);
  const again = loadLayout("b1", s);
  assert.deepEqual(again.map((x) => x.card), ["price", "forecast", "materials", "confirm", "brief", "routines"]);
  assert.equal(again[0].symbol, "NVDA");
  assert.equal(loadLayout("b2", s).length, DEFAULT_LAYOUT.length);
  assert.equal(resetLayout("b1", s).length, 6);
  assert.equal(loadLayout("b1", s)[3].card, "filings");
});

test("a stored layout is cleaned: unknown cards and bad symbols dropped", () => {
  assert.deepEqual(cleanLayout([{ card: "price", symbol: "<x>" }, { card: "nope" }, { card: "brief", size: "l" }]), [{ card: "price" }, { card: "brief", size: "l" }]);
  assert.equal(cleanLayout("x"), null);
  assert.deepEqual(moveCard([{ card: "price" }], 0, -1), [{ card: "price" }]);
});

test("current stock is per bot: picked › default company › first focus › page", () => {
  const s = memory();
  const apple = { id: "apple", focus: { companies: [{ symbol: "AAPL" }] } };
  assert.equal(botSymbol(apple, "TSLA", s), "AAPL");
  assert.equal(botSymbol({ id: "any", focus: { companies: [] } }, "TSLA", s), "TSLA");
  storeBotSymbol("apple", "msft", s);
  assert.equal(botSymbol(apple, "TSLA", s), "MSFT");
  assert.equal(storeBotSymbol("apple", "bad symbol", s), null);
  assert.deepEqual(symbolChoices(apple, ["NVDA", "AAPL"], "MSFT"), ["MSFT", "AAPL", "NVDA"]);
});

test("price summary is facts only", () => {
  assert.equal(priceSummary([]), null);
  const p = priceSummary([{ trading_date: "2026-10-26", close: 100 }, { trading_date: "2026-10-27", close: 102 }]);
  assert.deepEqual(p, { date: "2026-10-27", close: 102, change: 2, pct: 2 });
});
