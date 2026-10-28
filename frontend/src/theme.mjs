// Light, dark or follow the system; dark until the owner picks (the Grok-like chat skin, grok.css).  Both themes use the same CSS variable names (theme.css).
export const THEME_KEY = "market-evidence-agent:theme:v1";
export const THEMES = ["light", "dark", "system"];

export function storedTheme(storage = globalThis.localStorage) {
  try { const value = storage?.getItem(THEME_KEY); return THEMES.includes(value) ? value : "dark"; } catch { return "dark"; }
}

export function resolvedTheme(choice, prefersDark) {
  return choice === "system" ? (prefersDark ? "dark" : "light") : choice === "dark" ? "dark" : "light";
}

export function applyTheme(choice, root = globalThis.document?.documentElement, media = globalThis.matchMedia?.("(prefers-color-scheme: dark)")) {
  const theme = resolvedTheme(choice, Boolean(media?.matches));
  if (root) root.dataset.theme = theme;
  return theme;
}

export function saveTheme(choice, storage = globalThis.localStorage) {
  if (!THEMES.includes(choice)) return storedTheme(storage);
  try { storage?.setItem(THEME_KEY, choice); } catch { /* optional preference */ }
  return choice;
}
