import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { LocaleProvider } from "./i18n";
import "./styles.css";
import "./chat.css";
import "./theme.css";
import "./grok.css";
import { applyTheme, storedTheme } from "./theme.mjs";

applyTheme(storedTheme());
window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyTheme(storedTheme()));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LocaleProvider><App /></LocaleProvider>
  </StrictMode>,
);
