import assert from "node:assert/strict";
import test from "node:test";
import { librarySymbol, materialHref, materialLinkFromHash, usableFocus } from "../src/material-focus.mjs";

test("a save reply's link names the stock and the row", () => {
  assert.deepEqual(materialLinkFromHash("#materials?symbol=aapl&focus=6f1c2d3e-0000-4000-8000-000000000001"),
    { symbol: "AAPL", source_type: "uploaded_media", source_id: "6f1c2d3e-0000-4000-8000-000000000001" });
  assert.deepEqual(materialLinkFromHash("#materials?symbol=BRK.B"), { symbol: "BRK.B", source_type: "uploaded_media", source_id: null });
  assert.deepEqual(materialLinkFromHash("#materials?symbol=AAPL&focus=6f1c2d3e-0000-4000-8000-000000000002&type=official_filing"),
    { symbol: "AAPL", source_type: "official_filing", source_id: "6f1c2d3e-0000-4000-8000-000000000002" });
  assert.equal(materialLinkFromHash("#materials?symbol=AAPL&focus=6f1c2d3e-0000-4000-8000-000000000002&type=web").source_type, "uploaded_media");
  assert.equal(materialLinkFromHash("#materials"), null);
  assert.equal(materialLinkFromHash("#materials?symbol=<script>"), null);
  assert.equal(materialLinkFromHash("#agent"), null);
});

test("全部股票 sends no symbol; 当前股票 sends the current one", () => {
  assert.equal(librarySymbol("all", "AAPL"), "");
  assert.equal(librarySymbol("current", "AAPL"), "AAPL");
});

test("a focus without an analysis still locates the material", () => {
  assert.deepEqual(usableFocus({ symbol: "AAPL", source_type: "uploaded_media", source_id: "x1" }, "AAPL"),
    { symbol: "AAPL", source_type: "uploaded_media", source_id: "x1", analysis_id: null });
  assert.equal(usableFocus({ symbol: "MSFT", source_type: "uploaded_media", source_id: "x1" }, "AAPL"), null);
  assert.equal(usableFocus({ symbol: "AAPL", source_type: "web", source_id: "x1" }, "AAPL"), null);
});

// 工作区 → 资料库, as the app runs it: the card's link, App's hash handler, MaterialLibrary's stored focus,
// then the catalogue paged with the focus's source_type (what /api/v3/materials filters on).
const CATALOGUE = [
  ...Array.from({ length: 13 }, (_, n) => ({ symbol: "AAPL", source_type: "uploaded_media", source_id: `0000000${n}-aaaa-4000-8000-000000000000`.slice(-36), title: `upload ${n}` })),
  { symbol: "AAPL", source_type: "official_filing", source_id: "6f1c2d3e-0000-4000-8000-0000000000aa", title: "AAPL 10-Q" },
];

function locate(focus) {
  // MaterialLibrary's loop: 10 per page, filtered by the focus's type, until the row or the end.
  const rows = CATALOGUE.filter((row) => row.symbol === focus.symbol && row.source_type === focus.source_type);
  for (let offset = 0; offset === 0 || offset < rows.length; offset += 10) {
    const match = rows.slice(offset, offset + 10).find((row) => row.source_id === focus.source_id);
    if (match) return match;
  }
  return null;  // → 「…不在目录」
}

function openFromWorkspace(symbol, item, href = materialHref(symbol, item)) {
  const link = materialLinkFromHash(href);
  const stored = JSON.parse(JSON.stringify(link));  // sessionStorage round trip
  return locate(usableFocus(stored, link.symbol));
}

test("a filing opened from the 工作区 card is found in 资料库", () => {
  const filing = CATALOGUE.at(-1);
  assert.equal(openFromWorkspace("AAPL", filing), filing);
  // The link before 5d458b4 named no type: the filing was looked for among uploads and not found.
  assert.equal(openFromWorkspace("AAPL", filing, `#materials?symbol=AAPL&focus=${filing.source_id}`), null);
});

test("an upload on the second page is still found", () => {
  const upload = CATALOGUE[12];
  assert.equal(openFromWorkspace("AAPL", upload), upload);
});
