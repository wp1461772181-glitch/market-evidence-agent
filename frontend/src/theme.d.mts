export type ThemeChoice = "light" | "dark" | "system";
export const THEME_KEY: string;
export const THEMES: ThemeChoice[];
export function storedTheme(storage?: Storage): ThemeChoice;
export function resolvedTheme(choice: ThemeChoice, prefersDark: boolean): "light" | "dark";
export function applyTheme(choice: ThemeChoice, root?: HTMLElement, media?: MediaQueryList): "light" | "dark";
export function saveTheme(choice: ThemeChoice, storage?: Storage): ThemeChoice;
