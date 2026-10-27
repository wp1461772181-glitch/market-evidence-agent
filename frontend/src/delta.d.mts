export type DeltaDirection = "up" | "down" | "flat";
export function deltaDirection(value: unknown): DeltaDirection;
export function deltaGlyph(value: unknown): "↑" | "↓" | "→";
export function formatDelta(value: unknown, options?: { digits?: number; scale?: number; suffix?: string }): string;
