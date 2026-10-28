// 资料库 links and scope (UX batch 2, P1).  A confirmed agent save replies with
// #materials?symbol=AAPL&focus=<uploaded_media id>; opening it lands on that row.
// The workspace card also links SEC filings, so it adds &type=official_filing.
export const MATERIAL_FOCUS_KEY = "market-evidence-agent:material-analysis-focus";

const TICKER = /^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$/;
const ID = /^[0-9a-f-]{8,64}$/i;

/** {symbol, source_type, source_id} from a #materials?symbol=&focus=[&type=] hash, or null. */
export function materialLinkFromHash(hash) {
  const value = String(hash || "").replace(/^#/, "");
  if (!value.startsWith("materials?")) return null;
  const params = new URLSearchParams(value.slice("materials?".length));
  const symbol = String(params.get("symbol") || "").trim().toUpperCase();
  const focus = String(params.get("focus") || "").trim();
  if (!TICKER.test(symbol)) return null;
  const source_type = params.get("type") === "official_filing" ? "official_filing" : "uploaded_media";
  return { symbol, source_type, source_id: ID.test(focus) ? focus : null };
}

/** The symbol the library request filters on: none for 全部股票. */
export function librarySymbol(scope, symbol) {
  return scope === "all" ? "" : symbol;
}

/** A stored focus is usable when it names a material; an analysis id is optional (a fresh save has none). */
export function usableFocus(value, symbol) {
  if (!value || typeof value !== "object") return null;
  if (value.symbol !== symbol) return null;
  if (value.source_type !== "official_filing" && value.source_type !== "uploaded_media") return null;
  if (typeof value.source_id !== "string" || !value.source_id) return null;
  return { symbol, source_type: value.source_type, source_id: value.source_id,
           analysis_id: typeof value.analysis_id === "string" ? value.analysis_id : null };
}
