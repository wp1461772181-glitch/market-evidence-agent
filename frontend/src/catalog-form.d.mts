import type { CatalogModel, CatalogProbe, CatalogEntryInput } from "./api";
export type CatalogEntryDraft = { name: string; display_name: string; provider: string; model: string; base_url: string; key_mode: "vault" | "env"; api_key: string; key_env: string; is_default: boolean; editing: CatalogModel | null };
export function entryMoved(entry: CatalogEntryDraft | null): boolean;
export function probeFor(entry: CatalogEntryDraft | null, options?: { forDiscovery?: boolean }): CatalogProbe | null;
export function catalogName(source: string, taken?: string[]): string;
export function saveBody(entry: CatalogEntryDraft): CatalogEntryInput;
export function entryProblem(entry: CatalogEntryDraft | null): string;
export function siblingsToInert(main: Element | null | undefined, modal: Element | null | undefined): Element[];
