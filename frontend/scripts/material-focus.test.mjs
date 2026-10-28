import assert from "node:assert/strict";
import test from "node:test";
import { librarySymbol, materialLinkFromHash, usableFocus } from "../src/material-focus.mjs";

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
