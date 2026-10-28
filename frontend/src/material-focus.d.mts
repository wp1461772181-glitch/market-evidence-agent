export declare const MATERIAL_FOCUS_KEY: string;
export type MaterialLink = { symbol: string; source_type: "official_filing" | "uploaded_media"; source_id: string | null };
export declare function materialHref(symbol: string, item: { source_type: string; source_id: string }): string;
export declare function materialLinkFromHash(hash: string): MaterialLink | null;
export declare function librarySymbol(scope: "current" | "all", symbol: string): string;
export type UsableFocus = { symbol: string; source_type: "official_filing" | "uploaded_media"; source_id: string; analysis_id: string | null };
export declare function usableFocus(value: unknown, symbol: string): UsableFocus | null;
