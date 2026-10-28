export declare const ORB_COLORS: string[];
export declare const BRAND: "finbot";
export type ParsedAvatar = { kind: "orb"; color: string; brand: boolean } | { kind: "text"; text: string };
export declare function parseAvatar(avatar: string | null | undefined, name?: string): ParsedAvatar;
export declare function orbAvatar(color: string): string;
export type Point = { x: number; y: number };
export declare function gazeOffset(center: Point, pointer: Point | null, options?: { max?: number; k?: number }): Point;
export type GazeHub = { register(element: Element, apply: (offset: Point) => void): () => void; size(): number };
export declare function createGazeHub(env?: unknown, options?: { idleMs?: number; max?: number; k?: number }): GazeHub;
