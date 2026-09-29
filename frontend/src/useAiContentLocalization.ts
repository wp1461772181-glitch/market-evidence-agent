import { useEffect, useState } from "react";
import { localizeForecastBrief, localizeMaterialAnalysis } from "./api";
import { useLocale } from "./i18n";
import type { LocalizationResult } from "./types";

export type LocalizableKind = LocalizationResult["content_kind"];
export type LocalizationState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; fields: Record<string, string> }
  | { kind: "error"; message: string };

/** Fetches the server-cached English display overlay for one immutable record. */
export function useAiContentLocalization(kind: LocalizableKind, contentId: string | null | undefined): LocalizationState {
  const { locale, t } = useLocale();
  const requestKey = locale === "en-US" && contentId ? `${kind}:${contentId}` : null;
  const [stored, setStored] = useState<{ key: string | null; state: LocalizationState }>({ key: null, state: { kind: "idle" } });

  useEffect(() => {
    if (!requestKey || !contentId) {
      setStored({ key: null, state: { kind: "idle" } });
      return;
    }
    const controller = new AbortController();
    setStored({ key: requestKey, state: { kind: "loading" } });
    const request = kind === "material_analysis"
      ? localizeMaterialAnalysis(contentId, controller.signal)
      : localizeForecastBrief(contentId, controller.signal);
    request.then((result) => {
      if (!controller.signal.aborted) setStored({ key: requestKey, state: { kind: "ready", fields: result.fields } });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        const message = error instanceof Error && error.message ? error.message : t("English translation is temporarily unavailable.");
        setStored({ key: requestKey, state: { kind: "error", message } });
      }
    });
    return () => controller.abort();
  }, [contentId, kind, locale, requestKey, t]);

  if (!requestKey) return { kind: "idle" };
  return stored.key === requestKey ? stored.state : { kind: "loading" };
}

/** Clone a saved record and apply only the JSON-pointer fields returned by localization. */
export function withLocalizedFields<T>(source: T, fields: Record<string, string>): T {
  const copy = structuredClone(source);
  for (const [pointer, value] of Object.entries(fields)) {
    if (!pointer.startsWith("/")) continue;
    const segments = pointer.slice(1).split("/").map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
    let current: unknown = copy;
    for (const segment of segments.slice(0, -1)) {
      if (typeof current !== "object" || current === null || !(segment in current)) { current = null; break; }
      current = (current as Record<string, unknown>)[segment];
    }
    const leaf = segments.at(-1);
    if (leaf && typeof current === "object" && current !== null && leaf in current) {
      (current as Record<string, unknown>)[leaf] = value;
    }
  }
  return copy;
}
