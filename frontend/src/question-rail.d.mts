export const QUESTION_RAIL_MIN_TURNS: number;
export function railShouldShow(turnCount: number): boolean;
export function markerProgress(index: number, focusIndex: number | null): number;
export function adjacentTurnIndex(tops: number[], viewportTop: number, direction: "next" | "previous", tolerance?: number): number | null;
export type BookmarkStorage = Pick<Storage, "getItem" | "setItem">;
export function readBookmarkedTurns(storage: BookmarkStorage, conversationId: string | null): Set<string>;
export function writeBookmarkedTurns(storage: BookmarkStorage, conversationId: string | null, turnIds: Set<string>): void;
