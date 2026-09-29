import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import zhPhrases from "./locales/zh-CN.json";
import enMessages from "./locales/en-US.json";

export type Locale = "zh-CN" | "en-US";
export type PhraseKey = typeof zhPhrases[number];

const STORAGE_KEY = "market-evidence-agent:locale:v1";
const typedEnglishMessages: Record<PhraseKey, string> = enMessages;

type LocaleContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (text: string) => string;
};

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readSavedLocale);

  const setLocale = useCallback((nextLocale: Locale) => {
    setLocaleState(nextLocale);
    try { window.localStorage.setItem(STORAGE_KEY, nextLocale); } catch { /* Locale still applies for this session. */ }
  }, []);

  const t = useCallback((text: string) => {
    return translatePhrase(text, locale);
  }, [locale]);

  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext);
  if (!value) throw new Error("useLocale must be used inside LocaleProvider");
  return value;
}

/** Render an exact static UI phrase through the active locale dictionary. */
export function Tx({ text }: { text: string }) {
  const { t } = useLocale();
  return <>{t(text)}</>;
}

export function formatDateTime(value: string | Date, locale: Locale, timeZone = "UTC") {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat(locale, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    timeZone, timeZoneName: "short",
  }).format(date);
}

export function formatDate(value: string | Date, locale: Locale, timeZone = "UTC") {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric", timeZone }).format(date);
}

export function formatNumber(value: number, locale: Locale, options: Intl.NumberFormatOptions = {}) {
  return new Intl.NumberFormat(locale, options).format(value);
}

export function translatePhrase(text: string, locale: Locale = getLocalePreference()): string {
  if (locale === "zh-CN") return text;
  return typedEnglishMessages[text as PhraseKey] ?? text;
}

function readSavedLocale(): Locale {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "en-US" || saved === "zh-CN") return saved;
  } catch { /* Storage is optional; Chinese remains the default. */ }
  return "zh-CN";
}

/** Reads the same persisted preference for non-component date-format helpers. */
export function getLocalePreference(): Locale {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "en-US" || saved === "zh-CN") return saved;
  } catch { /* Fall back to the default locale. */ }
  return "zh-CN";
}
