import type { AgentCitation } from "./api";

export function citationOrdinal(citation: AgentCitation | null | undefined, index: number): number;
export function linkCitationMarkers(text: string | null | undefined, citations: AgentCitation[] | null | undefined): string;
